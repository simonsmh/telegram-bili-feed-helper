import asyncio
import re
import uuid

from httpx import AsyncClient, HTTPStatusError

from .strategy import Audio, Live, Opus, Read, Video
from .utils import ParserException, BILIBILI_DESKTOP_HEADER, credentialFactory, logger, retry_catcher


@retry_catcher
async def __feed_parser(client: AsyncClient, url: str, extra: dict | None = None):
    # bypass b23 short link
    if re.search(r"(?:^|/)(?:BV\w{10}|av\d+|ep\d+|ss\d+)", url):
        return await Video(url if "/" in url else f"b23.tv/{url}", client).handle(extra)
    elif re.search(r"(?:www|t|h|m)\.bilibili\.com\/(?:[^\/?]+\/)*?(?:\d+)(?:[\/?].*)?", url):
        return await Opus(url, client).handle()
    elif re.search(r"live\.bilibili\.com[\/\w]*\/(\d+)", url):
        return await Live(url, client).handle()
    elif re.search(r"bilibili\.com\/audio\/au(\d+)", url):
        return await Audio(url, client).handle()
    elif re.search(r"bilibili\.com\/read\/(?:cv|mobile\/|mobile\?id=)(\d+)", url):
        return await Read(url, client).handle()
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except HTTPStatusError as e:
        logger.warning(f"URL请求失败 [{e.response.status_code}]: {e.request.url}")
        raise ParserException("URL请求失败", url)
    except Exception as e:
        logger.error(f"URL请求异常 [{url}]: {str(e)}")
        raise ParserException("URL请求异常", url)
    url = str(resp.url)
    logger.debug(f"URL: {url}")
    # main video
    if re.search(r"video|bangumi/play|festival", url):
        return await Video(url, client).handle(extra)
    # dynamic opus
    elif re.search(r"(?:www|t|h|m)\.bilibili\.com\/(?:[^\/?]+\/)*?(?:\d+)(?:[\/?].*)?", url):
        return await Opus(url, client).handle()
    # live image
    elif "live" in url:
        return await Live(url, client).handle()
    # au audio
    elif "audio" in url:
        return await Audio(url, client).handle()
    # cv read
    elif "read" in url:
        return await Read(url, client).handle()
    # API link blackboard link user space link
    elif re.search(
        r"^https?:\/\/(?:api|www\.bilibili\.com\/blackboard|space\.bilibili\.com)", url
    ):
        pass
    raise ParserException("URL无可用策略", url)


async def biliparser(
    urls, extra: dict | None = None
) -> list[Video | Read | Audio | Live | Opus]:
    if isinstance(urls, str):
        urls = [urls]
    elif isinstance(urls, tuple):
        urls = list(urls)
    async with AsyncClient(
        headers=BILIBILI_DESKTOP_HEADER,
        http2=True,
        follow_redirects=True,
        cookies={"buvid3": f'{uuid.uuid4()}infoc'},
    ) as client:
        tasks = list(
            __feed_parser(
                client,
                f"http://{url}"
                if not url.startswith(("http:", "https:", "av", "BV"))
                else url,
                extra,
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
