import re

import httpx
from telegram.constants import FileSizeLimit
from tortoise import timezone
from tortoise.expressions import Q

from ..database import (
    audio_cache,
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
    match = re.search(r"bilibili\.com\/audio\/au(\d+)", url)
    if not match:
        raise ParserException("音频链接错误", url)
    f = Audio(url)
    f.audio_id = int(match.group(1))
    query = Q(audio_id=f.audio_id)
    cache = await audio_cache.get_or_none(
        query,
        created__gte=timezone.now() - audio_cache.timeout,
    )
    if cache:
        logger.info(f"拉取音频缓存: {cache.created}")
        f.infocontent = cache.content
        detail = f.infocontent["data"]
    else:
        r = await client.get(
            BILI_API + "/audio/music-service-c/songs/playing",
            params={"song_id": f.audio_id},
        )
        f.infocontent = r.json()
        detail = f.infocontent.get("data")
        if not detail:
            raise ParserException("音频解析错误", r.url, f.infocontent)
    logger.info(f"音频ID: {f.audio_id}")
    if not cache:
        logger.info(f"音频缓存: {f.audio_id}")
        cache = await audio_cache.get_or_none(query)
        try:
            if cache:
                cache.content = f.infocontent
                await cache.save(update_fields=["content", "created"])
            else:
                await audio_cache(audio_id=f.audio_id, content=f.infocontent).save()
        except Exception as e:
            logger.exception(f"音频缓存错误: {e}")
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
        if detail.get("data").get("size")
        < (
            FileSizeLimit.FILESIZE_DOWNLOAD_LOCAL_MODE
            if LOCAL_MODE
            else FileSizeLimit.FILESIZE_DOWNLOAD
        )
        else True
    )
    f.replycontent = await parse_reply(client, f.audio_id, f.reply_type)
    return f
