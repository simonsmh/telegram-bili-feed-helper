import re

import httpx
import orjson
from telegram.constants import FileSizeLimit

from ..cache import (
    CACHES_TIMER,
    RedisCache,
)
from ..model import Audio
from ..utils import (
    BILI_API,
    LOCAL_MODE,
    ParserException,
    escape_markdown,
    logger,
    retry_catcher,
)
from .reply_parser import parse_reply


@retry_catcher
async def parse_audio(client: httpx.AsyncClient, url: str):
    logger.info(f"处理音频信息: 链接: {url}")
    match = re.search(r"bilibili\.com\/audio\/au(\d+)", url)
    if not match:
        raise ParserException("音频链接错误", url)
    f = Audio(url)
    f.audio_id = int(match.group(1))
    # 1.获取缓存
    try:
        cache = RedisCache().get(f"audio:{f.audio_id}")
    except Exception as e:
        logger.exception(f"拉取音频缓存错误: {e}")
        cache = None
    # 2.拉取音频
    if cache:
        logger.info(f"拉取音频缓存: {f.audio_id}")
        f.infocontent = orjson.loads(cache)  # type: ignore
    else:
        try:
            r = await client.get(
                BILI_API + "/audio/music-service-c/songs/playing",
                params={"song_id": f.audio_id},
            )
            f.infocontent = r.json()
        except Exception as e:
            raise ParserException(f"音频获取错误:{f.audio_id}", url, e)
        # 3.解析音频
        if not f.infocontent or not f.infocontent.get("data"):
            raise ParserException("音频解析错误", r.url, f.infocontent)
        # 4.缓存音频
        try:
            RedisCache().set(
                f"audio:{f.audio_id}",
                orjson.dumps(f.infocontent),
                ex=CACHES_TIMER.get("audio"),
                nx=True,
            )
        except Exception as e:
            logger.exception(f"缓存音频错误: {e}")
    detail = f.infocontent.get("data")
    f.uid = detail.get("mid")
    r = await client.get(
        BILI_API + "/audio/music-service-c/url",
        params={
            "songid": f.audio_id,
            "mid": f.uid,
            "privilege": 2,
            "quality": 3,
            "platform": "",
        },
    )
    f.mediacontent = r.json()
    f.user = detail.get("author")
    f.content = detail.get("intro")
    f.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({f.url})"
    f.mediathumb = detail.get("cover_url")
    f.mediatitle = detail.get("title")
    f.mediaduration = detail.get("duration")
    f.mediaurls = f.mediacontent.get("data").get("cdns")
    f.mediatype = "audio"
    f.mediaraws = (
        False
        if f.mediacontent.get("data").get("size")
        < (
            FileSizeLimit.FILESIZE_DOWNLOAD_LOCAL_MODE
            if LOCAL_MODE
            else FileSizeLimit.FILESIZE_DOWNLOAD
        )
        else True
    )
    f.replycontent = await parse_reply(client, f.audio_id, f.reply_type)
    return f
