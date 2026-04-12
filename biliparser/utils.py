import html
import math
import os
import re
import sys
from io import BytesIO
from pathlib import Path

from loguru import logger
from PIL import Image

LOCAL_FILE_PATH = Path(os.environ.get("LOCAL_TEMP_FILE_PATH", os.getcwd()))

logger.remove()
logger.add(sys.stdout, backtrace=True, diagnose=True)
if os.environ.get("LOG_TO_FILE"):
    logger.add("bili_feed.log", backtrace=True, diagnose=True, rotation="1 MB")


def compress(inpil, size=1280, fix_ratio=False, format="PNG") -> BytesIO:
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
    if format.upper() == "JPEG":
        pil = pil.convert("RGB")
    pil.save(outpil, format, optimize=True)
    return outpil


def escape_markdown(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"([_*\[\]()~`>\#\+\-=|{}\.!\\])", r"\\\1", html.unescape(text))


def get_filename(url) -> str:
    target = re.search(r"\/([^\/]*\.\w{3,4})(?:$|\?)", url)
    if target:
        return target.group(1)
    return url
