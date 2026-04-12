"""
biliparser — 兼容入口

保持 `from biliparser import biliparser` 的向后兼容性。
内部改用 BilibiliProvider。
"""

from .model import MediaConstraints
from .provider.bilibili import BilibiliProvider

_provider = BilibiliProvider()
_default_constraints = MediaConstraints(
    max_upload_size=50 * 1024 * 1024,
    max_download_size=2 * 1024 * 1024 * 1024,
    caption_max_length=1024,
)


async def biliparser(urls, extra: dict | None = None):
    """向后兼容入口：解析 Bilibili URL，返回 ParsedContent 列表"""
    if isinstance(urls, str):
        urls = [urls]
    elif isinstance(urls, tuple):
        urls = list(urls)
    return await _provider.parse(list(set(urls)), _default_constraints, extra=extra)
