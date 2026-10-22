"""Validate destinations before issuing requests, including every redirect hop."""

import re
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx

from .api import ParserException

LINK_HOSTS = ("bilibili.com", "b23.tv", "acg.tv", "bili2233.cn", "bilibilibb.com")
IMAGE_HOSTS = ("hdslb.com",)
BARE_ID = re.compile(r"(?:BV\w{10}|av\d+|ep\d+|ss\d+)", re.IGNORECASE)


def allowed_url(url: str, hosts: tuple[str, ...]) -> bool:
    # Reject ambiguous forms before either urllib or HTTPX can normalize them.
    if not url or "\\" in url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme.lower() in ("http", "https")
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 80, 443)
            and any(parsed.hostname == h or (parsed.hostname or "").endswith("." + h) for h in hosts)
        )
    except ValueError:
        return False


def normalize_link(url: str) -> str:
    if BARE_ID.fullmatch(url):
        return url
    candidate = url if "://" in url else "https://" + url
    if not allowed_url(candidate, LINK_HOSTS):
        raise ParserException("不支持的链接主机", url)
    return candidate


def is_bilibili_link(url: str) -> bool:
    try:
        normalize_link(url)
    except ParserException:
        return False
    return True


@asynccontextmanager
async def checked_stream(client, method, url, hosts, **kwargs):
    """Never let HTTPX follow a redirect before checking its destination."""
    for _ in range(21):
        if not allowed_url(str(url), hosts):
            raise ParserException("不支持的请求主机", str(url))
        async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
            if not response.is_redirect or "location" not in response.headers:
                yield response
                return
            url = response.url.join(response.headers["location"])
    raise httpx.TooManyRedirects("Too many redirects")


async def checked_request(client, method, url, hosts):
    async with checked_stream(client, method, url, hosts) as response:
        await response.aread()
        return response
