"""测试兼容入口 — from biliparser import biliparser"""

from biliparser import biliparser
from biliparser.model import ParsedContent


def test_biliparser_is_callable():
    assert callable(biliparser)


def test_import_model_classes():
    from biliparser.model import (
        Author,
    )

    # 所有类都应可导入
    assert Author is not None
    assert ParsedContent is not None


def test_import_provider():
    from biliparser.provider.bilibili import BilibiliProvider

    assert BilibiliProvider is not None


def test_import_channel():
    from biliparser.channel.telegram import TelegramChannel

    assert TelegramChannel is not None


def test_import_storage():
    from biliparser.storage.cache import RedisCache

    assert RedisCache is not None


def test_import_utils():
    from biliparser.utils import compress, escape_markdown, get_filename, logger

    assert logger is not None
    assert callable(compress)
    assert callable(escape_markdown)
    assert callable(get_filename)


def test_import_bot():
    from biliparser.channel.telegram.bot import (
        add_handlers,
        format_caption_for_telegram,
    )

    assert callable(format_caption_for_telegram)
    assert callable(add_handlers)


def test_import_uploader():
    from biliparser.channel.telegram.uploader import (
        UploadQueueManager,
        UploadTask,
    )

    assert UploadTask is not None
    assert UploadQueueManager is not None
