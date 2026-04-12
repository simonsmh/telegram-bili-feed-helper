"""测试兼容入口 — from biliparser import biliparser"""
import pytest

from biliparser import biliparser
from biliparser.model import ParsedContent


def test_biliparser_is_callable():
    assert callable(biliparser)


def test_import_model_classes():
    from biliparser.model import (
        Author, Comment, MediaInfo, MediaConstraints,
        ParsedContent, PreparedMedia,
    )
    # 所有类都应可导入
    assert Author is not None
    assert ParsedContent is not None


def test_import_provider():
    from biliparser.provider import Provider, ProviderRegistry
    from biliparser.provider.bilibili import BilibiliProvider
    assert BilibiliProvider is not None


def test_import_channel():
    from biliparser.channel import Channel
    from biliparser.channel.telegram import TelegramChannel
    assert TelegramChannel is not None


def test_import_storage():
    from biliparser.storage import db_init, db_close
    from biliparser.storage.cache import RedisCache, FakeRedis
    from biliparser.storage.models import TelegramFileCache
    assert RedisCache is not None


def test_import_utils():
    from biliparser.utils import logger, compress, escape_markdown, get_filename
    assert logger is not None
    assert callable(compress)
    assert callable(escape_markdown)
    assert callable(get_filename)


def test_import_bot():
    from biliparser.channel.telegram.bot import (
        format_caption_for_telegram, add_handlers, run_bot,
    )
    assert callable(format_caption_for_telegram)
    assert callable(add_handlers)


def test_import_uploader():
    from biliparser.channel.telegram.uploader import (
        UploadTask, UploadQueueManager, get_cached_media_file_id,
        cleanup_medias, get_media,
    )
    assert UploadTask is not None
    assert UploadQueueManager is not None
