"""测试 model.py — 所有数据类"""

from pathlib import Path

from biliparser.model import (
    Author,
    Comment,
    MediaConstraints,
    MediaInfo,
    ParsedContent,
    PreparedMedia,
)


def test_author_defaults():
    a = Author()
    assert a.name == ""
    assert a.uid == ""


def test_author_with_values():
    a = Author(name="test_user", uid="12345")
    assert a.name == "test_user"
    assert a.uid == "12345"


def test_comment():
    c = Comment(author=Author(name="user1"), text="hello", is_top=True)
    assert c.author.name == "user1"
    assert c.text == "hello"
    assert c.is_top is True
    assert c.is_target is False


def test_media_info_defaults():
    m = MediaInfo(urls=["http://a.jpg"], type="image")
    assert m.thumbnail == ""
    assert m.duration == 0
    assert m.dimension == {"width": 0, "height": 0, "rotate": 0}
    assert m.filenames == []
    assert m.need_download is False


def test_media_info_full():
    m = MediaInfo(
        urls=["http://a.mp4"],
        type="video",
        thumbnail="http://thumb.jpg",
        duration=120,
        dimension={"width": 1920, "height": 1080, "rotate": 0},
        title="Test Video",
        filenames=["a.mp4"],
        thumbnail_filename="thumb.jpg",
        need_download=True,
    )
    assert m.type == "video"
    assert m.duration == 120
    assert m.need_download is True


def test_media_constraints():
    mc = MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
    )
    assert mc.local_mode is False
    assert mc.max_upload_size == 50 * 1024 * 1024


def test_media_constraints_local_mode():
    mc = MediaConstraints(
        max_upload_size=2 * 1024 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
        local_mode=True,
    )
    assert mc.local_mode is True


def test_parsed_content_minimal():
    pc = ParsedContent(url="https://example.com", author=Author())
    assert pc.title == ""
    assert pc.content == ""
    assert pc.media is None
    assert pc.comments == []
    assert pc.cache_keys == {}
    assert pc.extra_markdown == ""
    assert pc.source_url == ""


def test_parsed_content_full():
    pc = ParsedContent(
        url="https://bilibili.com/video/BV123",
        author=Author(name="up主", uid="999"),
        title="测试视频",
        content="视频描述",
        extra_markdown="[标题](https://bilibili.com)",
        media=MediaInfo(urls=["http://a.mp4"], type="video"),
        comments=[Comment(author=Author(name="评论者"), text="好看", is_target=True)],
        cache_keys={"video:aid": "video:aid:123"},
    )
    assert pc.author.name == "up主"
    assert pc.media.type == "video"
    assert len(pc.comments) == 1
    assert pc.comments[0].is_target is True


def test_parsed_content_mutable_defaults():
    """确保 list/dict 默认值不共享"""
    pc1 = ParsedContent(url="a", author=Author())
    pc2 = ParsedContent(url="b", author=Author())
    pc1.comments.append(Comment(author=Author(), text="x"))
    assert len(pc2.comments) == 0
    pc1.cache_keys["k"] = "v"
    assert len(pc2.cache_keys) == 0


def test_prepared_media():
    pm = PreparedMedia(files=[Path("/tmp/a.mp4"), "file_id_123"], thumbnail=Path("/tmp/thumb.jpg"))
    assert len(pm.files) == 2
    assert pm.cleanup_paths == []


def test_prepared_media_cleanup_paths():
    pm = PreparedMedia(
        files=[],
        thumbnail=None,
        cleanup_paths=[Path("/tmp/a"), Path("/tmp/b")],
    )
    assert len(pm.cleanup_paths) == 2
