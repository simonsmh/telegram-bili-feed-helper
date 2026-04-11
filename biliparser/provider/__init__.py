import asyncio
from abc import ABC, abstractmethod

from ..model import MediaConstraints, ParsedContent, PreparedMedia


class Provider(ABC):
    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """URL 是否属于本 Provider"""

    @abstractmethod
    async def parse(self, urls: list[str], constraints: MediaConstraints,
                    extra: dict | None = None) -> list[ParsedContent]:
        """解析 URL 列表，返回 ParsedContent 列表"""

    @abstractmethod
    async def prepare_media(self, content: ParsedContent,
                            constraints: MediaConstraints) -> PreparedMedia:
        """按 Channel 的 constraints 下载/准备媒体"""


class ProviderRegistry:
    def __init__(self):
        self._providers: list[Provider] = []

    def register(self, provider: Provider) -> None:
        self._providers.append(provider)

    def find_provider(self, url: str) -> Provider | None:
        for provider in self._providers:
            if provider.can_handle(url):
                return provider
        return None

    async def parse(self, urls: list[str], constraints: MediaConstraints,
                    extra: dict | None = None) -> list[ParsedContent]:
        provider_urls: dict[int, tuple[Provider, list[str]]] = {}
        for url in urls:
            provider = self.find_provider(url)
            if provider is None:
                continue
            pid = id(provider)
            if pid not in provider_urls:
                provider_urls[pid] = (provider, [])
            provider_urls[pid][1].append(url)

        tasks = [
            provider.parse(purl_list, constraints, extra)
            for provider, purl_list in provider_urls.values()
        ]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[ParsedContent] = []
        for r in results_nested:
            if isinstance(r, Exception):
                raise r
            results.extend(r)
        return results
