"""测试 biliparser/uploader/download.py — cleanup_medias"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from biliparser.model import Author, MediaConstraints, ParsedContent
from biliparser.provider import ProviderRegistry
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


def _media_constraints() -> MediaConstraints:
    return MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
    )


def test_telegram_upload_task_uses_context_message():
    from telegram import Message

    from biliparser.channel.telegram.uploader import TelegramUploadTask

    message = MagicMock(spec=Message)
    task = TelegramUploadTask(
        user_id=1,
        context=message,
        parsed_content=ParsedContent(url="https://example.com", author=Author()),
        media=[],
        mediathumb=None,
        urls=["https://example.com"],
    )

    assert task.message is message


@pytest.mark.asyncio
async def test_telegram_upload_success_deletes_share_message(monkeypatch):
    from biliparser.channel.telegram.uploader import TelegramUploadQueueManager, TelegramUploadTask

    manager = TelegramUploadQueueManager(
        registry=ProviderRegistry(),
        constraints=_media_constraints(),
    )
    message = MagicMock()
    task = TelegramUploadTask(
        user_id=1,
        context=message,
        parsed_content=ParsedContent(url="https://example.com", author=Author()),
        media=[],
        mediathumb=None,
        urls=["https://example.com"],
    )
    upload_media = AsyncMock(return_value=object())
    delete_share_message = AsyncMock()
    monkeypatch.setattr(manager, "_upload_media", upload_media)
    monkeypatch.setattr(manager, "_try_delete_share_message", delete_share_message)

    await manager._do_upload(task)

    upload_media.assert_called_once_with(task)
    delete_share_message.assert_called_once_with(task)
