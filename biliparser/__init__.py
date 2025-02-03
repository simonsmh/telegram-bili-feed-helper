import asyncio
import os
import re

import httpx

from .strategy import Audio, Live, Opus, Read, Video
from .utils import ParserException, headers, logger, retry_catcher


@retry_catcher
async def __feed_parser(client: httpx.AsyncClient, url: str):
    # bypass b23 short link
    if re.search(r"(?:^|/)(?:BV\w{10}|av\d+|ep\d+|ss\d+)", url):
        return await Video(url if "/" in url else f"b23.tv/{url}", client).handle()
    r = await client.get(url)
    url = str(r.url)
    logger.debug(f"URL: {url}")
    # main video
    if re.search(r"video|bangumi/play|festival", url):
        return await Video(url, client).handle()
    # au audio
    elif "read" in url:
        return await Read(url, client).handle()
    # au audio
    elif "audio" in url:
        return await Audio(url, client).handle()
    # live image
    elif "live" in url:
        return await Live(url, client).handle()
    # API link blackboard link user space link
    elif re.search(
        r"^https?:\/\/(?:api|www\.bilibili\.com\/blackboard|space\.bilibili\.com)", url
    ):
        pass
    # dynamic opus
    elif re.search(r"^https?:\/\/[th]\.|dynamic|opus", url):
        return await Opus(url, client).handle()
    raise ParserException("URL错误", url)


async def biliparser(urls) -> list[Video | Read | Audio | Live | Opus]:
    if isinstance(urls, str):
        urls = [urls]
    elif isinstance(urls, tuple):
        urls = list(urls)
    async with httpx.AsyncClient(
        headers=headers,
        http2=True,
        follow_redirects=True,
        proxy=os.environ.get("FILE_PROXY", os.environ.get("HTTP_PROXY")),
    ) as client:
        tasks = list(
            __feed_parser(
                client,
                f"http://{url}"
                if not url.startswith(("http:", "https:", "av", "BV"))
                else url,
            )
            for url in list(set(urls))
        )
        callbacks = await asyncio.gather(*tasks)
    for num, f in enumerate(callbacks):
        if isinstance(f, Exception):
            logger.warning(f"排序: {num}\n异常: {f}\n")
        else:
            logger.debug(
                f"排序: {num}\n"
                f"类型: {type(f)}\n"
                f"链接: {f.url}\n"
                f"用户: {f.user_markdown}\n"
                f"内容: {f.content_markdown}\n"
                f"附加内容: {f.extra_markdown}\n"
                f"评论: {f.comment_markdown}\n"
                f"媒体: {f.mediaurls}\n"
                f"媒体种类: {f.mediatype}\n"
                f"媒体预览: {f.mediathumb}\n"
                f"媒体标题: {f.mediatitle}\n"
                f"媒体文件名: {f.mediafilename}"
            )
    return callbacks
