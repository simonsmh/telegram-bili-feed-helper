"""测试 _feed_to_parsed_content — Feed 对象到 ParsedContent 的转换"""
import pytest
import httpx

from biliparser.provider.bilibili import _feed_to_parsed_content
from biliparser.provider.bilibili.feed import Feed
from biliparser.model import ParsedContent, Author, Comment, MediaInfo


class StubFeed(Feed):
    async def handle(self):
        return self


@pytest.fixture
def client():
    return httpx.AsyncClient()


def test_minimal_feed(client):
    f = StubFeed("https://bilibili.com/video/BV123", client)
    pc = _feed_to_parsed_content(f)
    assert isinstance(pc, ParsedContent)
    assert pc.url == "https://bilibili.com/video/BV123"
    assert pc.author.name == ""
    assert pc.media is None
    assert pc.comments == []


def test_feed_with_user(client):
    f = StubFeed("https://bilibili.com/video/BV123", client)
    f.user = "测试用户"
    f.uid = "12345"
    pc = _feed_to_parsed_content(f)
    assert pc.author.name == "测试用户"
    assert pc.author.uid == "12345"


def test_feed_with_media(client):
    f = StubFeed("https://bilibili.com/video/BV123", client)
    f.mediaurls = ["https://cdn.bilibili.com/video.mp4"]
    f.mediatype = "video"
    f.mediathumb = "https://cdn.bilibili.com/thumb.jpg"
    f.mediaduration = 120
    f.mediadimention = {"width": 1920, "height": 1080, "rotate": 0}
    f.mediatitle = "测试视频"
    f.mediaraws = True
    pc = _feed_to_parsed_content(f)
    assert pc.media is not None
    assert pc.media.type == "video"
    assert pc.media.urls == ["https://cdn.bilibili.com/video.mp4"]
    assert pc.media.thumbnail == "https://cdn.bilibili.com/thumb.jpg"
    assert pc.media.duration == 120
    assert pc.media.title == "测试视频"
    assert pc.media.need_download is True
    assert pc.media.dimension["width"] == 1920


def test_feed_with_image_list(client):
    f = StubFeed("https://t.bilibili.com/123", client)
    f.mediaurls = ["https://a.jpg", "https://b.jpg", "https://c.jpg"]
    f.mediatype = "image"
    pc = _feed_to_parsed_content(f)
    assert pc.media is not None
    assert len(pc.media.urls) == 3
    assert pc.media.type == "image"


def test_feed_no_media(client):
    f = StubFeed("https://bilibili.com/video/BV123", client)
    pc = _feed_to_parsed_content(f)
    assert pc.media is None


def test_feed_with_target_comment(client):
    f = StubFeed("https://bilibili.com/video/BV123", client)
    f.replycontent = {
        "target": {
            "member": {"uname": "评论者", "mid": "999"},
            "content": {"message": "好看"},
        },
        "top": None,
    }
    pc = _feed_to_parsed_content(f)
    assert len(pc.comments) == 1
    assert pc.comments[0].is_target is True
    assert pc.comments[0].author.name == "评论者"
    assert pc.comments[0].text == "好看"


def test_feed_with_top_comments_list(client):
    f = StubFeed("https://bilibili.com/video/BV123", client)
    f.replycontent = {
        "target": None,
        "top": [
            {"member": {"uname": "热评1"}, "content": {"message": "第一"}},
            {"member": {"uname": "热评2"}, "content": {"message": "第二"}},
        ],
    }
    pc = _feed_to_parsed_content(f)
    assert len(pc.comments) == 2
    assert all(c.is_top for c in pc.comments)


def test_feed_with_content_and_extra(client):
    f = StubFeed("https://bilibili.com/video/BV123", client)
    f.content = "视频描述内容"
    f.extra_markdown = "[标题](https://bilibili.com/video/BV123)"
    pc = _feed_to_parsed_content(f)
    assert pc.content == "视频描述内容"
    assert pc.extra_markdown == "[标题](https://bilibili.com/video/BV123)"


def test_feed_cache_keys(client):
    f = StubFeed("https://bilibili.com/video/BV123", client)
    # Feed 基类 cache_key 返回空 dict
    pc = _feed_to_parsed_content(f)
    assert pc.cache_keys == {}
