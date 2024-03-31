import os
import re

import httpx
from telegram.constants import FileSizeLimit
from tortoise import timezone
from tortoise.expressions import Q

from ..database import (
    bangumi_cache,
    video_cache,
)
from ..model import Video
from ..utils import (
    BILI_API,
    LOCAL_MODE,
    ParserException,
    escape_markdown,
    headers,
    logger,
    retry_catcher,
)
from .reply_parser import parse_reply


@retry_catcher
async def parse_video(client: httpx.AsyncClient, url: str):
    match = re.search(
        r"(?:bilibili\.com/(?:video|bangumi/play)|b23\.tv|acg\.tv)/(?:(?P<bvid>BV\w{10})|av(?P<aid>\d+)|ep(?P<epid>\d+)|ss(?P<ssid>\d+))",
        url,
    )
    match_fes = re.search(
        r"bilibili\.com/festival/(?P<festivalid>\w+)\?(?:bvid=(?P<bvid>BV\w{10}))", url
    )
    if match_fes:
        bvid = match_fes.group("bvid")
        epid = None
        aid = None
        ssid = None
    elif match:
        bvid = match.group("bvid")
        epid = match.group("epid")
        aid = match.group("aid")
        ssid = match.group("ssid")
    else:
        raise ParserException("视频链接错误", url)
    if epid:
        params = {"ep_id": epid}
    elif bvid:
        params = {"bvid": bvid}
    elif aid:
        params = {"aid": aid}
    elif ssid:
        params = {"season_id": ssid}
    else:
        raise ParserException("视频链接解析错误", url)
    f = Video(url)
    if "ep_id" in params or "season_id" in params:
        query = Q(
            epid=params.get("ep_id"),
            ssid=params.get("season_id"),
            join_type="OR",
        )
        cache = await bangumi_cache.get_or_none(
            query,
            created__gte=timezone.now() - bangumi_cache.timeout,
        )
        if cache:
            logger.info(f"拉取番剧缓存: {cache.created}")
            f.infocontent = cache.content
        else:
            r = await client.get(
                BILI_API + "/pgc/view/web/season",
                params=params,
            )
            f.infocontent = r.json()
        detail = f.infocontent.get("result")
        if not detail:
            # Anime detects non-China IP
            raise ParserException("番剧解析错误", url, f.infocontent)
        f.sid = detail.get("season_id")
        if epid:
            for episode in detail.get("episodes"):
                if str(episode.get("id")) == epid:
                    f.aid = episode.get("aid")
        if not f.aid:
            f.aid = detail.get("episodes")[-1].get("aid")
            epid = detail.get("episodes")[-1].get("id")
        logger.info(f"番剧ID: {epid}")
        if not cache:
            logger.info(f"番剧缓存: {epid}")
            cache = await bangumi_cache.get_or_none(query)
            try:
                if cache:
                    cache.content = f.infocontent
                    await cache.save(update_fields=["content", "created"])
                else:
                    await bangumi_cache(
                        epid=epid, ssid=f.sid, content=f.infocontent
                    ).save()
            except Exception as e:
                logger.exception(f"番剧缓存错误: {e}")
        params = {"aid": f.aid}
    # elif "aid" in params or "bvid" in params:
    query = Q(aid=params.get("aid"), bvid=params.get("bvid"), join_type="OR")
    cache = await video_cache.get_or_none(
        query,
        created__gte=timezone.now() - video_cache.timeout,
    )
    if cache:
        logger.info(f"拉取视频缓存: {cache.created}")
        f.infocontent = cache.content
        detail = f.infocontent.get("data")
    else:
        r = await client.get(
            BILI_API + "/x/web-interface/view",
            params=params,
        )
        # Video detects non-China IP
        f.infocontent = r.json()
        detail = f.infocontent.get("data")
        if not detail:
            raise ParserException("视频解析错误", r.url, f.infocontent)
    if not detail:
        raise ParserException("视频内容获取错误", f.url)
    bvid = detail.get("bvid")
    f.aid = detail.get("aid")
    f.cid = detail.get("cid")
    logger.info(f"视频ID: {f.aid}")
    if not cache:
        logger.info(f"视频缓存: {f.aid}")
        cache = await video_cache.get_or_none(query)
        try:
            if cache:
                cache.content = f.infocontent
                await cache.save(update_fields=["content", "created"])
            else:
                await video_cache(aid=f.aid, bvid=bvid, content=f.infocontent).save()
        except Exception as e:
            logger.exception(f"视频缓存错误: {e}")
    f.user = detail.get("owner").get("name")
    f.uid = detail.get("owner").get("mid")
    f.content = detail.get("tname", "")
    if detail.get("dynamic") or detail.get("desc"):
        f.content += f" - {detail.get('dynamic') or detail.get('desc')}"
    f.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({f.url})"
    f.mediatitle = detail.get("title")
    f.mediaurls = detail.get("pic")
    f.mediatype = "image"
    f.replycontent = await parse_reply(client, f.aid, f.reply_type)

    async def get_video_result(client: httpx.AsyncClient, f: Video, detail, qn: int):
        params = {"avid": f.aid, "cid": f.cid}
        if qn:
            params["qn"] = qn
        r = await client.get(
            BILI_API + "/x/player/playurl",
            params=params,
        )
        video_result = r.json()
        logger.debug(f"视频内容: {video_result}")
        if (
            video_result.get("code") == 0
            and video_result.get("data")
            and video_result.get("data").get("durl")
            and video_result.get("data").get("durl")[0].get("size")
            < (
                int(
                    os.environ.get(
                        "VIDEO_SIZE_LIMIT", FileSizeLimit.FILESIZE_UPLOAD_LOCAL_MODE
                    )
                )
                if LOCAL_MODE
                else FileSizeLimit.FILESIZE_UPLOAD
            )
        ):

            async def test_url_status_code(url):
                header = headers.copy()
                header["Referer"] = f.url
                async with client.stream("GET", url, headers=header) as response:
                    if response.status_code != 200:
                        return False
                    return True

            url = video_result["data"]["durl"][0]["url"]
            result = await test_url_status_code(url)
            if not result and video_result["data"]["durl"][0].get("backup_url", None):
                url = video_result["data"]["durl"][0]["backup_url"]
                result = await test_url_status_code(url)
            if result:
                f.mediacontent = video_result
                f.mediathumb = detail.get("pic")
                f.mediaduration = round(
                    video_result["data"]["durl"][0]["length"] / 1000
                )
                f.mediadimention = detail.get("pages")[0].get("dimension")
                f.mediaurls = url
                f.mediatype = "video"
                f.mediaraws = (
                    False
                    if video_result.get("data").get("durl")[0].get("size")
                    < (
                        FileSizeLimit.FILESIZE_DOWNLOAD_LOCAL_MODE
                        if LOCAL_MODE
                        else FileSizeLimit.FILESIZE_DOWNLOAD
                    )
                    else True
                )
                return True

    for item in [64, 32, 16]:
        if await get_video_result(client, f, detail, item):
            break
    return f
