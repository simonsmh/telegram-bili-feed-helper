import sys
import os
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
logger.add(sys.stderr, backtrace=True, diagnose=True)
logger.add("bili_feed.log", backtrace=True, diagnose=True, rotation="1 MB")


headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.54 Safari/537.36"
}

BILI_API = os.environ.get("BILI_API", "https://api.bilibili.com")
