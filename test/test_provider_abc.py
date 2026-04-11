import pytest
from biliparser.provider import Provider, ProviderRegistry
from biliparser.model import Author, MediaConstraints, ParsedContent, PreparedMedia


class DummyProvider(Provider):
    def can_handle(self, url: str) -> bool:
        return "dummy" in url

    async def parse(self, urls, constraints, extra=None):
        return [ParsedContent(url=u, author=Author()) for u in urls]

    async def prepare_media(self, content, constraints):
        return PreparedMedia(files=[], thumbnail=None)


def test_provider_registry_find():
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    assert registry.find_provider("https://dummy.com/test") is not None
    assert registry.find_provider("https://other.com/test") is None


@pytest.mark.asyncio
async def test_provider_registry_parse():
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    mc = MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
    )
    results = await registry.parse(["https://dummy.com/test"], mc)
    assert len(results) == 1
