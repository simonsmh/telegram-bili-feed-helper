"""测试 channel 层 — Channel ABC、TelegramChannel"""
import os
import pytest

from biliparser.channel import Channel
from biliparser.model import Author, MediaConstraints, MediaInfo, ParsedContent, PreparedMedia
from biliparser.provider import ProviderRegistry


class DummyChannel(Channel):
    @property
    def media_constraints(self):
        return MediaConstraints(
            max_upload_size=50 * 1024 * 1024,
            max_download_size=2 * 1024 * 1024 * 1024,
            caption_max_length=1024,
        )

    def format_caption(self, content):
        return content.url

    async def send_content(self, content, media, context):
        pass

    async def send_text(self, text, context):
        pass

    async def cache_sent_media(self, content, result):
        pass

    async def get_cached_media(self, filename):
        return None

    async def start(self, provider_registry):
        pass

    async def stop(self):
        pass


class TestChannelABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Channel()

    def test_dummy_channel_media_constraints(self):
        ch = DummyChannel()
        mc = ch.media_constraints
        assert mc.max_upload_size == 50 * 1024 * 1024
        assert mc.caption_max_length == 1024

    def test_dummy_channel_format_caption(self):
        ch = DummyChannel()
        pc = ParsedContent(url="https://example.com", author=Author())
        assert ch.format_caption(pc) == "https://example.com"


class TestTelegramChannel:
    def test_media_constraints_default(self):
        from biliparser.channel.telegram import TelegramChannel
        ch = TelegramChannel()
        mc = ch.media_constraints
        assert mc.max_upload_size == 50 * 1024 * 1024
        assert mc.caption_max_length == 1024
        assert mc.local_mode is False

    def test_media_constraints_local_mode(self):
        from biliparser.channel.telegram import TelegramChannel
        old = os.environ.get("LOCAL_MODE")
        os.environ["LOCAL_MODE"] = "1"
        try:
            ch = TelegramChannel()
            mc = ch.media_constraints
            assert mc.local_mode is True
            assert mc.max_upload_size == 2 * 1024 * 1024 * 1024
        finally:
            if old is None:
                os.environ.pop("LOCAL_MODE", None)
            else:
                os.environ["LOCAL_MODE"] = old

    @pytest.mark.asyncio
    async def test_get_cached_media_returns_none_without_db(self):
        """没有初始化 DB 时应该抛异常或返回 None"""
        from biliparser.channel.telegram import TelegramChannel
        ch = TelegramChannel()
        # 没有 db_init，查询会失败
        with pytest.raises(Exception):
            await ch.get_cached_media("nonexistent.jpg")

    @pytest.mark.asyncio
    async def test_start_sets_registry(self):
        from biliparser.channel.telegram import TelegramChannel
        ch = TelegramChannel()
        registry = ProviderRegistry()
        await ch.start(registry)
        assert ch._registry is registry

    @pytest.mark.asyncio
    async def test_stop(self):
        from biliparser.channel.telegram import TelegramChannel
        ch = TelegramChannel()
        await ch.stop()  # 不应抛异常
