"""测试 channel/telegram/bot.py — format_caption_for_telegram"""
from biliparser.model import Author, Comment, MediaConstraints, MediaInfo, ParsedContent
from biliparser.channel.telegram.bot import format_caption_for_telegram


def _mc(max_len=1024):
    return MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=max_len,
    )


def test_basic_url_only():
    pc = ParsedContent(url="https://bilibili.com/video/BV123", author=Author())
    caption = format_caption_for_telegram(pc, _mc())
    assert "bilibili" in caption


def test_with_extra_markdown():
    pc = ParsedContent(
        url="https://bilibili.com/video/BV123",
        author=Author(),
        extra_markdown="[标题](https://bilibili.com/video/BV123)",
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert "[标题]" in caption


def test_with_author():
    pc = ParsedContent(
        url="https://bilibili.com/video/BV123",
        author=Author(name="UP主", uid="12345"),
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert "UP主" in caption
    assert "12345" in caption


def test_author_no_uid():
    """没有 uid 时不应生成 user_markdown"""
    pc = ParsedContent(
        url="https://bilibili.com/video/BV123",
        author=Author(name="UP主", uid=""),
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert "space.bilibili.com" not in caption


def test_with_content():
    pc = ParsedContent(
        url="https://bilibili.com/video/BV123",
        author=Author(name="UP主", uid="12345"),
        content="这是视频描述",
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert "视频描述" in caption


def test_with_comments():
    pc = ParsedContent(
        url="https://bilibili.com/video/BV123",
        author=Author(),
        comments=[
            Comment(author=Author(name="评论者A"), text="好看", is_target=True),
            Comment(author=Author(name="评论者B"), text="顶", is_top=True),
        ],
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert "评论者A" in caption
    assert "评论者B" in caption


def test_target_comment_prefix():
    pc = ParsedContent(
        url="https://bilibili.com",
        author=Author(),
        comments=[Comment(author=Author(name="user"), text="msg", is_target=True)],
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert "💬" in caption


def test_top_comment_prefix():
    pc = ParsedContent(
        url="https://bilibili.com",
        author=Author(),
        comments=[Comment(author=Author(name="user"), text="msg", is_top=True)],
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert "🔝" in caption


def test_truncation():
    pc = ParsedContent(
        url="https://bilibili.com/video/BV123",
        author=Author(name="UP主", uid="12345"),
        content="A" * 2000,
    )
    caption = format_caption_for_telegram(pc, _mc(max_len=100))
    assert len(caption) <= 100


def test_escape_special_chars():
    """特殊字符应被转义"""
    pc = ParsedContent(
        url="https://bilibili.com",
        author=Author(),
        content="hello_world [test]",
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert r"\_" in caption or "hello" in caption


def test_content_markdown_preferred():
    """content_markdown 应优先于 content"""
    pc = ParsedContent(
        url="https://bilibili.com",
        author=Author(),
        content="plain text",
        content_markdown="already\\_escaped",
    )
    caption = format_caption_for_telegram(pc, _mc())
    assert "already\\_escaped" in caption


def test_empty_content():
    pc = ParsedContent(url="https://bilibili.com", author=Author())
    caption = format_caption_for_telegram(pc, _mc())
    assert caption  # 至少有 URL
