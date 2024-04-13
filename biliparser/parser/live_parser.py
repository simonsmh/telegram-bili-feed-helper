import re

import httpx
import orjson

from ..cache import (
    CACHES_TIMER,
    RedisCache,
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
    logger.info(f"处理直播信息: 链接: {url}")
    match = re.search(r"live\.bilibili\.com[\/\w]*\/(\d+)", url)
    if not match:
        raise ParserException("直播链接错误", url)
    f = Live(url)
    f.room_id = int(match.group(1))
    # 1.获取缓存
    try:
        cache = RedisCache().get(f"live:{f.room_id}")
    except Exception as e:
        logger.exception(f"拉取直播缓存错误: {e}")
        cache = None
    # 2.拉取直播
    if cache:
        logger.info(f"拉取直播缓存: {f.room_id}")
        f.rawcontent = orjson.loads(cache)  # type: ignore
    else:
        try:
            r = await client.get(
                "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
                params={"room_id": f.room_id},
            )
            f.rawcontent = r.json()
        except Exception as e:
            raise ParserException(f"直播获取错误:{f.room_id}", url, e)
        # 3.解析直播
        if not f.rawcontent or not f.rawcontent.get("data"):
            raise ParserException("直播解析错误", r.url, f.rawcontent)
        # 4.缓存直播
        try:
            RedisCache().set(
                f"live:{f.room_id}",
                orjson.dumps(f.rawcontent),
                ex=CACHES_TIMER.get("live"),
                nx=True,
            )
        except Exception as e:
            logger.exception(f"缓存直播错误: {e}")
    detail = f.rawcontent.get("data")
    f.user = detail["anchor_info"]["base_info"]["uname"]
    roominfo = detail.get("room_info")
    f.uid = roominfo.get("uid")
    f.content = f"{roominfo.get('title')} - {roominfo.get('area_name')} - {roominfo.get('parent_area_name')}"
    f.extra_markdown = f"[{escape_markdown(f.user)}的直播间]({f.url})"
    f.mediaurls = roominfo.get("keyframe") or roominfo.get("cover")
    f.mediatype = "image"
    return f
