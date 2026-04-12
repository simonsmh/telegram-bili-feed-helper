"""测试 provider 层 — Provider ABC、ProviderRegistry、BilibiliProvider"""
import pytest

from biliparser.model import Author, MediaConstraints, MediaInfo, ParsedContent, PreparedMedia
from biliparser.provider import Provider, ProviderRegistry
from biliparser.provider.bilibili import BilibiliProvider


# --- Provider ABC ---

class DummyProvider(Provider):
    def can_handle(self, url: str) -> bool:
        return "dummy" in url

    async def parse(self, urls, constraints, extra=None):
        return [ParsedContent(url=u, author=Author(name="dummy")) for u in urls]

    async def prepare_media(self, content, constraints):
        return PreparedMedia(files=[], thumbnail=None)


class AnotherProvider(Provider):
    def can_handle(self, url: str) -> bool:
        return "another" in url

    async def parse(self, urls, constraints, extra=None):
        return [ParsedContent(url=u, author=Author(name="another")) for u in urls]

    async def prepare_media(self, content, constraints):
        return PreparedMedia(files=[], thumbnail=None)


def _mc():
    return MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
    )


# --- ProviderRegistry ---

def test_registry_find_provider():
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    assert registry.find_provider("https://dummy.com/test") is not None
    assert registry.find_provider("https://other.com/test") is None


def test_registry_find_first_match():
    registry = ProviderRegistry()
    d = DummyProvider()
    a = AnotherProvider()
    registry.register(d)
    registry.register(a)
    assert registry.find_provider("https://dummy.com") is d
    assert registry.find_provider("https://another.com") is a


@pytest.mark.asyncio
async def test_registry_parse_single_provider():
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    results = await registry.parse(["https://dummy.com/1", "https://dummy.com/2"], _mc())
    assert len(results) == 2
    assert all(r.author.name == "dummy" for r in results)


@pytest.mark.asyncio
async def test_registry_parse_multiple_providers():
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    registry.register(AnotherProvider())
    results = await registry.parse(["https://dummy.com/1", "https://another.com/2"], _mc())
    assert len(results) == 2
    names = {r.author.name for r in results}
    assert names == {"dummy", "another"}


@pytest.mark.asyncio
async def test_registry_parse_unhandled_url():
    """无法处理的 URL 应被忽略"""
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    results = await registry.parse(["https://unknown.com/test"], _mc())
    assert len(results) == 0


@pytest.mark.asyncio
async def test_registry_parse_empty():
    registry = ProviderRegistry()
    results = await registry.parse([], _mc())
    assert results == []


@pytest.mark.asyncio
async def test_registry_parse_with_extra():
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    results = await registry.parse(["https://dummy.com/1"], _mc(), extra={"quality": "720P"})
    assert len(results) == 1


# --- BilibiliProvider.can_handle ---

class TestBilibiliProviderCanHandle:
    def setup_method(self):
        self.p = BilibiliProvider()

    def test_video_url(self):
        assert self.p.can_handle("https://www.bilibili.com/video/BV1bW411n7fY")

    def test_bangumi_ep(self):
        assert self.p.can_handle("https://www.bilibili.com/bangumi/play/ep317535")

    def test_bangumi_ss(self):
        assert self.p.can_handle("https://www.bilibili.com/bangumi/play/ss33055")

    def test_live(self):
        assert self.p.can_handle("https://live.bilibili.com/115")

    def test_audio(self):
        assert self.p.can_handle("https://www.bilibili.com/audio/au1360511")

    def test_dynamic(self):
        assert self.p.can_handle("https://t.bilibili.com/379593676394065939")

    def test_dynamic_h5(self):
        assert self.p.can_handle("https://t.bilibili.com/h5/dynamic/detail/371333904522848558")

    def test_read(self):
        assert self.p.can_handle("https://www.bilibili.com/read/cv12345")

    def test_short_link(self):
        assert self.p.can_handle("https://b23.tv/xZCcov")

    def test_bare_bvid(self):
        assert self.p.can_handle("BV1bW411n7fY")

    def test_bare_avid(self):
        assert self.p.can_handle("av19390801")

    def test_festival(self):
        assert self.p.can_handle("https://www.bilibili.com/festival/gswdm?bvid=BV1bW411n7fY")

    def test_youtube_rejected(self):
        assert not self.p.can_handle("https://youtube.com/watch?v=abc")

    def test_twitter_rejected(self):
        assert not self.p.can_handle("https://twitter.com/user/status/123")

    def test_empty_rejected(self):
        assert not self.p.can_handle("")

    def test_plain_text_rejected(self):
        assert not self.p.can_handle("hello world")
