import html
import math
import os
import re
import sys
from io import BytesIO
from urllib.parse import urlencode, urljoin

from httpx import AsyncClient, HTTPStatusError, Response
from loguru import logger
from PIL import Image

from .cache import LOCAL_FILE_PATH
from .credentialFactory import CredentialFactory

logger.remove()
logger.add(sys.stdout, backtrace=True, diagnose=True)
if os.environ.get("LOG_TO_FILE"):
    logger.add("bili_feed.log", backtrace=True, diagnose=True, rotation="1 MB")

BILIBILI_DESKTOP_HEADER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) bilibili_pc/1.16.5 Chrome/108.0.5359.215 Electron/22.3.27 Safari/537.36 build/1001016005"
}
BILIBILI_DESKTOP_BUILD = "11605"
LOCAL_MODE = os.environ.get("LOCAL_MODE", False)
LOCAL_MEDIA_FILE_PATH = LOCAL_FILE_PATH / ".tmp"
credentialFactory = CredentialFactory()


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


def compress(inpil, size=1280, fix_ratio=False) -> BytesIO:
    pil = Image.open(inpil)
    if fix_ratio:
        w, h = pil.size
        if w / h > 20:
            logger.info(f"{w}, {h}")
            new_h = math.ceil(w / 20)
            padded = Image.new("RGBA", (w, new_h))
            padded.paste(pil, (0, int((new_h - h) / 2)))
            pil = padded
        elif h / w > 20:
            logger.info(f"{w}, {h}")
            new_w = math.ceil(h / 20)
            padded = Image.new("RGBA", (new_w, h))
            padded.paste(pil, (int((new_w - h) / 2), 0))
            pil = padded
    if size > 0:
        pil.thumbnail((size, size), Image.Resampling.LANCZOS)
    outpil = BytesIO()
    pil.save(outpil, "PNG", optimize=True)
    return outpil


def escape_markdown(text: str):
    if not text:
        return ""
    return re.sub(r"([_*\[\]()~`>\#\+\-=|{}\.!\\])", r"\\\1", html.unescape(text))


def get_filename(url) -> str:
    target = re.search(r"\/([^\/]*\.\w{3,4})(?:$|\?)", url)
    if target:
        return target.group(1)
    return url


def referer_url(url: str, referer: str):
    if not referer:
        return url
    params = {"url": url, "referer": referer}
    return (
        f"https://referer.simonsmh.workers.dev/?{urlencode(params)}#{get_filename(url)}"
    )


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
