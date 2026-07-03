"""
Discord Channel 实现

DiscordChannel: 实现 Channel ABC，声明 Discord 的媒体约束
"""

import os

from ...model import MediaConstraints, ParsedContent
from ...model import PreparedMedia as PreparedMedia
from ...provider import ProviderRegistry
from .. import Channel
from .bot import format_caption_for_discord


class DiscordChannel(Channel):
    def __init__(self) -> None:
        self._registry: ProviderRegistry | None = None

    @property
    def media_constraints(self) -> MediaConstraints:
        # 运行时由 guild.filesize_limit 动态决定上传限制；
        # 这里给一个保守默认值（10MB，无 boost 服务器），
        # 实际上传前会在 _do_upload 里按 guild 动态检查。
        local_mode = bool(os.environ.get("LOCAL_MODE", False))
        return MediaConstraints(
            max_upload_size=10 * 1024 * 1024,
            max_download_size=2 * 1024 * 1024 * 1024,
            caption_max_length=2000,
            local_mode=local_mode,
        )

    def format_caption(self, content: ParsedContent) -> str:
        return format_caption_for_discord(content, self.media_constraints)

    async def send_content(self, content: ParsedContent, media, context) -> None:
        pass

    async def send_text(self, text: str, context) -> None:
        pass

    async def cache_sent_media(self, content: ParsedContent, result) -> None:
        pass  # Discord CDN URL 会过期，不缓存

    async def get_cached_media(self, filename: str) -> str | None:
        return None  # 不缓存

    async def start(self, provider_registry: ProviderRegistry) -> None:
        self._registry = provider_registry

    async def stop(self) -> None:
        pass
