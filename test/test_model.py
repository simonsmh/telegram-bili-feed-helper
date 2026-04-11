from biliparser.model import Author, Comment, MediaInfo, MediaConstraints, ParsedContent, PreparedMedia
from pathlib import Path

def test_author_defaults():
    a = Author()
    assert a.name == ""
    assert a.uid == ""

def test_media_constraints():
    mc = MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
    )
    assert mc.local_mode is False

def test_parsed_content_minimal():
    pc = ParsedContent(url="https://example.com", author=Author())
    assert pc.title == ""
    assert pc.content == ""
    assert pc.media is None
    assert pc.comments == []
    assert pc.cache_keys == {}

def test_prepared_media_cleanup():
    pm = PreparedMedia(files=[], thumbnail=None, cleanup_paths=[])
    assert pm.files == []
