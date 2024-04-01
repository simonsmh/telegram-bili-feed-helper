import re
from functools import reduce

import httpx
import orjson

from ..cache import (
    CACHES_TIMER,
    RedisCache,
)
from ..model import Opus
from ..utils import (
    BILI_API,
    ParserException,
    escape_markdown,
    logger,
    retry_catcher,
)
from .reply_parser import parse_reply


def __list_dicts_to_dict(lists: list[dict]):
    return reduce(lambda old, new: old.update(new) or old, lists, {})


def __opus_handle_major(f: Opus, major: dict):
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
        f.has_forward = True
        majorcontent = __list_dicts_to_dict(major[target]["item"]["modules"])
        f.forward_user = majorcontent["module_author"]["user"]["name"]
        f.forward_uid = majorcontent["module_author"]["user"]["mid"]
        if majorcontent.get("module_desc"):
            f.forward_content = __opus_handle_desc_text(majorcontent["module_desc"])
        if not f.mediatype and majorcontent.get("module_dynamic"):
            __opus_handle_major(f, majorcontent["module_dynamic"])
    elif major["type"] == "MDL_DYN_TYPE_DRAW":
        f.mediaurls = [item["src"] for item in major[target]["items"]]
        f.mediatype = "image"
    elif datapath_map.get(major["type"]):
        if major[target].get("cover"):
            f.mediaurls = major[target]["cover"]
            f.mediatype = "image"
        if major[target].get("aid") and major[target].get("title"):
            f.extra_markdown = f"[{escape_markdown(major[target]['title'])}](https://www.bilibili.com/video/av{major[target]['aid']})"


def __opus_handle_desc_text(desc: dict):
    if not desc:
        return ""
    return desc["text"]


@retry_catcher
async def parse_opus(client: httpx.AsyncClient, url: str):
    logger.info(f"处理动态信息: 链接: {url}")
    match = re.search(r"bilibili\.com[\/\w]*\/(\d+)", url)
    if not match:
        raise ParserException("动态链接错误", url)
    f = Opus(url)
    f.dynamic_id = int(match.group(1))
    # 1.获取缓存
    try:
        cache = RedisCache().get(f"opus:dynamic_id:{f.dynamic_id}")
    except Exception as e:
        logger.exception(f"拉取动态缓存错误: {e}")
        cache = None
    # 2.拉取动态
    if cache:
        logger.info(f"拉取动态缓存: {f.dynamic_id}")
        f.detailcontent = orjson.loads(cache)  # type: ignore
    else:
        try:
            r = await client.get(
                BILI_API + "/x/polymer/web-dynamic/desktop/v1/detail",
                params={"id": f.dynamic_id},
            )
            response = r.json()
        except Exception as e:
            raise ParserException(f"动态获取错误:{f.dynamic_id}", url, e)
        # 3.动态解析
        if not response or not response.get("data") or not response["data"].get("item"):
            raise ParserException("动态解析错误", url, response)
        f.detailcontent = response["data"]
        # 4.缓存动态
        try:
            RedisCache().set(
                f"opus:dynamic_id:{f.dynamic_id}",
                orjson.dumps(f.detailcontent),
                ex=CACHES_TIMER.get("opus"),
                nx=True,
            )
        except Exception as e:
            logger.exception(f"缓存动态错误: {e}")
    detailcontent = __list_dicts_to_dict(f.detailcontent["item"]["modules"])
    f.user = detailcontent["module_author"]["user"]["name"]
    f.uid = detailcontent["module_author"]["user"]["mid"]
    if detailcontent.get("module_desc"):
        f.content = __opus_handle_desc_text(detailcontent["module_desc"])
    if detailcontent.get("module_dynamic"):
        __opus_handle_major(f, detailcontent["module_dynamic"])
    f.replycontent = await parse_reply(client, f.rid, f.reply_type)
    return f
