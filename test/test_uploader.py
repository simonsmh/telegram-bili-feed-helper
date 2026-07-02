"""测试 biliparser/uploader/download.py — cleanup_medias"""

import tempfile
from pathlib import Path

from biliparser.uploader.download import cleanup_medias


def test_cleanup_medias_paths():
    """Path 类型的文件应被删除"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
        p = Path(f.name)
        f.write(b"test")
    assert p.exists()
    cleanup_medias([p])
    assert not p.exists()


def test_cleanup_medias_strings():
    """字符串类型（file_id）不应被删除"""
    cleanup_medias(["file_id_123", "another_id"])  # 不应抛异常


def test_cleanup_medias_mixed():
    """混合类型应只删除 Path"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        p = Path(f.name)
        f.write(b"test")
    cleanup_medias(["file_id", p, "another_id"])
    assert not p.exists()


def test_cleanup_medias_missing_file():
    """不存在的文件不应抛异常"""
    cleanup_medias([Path("/tmp/nonexistent_file_12345.jpg")])


def test_cleanup_medias_empty():
    cleanup_medias([])


def test_telegram_channel_constraints():
    """TelegramChannel.media_constraints 应返回正确的默认值"""
    from biliparser.channel.telegram import TelegramChannel

    ch = TelegramChannel()
    mc = ch.media_constraints
    assert mc.max_upload_size == 50 * 1024 * 1024  # 50MB (non-local mode)
    assert mc.max_download_size == 2 * 1024 * 1024 * 1024
    assert mc.caption_max_length == 1024
