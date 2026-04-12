from abc import ABC, abstractmethod
from typing import Any

from ..model import MediaConstraints, ParsedContent, PreparedMedia
from ..provider import ProviderRegistry


class Channel(ABC):
    @property
    @abstractmethod
    def media_constraints(self) -> MediaConstraints:
        """声明本通道的媒体能力"""

    @abstractmethod
    def format_caption(self, content: ParsedContent) -> str:
        """将 ParsedContent 格式化为通道特定的文本"""

    @abstractmethod
    async def send_content(self, content: ParsedContent, media: PreparedMedia | None, context: Any) -> Any:
        """发送内容到通道"""

    @abstractmethod
    async def send_text(self, text: str, context: Any) -> None:
        """发送纯文本"""

    @abstractmethod
    async def cache_sent_media(self, content: ParsedContent, result: Any) -> None:
        """缓存已发送的媒体标识"""

    @abstractmethod
    async def get_cached_media(self, filename: str) -> str | None:
        """查询已缓存的媒体标识"""

    @abstractmethod
    async def start(self, provider_registry: ProviderRegistry) -> None:
        """启动通道"""

    @abstractmethod
    async def stop(self) -> None:
        """停止通道"""
