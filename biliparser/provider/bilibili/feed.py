import os
import random
import re
from abc import ABC, abstractmethod
from functools import cached_property

import httpx
import orjson

from ...storage.cache import RedisCache
from ...utils import (
    escape_markdown,
    get_filename,
    logger,
)
from .api import (
    BILIBILI_DESKTOP_HEADER,
    CACHES_TIMER,
    bili_api_request,
)


class Feed(ABC):
    user: str = ""
    uid: str = ""
    __content: str = ""
    __mediaurls: list = []
    mediacontent: dict = {}
    mediaraws: bool = False
    mediamerge: bool = False  # 多轨流需要合并（如 DASH 视频轨+音频轨）
    mediatype: str = ""
    __mediathumb: str = ""
    mediaduration: int = 0
    mediadimention: dict = {"width": 0, "height": 0, "rotate": 0}
    mediatitle: str = ""
    mediafilesize: int = 0
    extra_markdown: str = ""
    replycontent: dict = {}

    def __init__(self, rawurl: str, client: httpx.AsyncClient):
        self.rawurl = rawurl
        self.client = client

    async def test_url_status_code(self, url, referer):
        header = BILIBILI_DESKTOP_HEADER.copy()
        header["Referer"] = referer
        select_urls = [url]
        upos_domain = os.environ.get("UPOS_DOMAIN")
        if upos_domain:
            domains = upos_domain.split(",")
            if domains:
                random.shuffle(domains)
                domain = domains.pop()
                if domain:
                    test_url = re.sub(r"https?://[^/]+/", f"https://{domain}/", url)
                    select_urls.insert(0, test_url)
        for select_url in select_urls:
            try:
                select_url = re.sub(r"&buvid=[^&]+", "&buvid=", select_url)  ## 清除buvid参数
                async with self.client.stream("GET", select_url, headers=header) as response:
                    if response.status_code != 200:
                        continue
                    return int(response.headers.get("Content-Length", 0)), select_url
            except Exception as e:
                logger.error(f"下载链接测试错误: {url}->{referer}")
                logger.exception(e)
        return 0, url

    @staticmethod
    def make_user_markdown(user, uid):
        return f"[@{escape_markdown(user)}](https://space.bilibili.com/{uid})" if user and uid else ""

    @staticmethod
    def shrink_line(text: str):
        return (
            text.strip()
            .replace(
                r"\r\n",
                r"\n",
            )
            .replace(r"\n*\n", r"\n")
            if text
            else ""
        )

    @staticmethod
    def clean_cn_tag_style(content: str) -> str:
        if not content:
            return ""
        ## Refine cn tag style display: #abc# -> #abc
        return re.sub(r"\\#((?:(?!\\#).)+)\\#", r"\\#\1 ", content)

    @staticmethod
    def wan(num):
        return f"{num / 10000:.2f}万" if num >= 10000 else num

    @property
    def content(self):
        return self.shrink_line(self.__content)

    @content.setter
    def content(self, content):
        self.__content = content

    @cached_property
    def comment(self):
        comment = ""
        if isinstance(self.replycontent, dict):
            target = self.replycontent.get("target")
            if target:
                comment += f"💬> @{target['member']['uname']}:\n{target['content']['message']}\n"
            top = self.replycontent.get("top")
            if top:
                for item in top.values():
                    if item:
                        comment += f"🔝> @{item['member']['uname']}:\n{item['content']['message']}\n"
        return self.shrink_line(comment)

    @property
    def mediaurls(self):
        return self.__mediaurls

    @mediaurls.setter
    def mediaurls(self, content):
        if isinstance(content, list):
            self.__mediaurls = content
        else:
            self.__mediaurls = [content]
        if hasattr(self, "mediafilename"):
            delattr(self, "mediafilename")

    @cached_property
    def mediafilename(self):
        return [get_filename(i) for i in self.__mediaurls] if self.__mediaurls else list()

    @property
    def mediathumb(self):
        return self.__mediathumb

    @mediathumb.setter
    def mediathumb(self, content):
        self.__mediathumb = content
        if hasattr(self, "mediathumbfilename"):
            delattr(self, "mediathumbfilename")

    @cached_property
    def mediathumbfilename(self):
        return get_filename(self.mediathumb) if self.mediathumb else ""

    @cached_property
    def url(self):
        return self.rawurl

    @property
    def cache_key(self):
        return {}

    async def parse_reply(self, oid, reply_type, seek_comment_id=None):
        logger.info(f"处理评论信息: 媒体ID: {oid} 评论类型: {reply_type} 评论ID {seek_comment_id}")
        cache_key = "new_reply:" + ":".join(str(x) for x in [oid, reply_type, seek_comment_id] if x is not None)
        # 1.获取缓存
        try:
            cache = await RedisCache().get(cache_key)
        except Exception as e:
            logger.exception(f"拉取评论缓存错误: {e}")
            cache = None
        # 2.拉取评论
        if cache:
            reply = orjson.loads(cache)  # type: ignore
            logger.info(f"拉取评论缓存: {oid}")
        else:
            try:
                params = {"oid": oid, "type": reply_type}
                if seek_comment_id is not None:
                    params["seek_rpid"] = seek_comment_id
                r = await bili_api_request(
                    self.client,
                    "/x/v2/reply/main",
                    params=params,
                    headers={"Referer": "https://www.bilibili.com/client"},
                )
                response = r.json()
            except Exception as e:
                logger.exception(f"评论获取错误: {cache_key} {e}")
                return {}
            # 3.评论解析
            if not response or not response.get("data"):
                logger.warning(f"评论解析错误: {cache_key} {response}")
                return {}
            data = response["data"]
            # find target comment
            target = None
            if seek_comment_id is not None and "replies" in data:
                for r in data["replies"]:
                    if str(r["rpid"]) == str(seek_comment_id):
                        target = r
                        break
                    for sr in r["replies"]:
                        if str(sr["rpid"]) == str(seek_comment_id):
                            target = sr
                            break
            reply = {"top": data.get("top_replies"), "target": target}
            # 4.缓存评论
            try:
                await RedisCache().set(
                    cache_key,
                    orjson.dumps(reply),
                    ex=CACHES_TIMER["REPLY"],
                    nx=True,
                )
            except Exception as e:
                logger.exception(f"缓存评论错误: {e}")
        return reply

    @abstractmethod
    async def handle(self):
        return self
