"""测试 provider/bilibili/feed.py — Feed 基类的纯数据属性和工具方法"""
import pytest
import httpx

from biliparser.provider.bilibili.feed import Feed


class ConcreteFeed(Feed):
    """Feed 是 ABC，需要一个具体子类来测试"""
    async def handle(self):
        return self


class TestFeedStaticMethods:
    def test_shrink_line_normal(self):
        assert Feed.shrink_line("  hello  ") == "hello"

    def test_shrink_line_empty(self):
        assert Feed.shrink_line("") == ""
        assert Feed.shrink_line(None) == ""

    def test_clean_cn_tag_style(self):
        result = Feed.clean_cn_tag_style("\\#标签\\#")
        assert "\\#标签 " in result

    def test_clean_cn_tag_style_empty(self):
        assert Feed.clean_cn_tag_style("") == ""
        assert Feed.clean_cn_tag_style(None) == ""

    def test_wan_below_threshold(self):
        assert Feed.wan(9999) == 9999

    def test_wan_above_threshold(self):
        result = Feed.wan(10000)
        assert "万" in str(result)
        assert "1.00" in str(result)

    def test_wan_large_number(self):
        result = Feed.wan(1234567)
        assert "万" in str(result)

    def test_make_user_markdown(self):
        result = Feed.make_user_markdown("用户名", "12345")
        assert "用户名" in result
        assert "12345" in result
        assert "space.bilibili.com" in result

    def test_make_user_markdown_empty(self):
        assert Feed.make_user_markdown("", "") == ""
        assert Feed.make_user_markdown("user", "") == ""
        assert Feed.make_user_markdown("", "123") == ""


class TestFeedProperties:
    @pytest.fixture
    def feed(self):
        client = httpx.AsyncClient()
        f = ConcreteFeed("https://test.bilibili.com/123", client)
        return f

    def test_default_values(self, feed):
        assert feed.user == ""
        assert feed.uid == ""
        assert feed.mediatype == ""
        assert feed.mediaduration == 0
        assert feed.mediatitle == ""
        assert feed.extra_markdown == ""

    def test_content_property(self, feed):
        feed.content = "  test content  "
        assert feed.content == "test content"

    def test_mediaurls_single(self, feed):
        feed.mediaurls = "https://example.com/image.jpg"
        assert feed.mediaurls == ["https://example.com/image.jpg"]

    def test_mediaurls_list(self, feed):
        feed.mediaurls = ["https://a.jpg", "https://b.jpg"]
        assert len(feed.mediaurls) == 2

    def test_mediafilename(self, feed):
        feed.mediaurls = ["https://example.com/path/image.jpg"]
        assert feed.mediafilename == ["image.jpg"]

    def test_mediathumb(self, feed):
        feed.mediathumb = "https://example.com/thumb.jpg"
        assert feed.mediathumb == "https://example.com/thumb.jpg"
        assert feed.mediathumbfilename == "thumb.jpg"

    def test_mediathumb_empty(self, feed):
        assert feed.mediathumb == ""
        assert feed.mediathumbfilename == ""

    def test_url_default(self, feed):
        assert feed.url == "https://test.bilibili.com/123"

    def test_cache_key_default(self, feed):
        assert feed.cache_key == {}

    def test_rawurl(self, feed):
        assert feed.rawurl == "https://test.bilibili.com/123"
