import os
import random
import re
from abc import ABC, abstractmethod
from functools import cached_property

import httpx
import orjson
from telegram.constants import MessageLimit

from ..cache import CACHES_TIMER, RedisCache
from ..utils import (
    bili_api_request,
    escape_markdown,
    get_filename,
    BILIBILI_DESKTOP_HEADER,
    logger,
)


class Feed(ABC):
    user: str = ""
    uid: str = ""
    __content: str = ""
    __mediaurls: list = []
    mediacontent: dict = {}
    mediaraws: bool = False
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
                select_url = re.sub(
                    r"&buvid=[^&]+", "&buvid=", select_url
                )  ## 清除buvid参数
                async with self.client.stream(
                    "GET", select_url, headers=header
                ) as response:
                    if response.status_code != 200:
                        continue
                    return int(response.headers.get("Content-Length", 0)), select_url
            except Exception as e:
                logger.error(f"下载链接测试错误: {url}->{referer}")
                logger.exception(e)
        return 0, url

    @staticmethod
    def make_user_markdown(user, uid):
        return (
            f"[@{escape_markdown(user)}](https://space.bilibili.com/{uid})"
            if user and uid
            else str()
        )

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
            else str()
        )

    @staticmethod
    def clean_cn_tag_style(content: str) -> str:
        if not content:
            return ""
        ## Refine cn tag style display: #abc# -> #abc
        return re.sub(r"\\#((?:(?!\\#).)+)\\#", r"\\#\1 ", content)

    @cached_property
    def user_markdown(self):
        return self.make_user_markdown(self.user, self.uid)

    @property
    def content(self):
        return self.shrink_line(self.__content)

    @content.setter
    def content(self, content):
        self.__content = content

    @cached_property
    def content_markdown(self):
        content_markdown = escape_markdown(self.content)
        if not content_markdown.endswith("\n"):
            content_markdown += "\n"
        # if self.extra_markdown:
        #     content_markdown += self.extra_markdown
        return self.shrink_line(content_markdown)

    @cached_property
    def comment(self):
        comment = str()
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

    @cached_property
    def comment_markdown(self):
        comment_markdown = str()
        if isinstance(self.replycontent, dict):
            target = self.replycontent.get("target")
            if target:
                comment_markdown += f"💬\\> {self.make_user_markdown(target['member']['uname'], target['member']['mid'])}:\n{escape_markdown(target['content']['message'])}\n"
            top = self.replycontent.get("top")
            if top:
                for item in top:
                    if item:
                        comment_markdown += f"🔝\\> {self.make_user_markdown(item['member']['uname'], item['member']['mid'])}:\n{escape_markdown(item['content']['message'])}\n"
        return self.shrink_line(comment_markdown)

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
        return (
            [get_filename(i) for i in self.__mediaurls] if self.__mediaurls else list()
        )

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
        return get_filename(self.mediathumb) if self.mediathumb else str()

    @cached_property
    def url(self):
        return self.rawurl

    @property
    def cache_key(self):
        return {}

    def _try_append_within_limit(self, components: list[str], content: str) -> bool:
        if not content:
            return True
        test_content = "".join(components + [content])
        if len(test_content) < MessageLimit.CAPTION_LENGTH:
            components.append(content)
            return True
        return False

    @cached_property
    def caption(self) -> str:
        components = [f"{self.extra_markdown or escape_markdown(self.url)}\n"]
        if self.user:
            if not self._try_append_within_limit(components, f"{self.user_markdown}:"):
                return "".join(components)
        for content_source in [self.content_markdown, self.comment_markdown]:
            if content_source:
                formatted_content = f"\n**>{self.clean_cn_tag_style(content_source).replace('\n', '\n>')}||"
                if formatted_content:
                    if not self._try_append_within_limit(components, formatted_content):
                        return "".join(components)
        return "".join(components)

    async def parse_reply(self, oid, reply_type, seek_comment_id=None):
        logger.info(
            f"处理评论信息: 媒体ID: {oid} 评论类型: {reply_type} 评论ID {seek_comment_id}"
        )
        cache_key = "new_reply:" + ":".join(
            str(x) for x in [oid, reply_type, seek_comment_id] if x is not None
        )
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
                    else:
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
