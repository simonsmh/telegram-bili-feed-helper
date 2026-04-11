import os
from urllib.parse import urlencode, urljoin

from httpx import AsyncClient, HTTPStatusError, Response

from ...utils import get_filename, logger

BILIBILI_DESKTOP_HEADER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) bilibili_pc/1.16.5 Chrome/108.0.5359.215 Electron/22.3.27 Safari/537.36 build/1001016005"
}
BILIBILI_DESKTOP_BUILD = "11605"

CACHE_TIMER_DEFAULTS = {
    "CREDENTIAL": 60 * 60 * 24 * 7 * 4,
    "LOCK": 60 * 60,
    "AUDIO": 60 * 60,
    "BANGUMI": 60 * 60,
    "OPUS": 60 * 60,
    "LIVE": 60 * 5,
    "READ": 60 * 60,
    "REPLY": 60 * 60,
    "VIDEO": 60 * 60,
}

CACHES_TIMER = {
    k: int(os.environ.get(f"{k}_CACHE_TIME", v))
    for k, v in CACHE_TIMER_DEFAULTS.items()
}


class ParserException(Exception):
    def __init__(self, msg, url, res=None):
        self.msg = msg
        self.url = url
        self.res = str(res) if res else None

    def __str__(self):
        return f"{self.msg}: {self.url} ->\n{self.res}"


def retry_catcher(func):
    async def inner_function(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ParserException as err:
            logger.error(err)
            return err
        except BaseException as err:
            logger.exception(err)
            return err
    return inner_function


def referer_url(url: str, referer: str) -> str:
    if not referer:
        return url
    params = {"url": url, "referer": referer}
    return f"https://referer.simonsmh.workers.dev/?{urlencode(params)}#{get_filename(url)}"


async def bili_api_request(client: AsyncClient, path: str, **kwargs) -> Response:
    url_prefixes = ["https://api.bilibili.com"]
    bili_apis = os.environ.get("BILI_API")
    if bili_apis:
        url_prefixes = [*bili_apis.split(","), *url_prefixes]
    for url_prefix in url_prefixes:
        try:
            url = urljoin(url_prefix.rstrip("/") + "/", path.lstrip("/"))
            resp = await client.get(url, **kwargs)
            resp.raise_for_status()
            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == 0:
                    logger.debug(
                        f"biliAPI请求成功 [{resp.status_code}]: {url} -> {resp.text}"
                    )
                    return resp
        except HTTPStatusError as e:
            logger.warning(
                f"biliAPI请求失败 [{e.response.status_code}]: {e.request.url}"
            )
        except Exception as e:
            logger.error(f"biliAPI请求异常 [{url_prefix}]: {str(e)}")
            continue
    raise ParserException("biliAPI请求错误", path)
