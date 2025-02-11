import re
from functools import cached_property

import orjson
from telegram.constants import FileSizeLimit

from ..cache import CACHES_TIMER, RedisCache
from ..utils import BILI_API, LOCAL_MODE, ParserException, escape_markdown, logger
from .feed import Feed


class Audio(Feed):
    infocontent: dict = {}
    audio_id: int = 0
    reply_type: int = 14

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/audio/au{self.audio_id}"

    @property
    def cache_key(self):
        return {
            "audio:info": f"audio:info:{self.audio_id}",
            "audio:media": f"audio:media:{self.audio_id}",
        }

    async def handle(self):
        logger.info(f"处理音频信息: 链接: {self.rawurl}")
        match = re.search(r"bilibili\.com\/audio\/au(\d+)", self.rawurl)
        if not match:
            raise ParserException("音频链接错误", self.rawurl)
        self.audio_id = int(match.group(1))
        # 1.获取缓存
        try:
            cache = await RedisCache().get(self.cache_key["audio:info"])
        except Exception as e:
            logger.exception(f"拉取音频缓存错误: {e}")
            cache = None
        # 2.拉取音频
        if cache:
            self.infocontent = orjson.loads(cache)  # type: ignore
            logger.info(f"拉取音频缓存: {self.audio_id}")
        else:
            try:
                r = await self.client.get(
                    BILI_API + "/audio/music-service-c/songs/playing",
                    params={"song_id": self.audio_id},
                )
                self.infocontent = r.json()
            except Exception as e:
                raise ParserException(f"音频获取错误:{self.audio_id}", self.rawurl, e)
            # 3.解析音频
            if not self.infocontent or not self.infocontent.get("data"):
                raise ParserException("音频解析错误", r.url, self.infocontent)
            # 4.缓存音频
            try:
                await RedisCache().set(
                    self.cache_key["audio:info"],
                    orjson.dumps(self.infocontent),
                    ex=CACHES_TIMER["AUDIO"],
                    nx=True,
                )
            except Exception as e:
                logger.exception(f"缓存音频错误: {e}")
        detail = self.infocontent["data"]
        self.user = detail.get("author")
        self.content = detail.get("intro")
        self.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({self.rawurl})"
        self.mediathumb = detail.get("cover_url")
        self.mediatitle = detail.get("title")
        self.mediaduration = detail.get("duration")
        self.uid = detail.get("mid")
        # 1.获取缓存
        try:
            cache = await RedisCache().get(self.cache_key["audio:media"])
        except Exception as e:
            logger.exception(f"拉取音频缓存错误: {e}")
            cache = None
        # 2.拉取音频
        if cache:
            self.mediacontent = orjson.loads(cache)  # type: ignore
            logger.info(f"拉取音频缓存: {self.audio_id}")
        else:
            try:
                r = await self.client.get(
                    BILI_API + "/audio/music-service-c/url",
                    params={
                        "songid": self.audio_id,
                        "mid": self.uid,
                        "privilege": 2,
                        "quality": 3,
                        "platform": "",
                    },
                )
                self.mediacontent = r.json()
            except Exception as e:
                raise ParserException(
                    f"音频媒体获取错误:{self.audio_id}", self.rawurl, e
                )
            # 3.解析音频
            if not self.mediacontent or not self.mediacontent.get("data"):
                raise ParserException("音频媒体解析错误", r.url, self.mediacontent)
            # 4.缓存音频
            try:
                await RedisCache().set(
                    self.cache_key["audio:media"],
                    orjson.dumps(self.mediacontent),
                    ex=CACHES_TIMER["AUDIO"],
                    nx=True,
                )
            except Exception as e:
                logger.exception(f"缓存音频媒体错误: {e}")
        self.mediaurls = self.mediacontent["data"].get("cdns")
        self.mediatype = "audio"
        self.mediaraws = (
            False
            if self.mediacontent["data"].get("size")
            < (
                FileSizeLimit.FILESIZE_DOWNLOAD_LOCAL_MODE
                if LOCAL_MODE
                else FileSizeLimit.FILESIZE_DOWNLOAD
            )
            else True
        )
        self.replycontent = await self.parse_reply(self.audio_id, self.reply_type)
        return self
