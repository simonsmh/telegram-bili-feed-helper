import asyncio
import re
import uuid

import httpx

from ...model import (
    Author,
    Comment,
    MediaConstraints,
    MediaInfo,
    ParsedContent,
    PreparedMedia,
)
from ..  import Provider
from .api import (
    BILIBILI_DESKTOP_HEADER,
    BILIBILI_DESKTOP_BUILD,
    CACHES_TIMER,
    ParserException,
    retry_catcher,
    referer_url,
    bili_api_request,
)
from .credential import CredentialFactory, credentialFactory
from .feed import Feed
from .audio import Audio
from .live import Live
from .opus import Opus
from .read import Read
from .video import Video

# Regex that matches any Bilibili URL we can handle
_BILIBILI_RE = re.compile(
    r"(?:"
    r"bilibili\.com"          # any bilibili.com subdomain
    r"|b23\.tv"               # short links
    r"|(?:BV\w{10})"          # bare BV id
    r"|(?:av\d+)"             # bare av id
    r")",
    re.IGNORECASE,
)


def _feed_to_parsed_content(f: Feed) -> ParsedContent:
    author = Author(name=f.user, uid=str(f.uid))

    media: MediaInfo | None = None
    if f.mediaurls:
        media = MediaInfo(
            urls=f.mediaurls,
            type=f.mediatype,
            thumbnail=f.mediathumb,
            duration=f.mediaduration,
            dimension=f.mediadimention,
            title=f.mediatitle,
            filenames=f.mediafilename,
            thumbnail_filename=f.mediathumbfilename,
            need_download=f.mediaraws,
        )

    comments: list[Comment] = []
    if isinstance(f.replycontent, dict):
        target = f.replycontent.get("target")
        if target:
            comments.append(
                Comment(
                    author=Author(name=target["member"]["uname"]),
                    text=target["content"]["message"],
                    is_target=True,
                )
            )
        top = f.replycontent.get("top")
        if top:
            for item in (top.values() if isinstance(top, dict) else top):
                if item:
                    comments.append(
                        Comment(
                            author=Author(name=item["member"]["uname"]),
                            text=item["content"]["message"],
                            is_top=True,
                        )
                    )

    return ParsedContent(
        url=f.url,
        author=author,
        content=f.content,
        extra_markdown=f.extra_markdown,
        media=media,
        comments=comments,
        cache_keys=f.cache_key,
    )


@retry_catcher
async def _route(client: httpx.AsyncClient, url: str, extra: dict | None = None) -> Feed:
    # bare BV/av/ep/ss ids or short paths
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

    # follow redirects then re-classify
    try:
        resp = await client.head(url)
    except httpx.HTTPStatusError as e:
        raise ParserException("URL请求失败", url)
    except Exception:
        raise ParserException("URL请求异常", url)

    url = str(resp.url)
    if re.search(r"video|bangumi/play|festival", url):
        return await Video(url, client).handle(extra)
    elif re.search(r"(?:www|t|h|m)\.bilibili\.com\/(?:[^\/?]+\/)*?(?:\d+)(?:[\/?].*)?", url):
        return await Opus(url, client).handle()
    elif "live" in url:
        return await Live(url, client).handle()
    elif "audio" in url:
        return await Audio(url, client).handle()
    elif "read" in url:
        return await Read(url, client).handle()
    elif re.search(r"^https?:\/\/(?:api|www\.bilibili\.com\/blackboard|space\.bilibili\.com)", url):
        pass
    raise ParserException("URL无可用策略", url)


class BilibiliProvider(Provider):
    def can_handle(self, url: str) -> bool:
        return bool(_BILIBILI_RE.search(url))

    async def parse(
        self,
        urls: list[str],
        constraints: MediaConstraints,
        extra: dict | None = None,
    ) -> list[ParsedContent]:
        async with httpx.AsyncClient(
            headers=BILIBILI_DESKTOP_HEADER,
            http2=True,
            follow_redirects=True,
            cookies={"buvid3": f"{uuid.uuid4()}infoc"},
        ) as client:
            tasks = [
                _route(
                    client,
                    f"http://{url}" if not url.startswith(("http:", "https:", "av", "BV")) else url,
                    extra,
                )
                for url in list(set(urls))
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        parsed: list[ParsedContent] = []
        for r in results:
            if isinstance(r, Exception):
                raise r
            parsed.append(_feed_to_parsed_content(r))
        return parsed

    async def prepare_media(
        self,
        content: ParsedContent,
        constraints: MediaConstraints,
    ) -> PreparedMedia:
        # Will be implemented in a later task
        return PreparedMedia(files=[], thumbnail=None)


__all__ = [
    # provider
    "BilibiliProvider",
    # api helpers (kept for backward compat)
    "BILIBILI_DESKTOP_HEADER",
    "BILIBILI_DESKTOP_BUILD",
    "CACHES_TIMER",
    "ParserException",
    "retry_catcher",
    "referer_url",
    "bili_api_request",
    # credential
    "CredentialFactory",
    "credentialFactory",
    # feed + strategies
    "Feed",
    "Audio",
    "Live",
    "Opus",
    "Read",
    "Video",
]
