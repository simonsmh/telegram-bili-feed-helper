import re
from functools import cached_property, lru_cache, reduce

import orjson

from ..cache import CACHES_TIMER, RedisCache
from ..utils import BILI_API, ParserException, escape_markdown, logger
from .feed import Feed


class Opus(Feed):
    detailcontent: dict = {}
    dynamic_id: int = 0
    user: str = ""
    __content: str = ""
    forward_user: str = ""
    forward_uid: int = 0
    forward_content: str = ""
    has_forward: bool = False

    @cached_property
    def reply_type(self):
        if self.rtype == 2:
            return 11
        if self.rtype == 16:
            return 5
        if self.rtype == 64:
            return 12
        if self.rtype == 256:
            return 14
        if self.rtype in [8, 512, *range(4000, 4200)]:
            return 1
        if self.rtype in [1, 4, *range(4200, 4300), *range(2048, 2100)]:
            return 17

    @cached_property
    def rtype(self):
        return int(self.detailcontent["item"]["basic"]["rtype"])

    @cached_property
    def rid(self):
        return int(self.detailcontent["item"]["basic"]["rid_str"])

    @property
    @lru_cache(maxsize=1)
    def content(self):
        content = self.__content
        if self.has_forward:
            if self.forward_user:
                content += f"//@{self.forward_user}:\n"
            content += self.forward_content
        return self.shrink_line(content)

    @content.setter
    def content(self, content):
        self.__content = content

    @cached_property
    def content_markdown(self):
        content_markdown = escape_markdown(self.__content)
        if self.has_forward:
            if self.uid:
                content_markdown += f"//{self.make_user_markdown(self.forward_user, self.forward_uid)}:\n"
            elif self.user:
                content_markdown += f"//@{escape_markdown(self.forward_user)}:\n"
            content_markdown += escape_markdown(self.forward_content)
        if not content_markdown.endswith("\n"):
            content_markdown += "\n"
        return self.shrink_line(content_markdown)

    @cached_property
    def url(self):
        return f"https://t.bilibili.com/{self.dynamic_id}"

    @property
    def cache_key(self):
        return {"opus:dynamic_id": f"opus:dynamic_id:{self.dynamic_id}"}

    def __list_dicts_to_dict(self, lists: list[dict]):
        return reduce(lambda old, new: old.update(new) or old, lists, {})

    def __opus_handle_major(self, major):
        datapath_map = {
            "MDL_DYN_TYPE_ARCHIVE": "dyn_archive",
            "MDL_DYN_TYPE_PGC": "dyn_pgc",
            "MDL_DYN_TYPE_ARTICLE": "dyn_article",
            "MDL_DYN_TYPE_MUSIC": "dyn_music",
            "MDL_DYN_TYPE_COMMON": "dyn_common",
            "MDL_DYN_TYPE_LIVE": "dyn_live",
            "MDL_DYN_TYPE_UGC_SEASON": "dyn_ugc_season",
            "MDL_DYN_TYPE_DRAW": "dyn_draw",
            "MDL_DYN_TYPE_OPUS": "dyn_opus",
            "MDL_DYN_TYPE_FORWARD": "dyn_forward",
        }
        if not major:
            return
        target = datapath_map.get(major["type"])
        if major["type"] == "MDL_DYN_TYPE_FORWARD":
            self.has_forward = True
            majorcontent = self.__list_dicts_to_dict(major[target]["item"]["modules"])
            self.forward_user = majorcontent["module_author"]["user"]["name"]
            self.forward_uid = majorcontent["module_author"]["user"]["mid"]
            if majorcontent.get("module_desc"):
                self.forward_content = self.__opus_handle_desc_text(
                    majorcontent["module_desc"]
                )
            if not self.mediatype and majorcontent.get("module_dynamic"):
                self.__opus_handle_major(majorcontent["module_dynamic"])
        elif major["type"] == "MDL_DYN_TYPE_DRAW":
            self.mediaurls = [item["src"] for item in major[target]["items"]]
            self.mediatype = "image"
        elif datapath_map.get(major["type"]):
            if major[target].get("cover"):
                self.mediaurls = major[target]["cover"]
                self.mediatype = "image"
            if major[target].get("aid") and major[target].get("title"):
                self.extra_markdown = f"[{escape_markdown(major[target]['title'])}](https://www.bilibili.com/video/av{major[target]['aid']})"

    def __opus_handle_desc_text(self, desc: dict):
        if not desc:
            return ""
        return desc["text"]

    async def handle(self):
        logger.info(f"处理动态信息: 链接: {self.rawurl}")
        match = re.search(r"bilibili\.com[\/\w]*\/(\d+)", self.rawurl)
        if not match:
            raise ParserException("动态链接错误", self.rawurl)
        self.dynamic_id = int(match.group(1))
        # 1.获取缓存
        try:
            cache = await RedisCache().get(self.cache_key["opus:dynamic_id"])
        except Exception as e:
            logger.exception(f"拉取动态缓存错误: {e}")
            cache = None
        # 2.拉取动态
        if cache:
            self.detailcontent = orjson.loads(cache)  # type: ignore
            logger.info(f"拉取动态缓存: {self.dynamic_id}")
        else:
            try:
                r = await self.client.get(
                    BILI_API + "/x/polymer/web-dynamic/desktop/v1/detail",
                    params={"id": self.dynamic_id},
                )
                response = r.json()
            except Exception as e:
                raise ParserException(f"动态获取错误:{self.dynamic_id}", self.rawurl, e)
            # 3.动态解析
            if (
                not response
                or not response.get("data")
                or not response["data"].get("item")
            ):
                raise ParserException("动态解析错误", self.rawurl, response)
            self.detailcontent = response["data"]
            # 4.缓存动态
            try:
                await RedisCache().set(
                    self.cache_key["opus:dynamic_id"],
                    orjson.dumps(self.detailcontent),
                    ex=CACHES_TIMER["OPUS"],
                    nx=True,
                )
            except Exception as e:
                logger.exception(f"缓存动态错误: {e}")
        detailcontent = self.__list_dicts_to_dict(self.detailcontent["item"]["modules"])
        self.user = detailcontent["module_author"]["user"]["name"]
        self.uid = detailcontent["module_author"]["user"]["mid"]
        if detailcontent.get("module_desc"):
            self.content = self.__opus_handle_desc_text(detailcontent["module_desc"])
        if detailcontent.get("module_dynamic"):
            self.__opus_handle_major(detailcontent["module_dynamic"])
        self.extra_markdown = f"[{escape_markdown(self.user)}的动态]({self.url})"
        self.replycontent = await self.parse_reply(self.rid, self.reply_type)
        return self
