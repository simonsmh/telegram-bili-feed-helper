import re
from functools import cached_property

import orjson

from ..cache import CACHES_TIMER, RedisCache
from ..utils import ParserException, escape_markdown, logger
from .feed import Feed


class Live(Feed):
    rawcontent: dict = {}
    room_id: int = 0

    @cached_property
    def url(self):
        return f"https://live.bilibili.com/{self.room_id}"

    @property
    def cache_key(self):
        return {"live": f"live:{self.room_id}"}
    async def handle(self):
        logger.info(f"处理直播信息: 链接: {self.rawurl}")
        match = re.search(r"live\.bilibili\.com[\/\w]*\/(\d+)", self.rawurl)
        if not match:
            raise ParserException("直播链接错误", self.rawurl)
        self.room_id = int(match.group(1))
        # 1.获取缓存
        try:
            cache = await RedisCache().get(f"live:{self.room_id}")
        except Exception as e:
            logger.exception(f"拉取直播缓存错误: {e}")
            cache = None
        # 2.拉取直播
        if cache:
            self.rawcontent = orjson.loads(cache)  # type: ignore
            logger.info(f"拉取直播缓存: {self.room_id}")
        else:
            try:
                r = await self.client.get(
                    "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
                    params={"room_id": self.room_id},
                )
                self.rawcontent = r.json()
            except Exception as e:
                raise ParserException(f"直播获取错误:{self.room_id}", self.rawurl, e)
            # 3.解析直播
            if not self.rawcontent or not self.rawcontent.get("data"):
                raise ParserException("直播解析错误", r.url, self.rawcontent)
            # 4.缓存直播
            try:
                await RedisCache().set(
                    self.cache_key["live"],
                    orjson.dumps(self.rawcontent),
                    ex=CACHES_TIMER["LIVE"],
                    nx=True,
                )
            except Exception as e:
                logger.exception(f"缓存直播错误: {e}")
        detail = self.rawcontent["data"]
        self.user = detail["anchor_info"]["base_info"]["uname"]
        roominfo = detail.get("room_info")
        self.uid = roominfo.get("uid")
        self.content = f"{roominfo.get('title')} - {roominfo.get('area_name')} - {roominfo.get('parent_area_name')}"
        self.extra_markdown = f"[{escape_markdown(self.user)}的直播间]({self.url})"
        self.mediaurls = roominfo.get("keyframe") or roominfo.get("cover")
        self.mediatype = "image"
        return self
