import re

import httpx
from tortoise import timezone
from tortoise.expressions import Q

from ..database import (
    live_cache,
)
from ..model import Live
from ..utils import (
    ParserException,
    escape_markdown,
    logger,
    retry_catcher,
)


@retry_catcher
async def parse_live(client: httpx.AsyncClient, url: str):
    match = re.search(r"live\.bilibili\.com[\/\w]*\/(\d+)", url)
    if not match:
        raise ParserException("直播链接错误", url)
    f = Live(url)
    f.room_id = int(match.group(1))
    query = Q(room_id=f.room_id)
    cache = await live_cache.get_or_none(
        query,
        created__gte=timezone.now() - live_cache.timeout,
    )
    if cache:
        logger.info(f"拉取直播缓存: {cache.created}")
        f.rawcontent = cache.content
        detail = f.rawcontent.get("data")
    else:
        r = await client.get(
            "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
            params={"room_id": f.room_id},
        )
        f.rawcontent = r.json()
        detail = f.rawcontent.get("data")
        if not detail:
            raise ParserException("直播解析错误", r.url, f.rawcontent)
    logger.info(f"直播ID: {f.room_id}")
    if not cache:
        logger.info(f"直播缓存: {f.room_id}")
        cache = await live_cache.get_or_none(query)
        try:
            if cache:
                cache.content = f.rawcontent
                await cache.save(update_fields=["content", "created"])
            else:
                await live_cache(room_id=f.room_id, content=f.rawcontent).save()
        except Exception as e:
            logger.exception(f"直播缓存错误: {e}")
    if not detail:
        raise ParserException("直播内容获取错误", f.url)
    f.user = detail["anchor_info"]["base_info"]["uname"]
    roominfo = detail.get("room_info")
    f.uid = roominfo.get("uid")
    f.content = f"{roominfo.get('title')} - {roominfo.get('area_name')} - {roominfo.get('parent_area_name')}"
    f.extra_markdown = f"[{escape_markdown(f.user)}的直播间]({f.url})"
    f.mediaurls = roominfo.get("keyframe")
    f.mediatype = "image"
    return f
