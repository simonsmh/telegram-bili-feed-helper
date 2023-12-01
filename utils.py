import html
import os
import re
import sys
from io import BytesIO

from loguru import logger

try:
    from PIL import Image

    def compress(inpil, size=1280) -> BytesIO:
        pil = Image.open(inpil)
        pil.thumbnail((size, size), Image.LANCZOS)
        outpil = BytesIO()
        pil.save(outpil, "PNG", optimize=True)
        return outpil

except ImportError:
    from wand.image import Image

    def compress(inpil, size=1280) -> BytesIO:
        pil = Image(blob=inpil)
        pil.thumbnail(size, size)
        outpil = BytesIO()
        pil.save(outpil)
        return outpil


logger.remove()
logger.add(sys.stdout, backtrace=True, diagnose=True)
logger.add("bili_feed.log", backtrace=True, diagnose=True, rotation="1 MB")


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) bilibili_pc/1.12.1 Chrome/106.0.5249.199 Electron/21.3.3 Safari/537.36"
}

BILI_API = os.environ.get("BILI_API", "https://api.bilibili.com")

LOCAL_MODE = os.environ.get("LOCAL_MODE", False)


def escape_markdown(text):
    return (
        re.sub(r"([_*\[\]()~`>\#\+\-=|{}\.!\\])", r"\\\1", html.unescape(text))
        if text
        else str()
    )
