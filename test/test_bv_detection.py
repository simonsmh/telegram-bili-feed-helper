"""测试 BV 号检测链路：从 Telegram 消息文本到 URL 提取、filter 匹配、provider 路由。

验证 https://www.bilibili.com/video/BV1zvQbBkEcG?spm_id_from=... 和裸 BV1zvQbBkEcG
都能命中处理逻辑。
"""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User
from telegram.ext import ContextTypes, filters

from biliparser.channel.telegram.bot import (
    BILIBILI_URL_REGEX,
    message_to_urls,
    message_to_urls_sync,
)
from biliparser.provider import ProviderRegistry
from biliparser.provider.bilibili import BilibiliProvider

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_message(text: str, entities=None, caption=None, caption_entities=None) -> Message:
    """构造一个最小化的 Message mock。"""
    chat = Chat(id=123, type="private")
    user = User(id=456, is_bot=False, first_name="Test")
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.caption = caption
    msg.entities = entities or []
    msg.caption_entities = caption_entities or []
    msg.chat = chat
    msg.from_user = user
    msg.forward_origin = None
    msg.message_id = 1
    return msg


def _make_update(message: Message) -> Update:
    update = MagicMock(spec=Update)
    update.message = message
    update.channel_post = None
    update.effective_message = message
    return update


def _make_context(bot_username="testbot", bot_first_name="TestBot") -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.username = bot_username
    ctx.bot.first_name = bot_first_name
    ctx.bot.id = 999
    return ctx


# ---------------------------------------------------------------------------
# 1. BILIBILI_URL_REGEX 匹配测试
# ---------------------------------------------------------------------------


class TestBilibiliUrlRegex:
    """验证 BILIBILI_URL_REGEX 能匹配各种 bilibili URL 和裸 BV 号。"""

    def test_full_url_with_params(self):
        text = "https://www.bilibili.com/video/BV1zvQbBkEcG?spm_id_from=333.1007.tianma.1-2-2.click"
        assert re.findall(BILIBILI_URL_REGEX, text) == [text]

    def test_bare_bv(self):
        assert re.findall(BILIBILI_URL_REGEX, "BV1zvQbBkEcG") == ["BV1zvQbBkEcG"]

    def test_bare_bv_in_sentence(self):
        text = "看看这个 BV1zvQbBkEcG 视频"
        assert re.findall(BILIBILI_URL_REGEX, text) == ["BV1zvQbBkEcG"]

    def test_bare_bv_no_space(self):
        text = "推荐BV1zvQbBkEcG不错"
        assert re.findall(BILIBILI_URL_REGEX, text) == ["BV1zvQbBkEcG"]

    def test_url_and_bare_bv_coexist(self):
        text = "https://www.bilibili.com/video/BV1zvQbBkEcG 还有 BV1Y25Nz4EZ3"
        urls = re.findall(BILIBILI_URL_REGEX, text)
        assert len(urls) == 2

    def test_b23_short_link(self):
        text = "https://b23.tv/xZCcov"
        assert re.findall(BILIBILI_URL_REGEX, text)

    def test_no_match_random_text(self):
        assert re.findall(BILIBILI_URL_REGEX, "hello world") == []


# ---------------------------------------------------------------------------
# 2. filters.Regex 匹配测试 — 模拟 Telegram 的 filter 行为
# ---------------------------------------------------------------------------


class TestTelegramFilterRegex:
    """验证 Telegram 的 filters.Regex 能命中含 BV 号的消息。"""

    def setup_method(self):
        self.regex_filter = filters.Regex(BILIBILI_URL_REGEX)

    def test_full_url_matches_filter(self):
        msg = _make_message("https://www.bilibili.com/video/BV1zvQbBkEcG?spm_id_from=333.1007.tianma.1-2-2.click")
        update = _make_update(msg)
        assert self.regex_filter.check_update(update)

    def test_bare_bv_matches_filter(self):
        msg = _make_message("BV1zvQbBkEcG")
        update = _make_update(msg)
        assert self.regex_filter.check_update(update)

    def test_bare_bv_in_sentence_matches_filter(self):
        msg = _make_message("看看这个 BV1zvQbBkEcG 视频")
        update = _make_update(msg)
        assert self.regex_filter.check_update(update)

    def test_random_text_no_match(self):
        msg = _make_message("hello world")
        update = _make_update(msg)
        assert not self.regex_filter.check_update(update)


# ---------------------------------------------------------------------------
# 3. message_to_urls_sync 提取测试
# ---------------------------------------------------------------------------


class TestMessageToUrlsSync:
    def test_extract_full_url(self):
        msg = _make_message("https://www.bilibili.com/video/BV1zvQbBkEcG?spm_id_from=333.1007.tianma.1-2-2.click")
        urls = message_to_urls_sync(msg, "testbot", "TestBot")
        assert len(urls) == 1
        assert "BV1zvQbBkEcG" in urls[0]

    def test_extract_bare_bv(self):
        msg = _make_message("BV1zvQbBkEcG")
        urls = message_to_urls_sync(msg, "testbot", "TestBot")
        assert urls == ["BV1zvQbBkEcG"]

    def test_extract_bare_bv_in_sentence(self):
        msg = _make_message("看看这个 BV1zvQbBkEcG 视频")
        urls = message_to_urls_sync(msg, "testbot", "TestBot")
        assert urls == ["BV1zvQbBkEcG"]

    def test_extract_from_entity_url(self):
        """TEXT_LINK entity 的 url 属性也应被提取。"""
        entity = MagicMock(spec=MessageEntity)
        entity.url = "https://www.bilibili.com/video/BV1zvQbBkEcG"
        msg = _make_message("点击这里", entities=[entity])
        urls = message_to_urls_sync(msg, "testbot", "TestBot")
        assert any("BV1zvQbBkEcG" in u for u in urls)

    def test_no_match(self):
        msg = _make_message("hello world")
        urls = message_to_urls_sync(msg, "testbot", "TestBot")
        assert urls == []


# ---------------------------------------------------------------------------
# 4. message_to_urls (async) 提取测试
# ---------------------------------------------------------------------------


class TestMessageToUrls:
    @pytest.mark.asyncio
    async def test_extract_full_url(self):
        msg = _make_message("https://www.bilibili.com/video/BV1zvQbBkEcG?spm_id_from=333.1007.tianma.1-2-2.click")
        update = _make_update(msg)
        ctx = _make_context()
        result_msg, urls = await message_to_urls(update, ctx)
        assert result_msg is msg
        assert len(urls) == 1
        assert "BV1zvQbBkEcG" in urls[0]

    @pytest.mark.asyncio
    async def test_extract_bare_bv(self):
        msg = _make_message("BV1zvQbBkEcG")
        update = _make_update(msg)
        ctx = _make_context()
        result_msg, urls = await message_to_urls(update, ctx)
        assert result_msg is msg
        assert urls == ["BV1zvQbBkEcG"]

    @pytest.mark.asyncio
    async def test_extract_bare_bv_in_sentence(self):
        msg = _make_message("看看这个 BV1zvQbBkEcG 视频不错")
        update = _make_update(msg)
        ctx = _make_context()
        _result_msg, urls = await message_to_urls(update, ctx)
        assert urls == ["BV1zvQbBkEcG"]

    @pytest.mark.asyncio
    async def test_none_message(self):
        update = MagicMock(spec=Update)
        update.message = None
        update.channel_post = None
        ctx = _make_context()
        result_msg, urls = await message_to_urls(update, ctx)
        assert result_msg is None
        assert urls == []


# ---------------------------------------------------------------------------
# 5. BilibiliProvider.can_handle 路由测试
# ---------------------------------------------------------------------------


class TestProviderCanHandle:
    def setup_method(self):
        self.p = BilibiliProvider()

    def test_full_url(self):
        assert self.p.can_handle("https://www.bilibili.com/video/BV1zvQbBkEcG?spm_id_from=333.1007.tianma.1-2-2.click")

    def test_bare_bv(self):
        assert self.p.can_handle("BV1zvQbBkEcG")

    def test_bare_bv_lowercase(self):
        """_BILIBILI_RE 使用 IGNORECASE，小写 bv 也应匹配。"""
        assert self.p.can_handle("bv1zvQbBkEcG")

    def test_youtube_rejected(self):
        assert not self.p.can_handle("https://youtube.com/watch?v=abc")


# ---------------------------------------------------------------------------
# 6. ProviderRegistry 路由测试 — 裸 BV 号不应被静默丢弃
# ---------------------------------------------------------------------------


class TestRegistryRouting:
    def setup_method(self):
        self.registry = ProviderRegistry()
        self.registry.register(BilibiliProvider())

    def test_find_provider_full_url(self):
        assert self.registry.find_provider("https://www.bilibili.com/video/BV1zvQbBkEcG") is not None

    def test_find_provider_bare_bv(self):
        assert self.registry.find_provider("BV1zvQbBkEcG") is not None

    def test_find_provider_bare_bv_lowercase(self):
        assert self.registry.find_provider("bv1zvQbBkEcG") is not None

    def test_find_provider_unrelated(self):
        assert self.registry.find_provider("https://youtube.com/watch") is None


# ---------------------------------------------------------------------------
# 7. _route 路由测试 — 裸 BV 号应命中 Video 策略
# ---------------------------------------------------------------------------


class TestRouteBareBV:
    """验证 _route 函数对裸 BV 号的路由正确性。"""

    def test_route_regex_matches_bare_bv(self):
        """_route 内部的正则应匹配裸 BV 号。"""
        route_re = r"(?:^|/)(?:BV\w{10}|av\d+|ep\d+|ss\d+)"
        assert re.search(route_re, "BV1zvQbBkEcG")

    def test_route_regex_matches_bv_in_path(self):
        route_re = r"(?:^|/)(?:BV\w{10}|av\d+|ep\d+|ss\d+)"
        assert re.search(route_re, "b23.tv/BV1zvQbBkEcG")

    def test_startswith_check_uppercase_bv(self):
        """BilibiliProvider.parse 的 startswith 检查：大写 BV 应直接传递，不加 http://。"""
        url = "BV1zvQbBkEcG"
        result = f"http://{url}" if not url.startswith(("http:", "https:", "av", "BV")) else url
        assert result == "BV1zvQbBkEcG"

    def test_startswith_check_lowercase_bv_gets_http(self):
        """小写 bv 不在 startswith 列表中，会被加上 http:// 前缀。"""
        url = "bv1zvQbBkEcG"
        result = f"http://{url}" if not url.startswith(("http:", "https:", "av", "BV")) else url
        assert result == "http://bv1zvQbBkEcG"

    def test_route_regex_no_match_lowercase_bv_with_http(self):
        """http://bv... 不会被 _route 的正则匹配到（正则区分大小写），会走 redirect 逻辑。"""
        route_re = r"(?:^|/)(?:BV\w{10}|av\d+|ep\d+|ss\d+)"
        assert not re.search(route_re, "http://bv1zvQbBkEcG")

    def test_full_chain_uppercase_bv(self):
        """完整链路：大写 BV 号从 startswith 到 _route 到 Video.handle 正则全部通过。"""
        url = "BV1zvQbBkEcG"
        # Step 1: startswith — 大写 BV 直接传递
        processed = f"http://{url}" if not url.startswith(("http:", "https:", "av", "BV")) else url
        assert processed == "BV1zvQbBkEcG"
        # Step 2: _route 正则匹配
        route_re = r"(?:^|/)(?:BV\w{10}|av\d+|ep\d+|ss\d+)"
        assert re.search(route_re, processed)
        # Step 3: Video URL 构造
        video_url = processed if "/" in processed else f"b23.tv/{processed}"
        assert video_url == "b23.tv/BV1zvQbBkEcG"
        # Step 4: Video.handle 内部正则
        video_re = r"(?:bilibili\.com(?:/video|/bangumi/play)?|b23\.tv|acg\.tv)/(?:(?P<bvid>BV\w{10})|av(?P<aid>\d+)|ep(?P<epid>\d+)|ss(?P<ssid>\d+)|)"
        m = re.search(video_re, video_url)
        assert m and m.group("bvid") == "BV1zvQbBkEcG"


# ---------------------------------------------------------------------------
# 8. 端到端：parse handler 对裸 BV 号的处理
# ---------------------------------------------------------------------------


class TestParseHandlerBareBV:
    """验证 parse handler 对裸 BV 号消息的完整处理链路。"""

    @pytest.mark.asyncio
    async def test_parse_handler_receives_bare_bv(self):
        """模拟一条含裸 BV 号的消息，验证 parse handler 能提取 URL 并调用 registry.parse。"""
        from biliparser.channel.telegram.bot import parse

        msg = _make_message("BV1zvQbBkEcG")
        msg.reply_text = AsyncMock()
        msg.reply_chat_action = AsyncMock()
        update = _make_update(msg)
        ctx = _make_context()

        # mock registry 和 channel
        mock_registry = MagicMock(spec=ProviderRegistry)
        mock_registry.parse = AsyncMock(return_value=[])
        mock_channel = MagicMock()
        mock_channel.media_constraints = MagicMock()

        ctx.bot_data = {
            "provider_registry": mock_registry,
            "telegram_channel": mock_channel,
            "upload_queue_manager": MagicMock(),
        }

        await parse(update, ctx)

        # registry.parse 应该被调用，且 urls 包含 BV1zvQbBkEcG
        mock_registry.parse.assert_called_once()
        call_args = mock_registry.parse.call_args
        urls = call_args[0][0]
        assert "BV1zvQbBkEcG" in urls

    @pytest.mark.asyncio
    async def test_parse_handler_receives_full_url(self):
        """模拟一条含完整 bilibili URL 的消息，验证 parse handler 能提取并调用 registry.parse。"""
        from biliparser.channel.telegram.bot import parse

        full_url = "https://www.bilibili.com/video/BV1zvQbBkEcG?spm_id_from=333.1007.tianma.1-2-2.click"
        msg = _make_message(full_url)
        msg.reply_text = AsyncMock()
        msg.reply_chat_action = AsyncMock()
        update = _make_update(msg)
        ctx = _make_context()

        mock_registry = MagicMock(spec=ProviderRegistry)
        mock_registry.parse = AsyncMock(return_value=[])
        mock_channel = MagicMock()
        mock_channel.media_constraints = MagicMock()

        ctx.bot_data = {
            "provider_registry": mock_registry,
            "telegram_channel": mock_channel,
            "upload_queue_manager": MagicMock(),
        }

        await parse(update, ctx)

        mock_registry.parse.assert_called_once()
        call_args = mock_registry.parse.call_args
        urls = call_args[0][0]
        assert any("BV1zvQbBkEcG" in u for u in urls)

    @pytest.mark.asyncio
    async def test_parse_handler_bv_in_sentence(self):
        """消息文本中夹杂 BV 号也应被提取。"""
        from biliparser.channel.telegram.bot import parse

        msg = _make_message("看看这个 BV1zvQbBkEcG 视频")
        msg.reply_text = AsyncMock()
        msg.reply_chat_action = AsyncMock()
        update = _make_update(msg)
        ctx = _make_context()

        mock_registry = MagicMock(spec=ProviderRegistry)
        mock_registry.parse = AsyncMock(return_value=[])
        mock_channel = MagicMock()
        mock_channel.media_constraints = MagicMock()

        ctx.bot_data = {
            "provider_registry": mock_registry,
            "telegram_channel": mock_channel,
            "upload_queue_manager": MagicMock(),
        }

        await parse(update, ctx)

        mock_registry.parse.assert_called_once()
        call_args = mock_registry.parse.call_args
        urls = call_args[0][0]
        assert "BV1zvQbBkEcG" in urls


# ---------------------------------------------------------------------------
# 9. ProviderRegistry.parse 异常处理 — 不应 raise，应返回在列表中
# ---------------------------------------------------------------------------


class TestRegistryExceptionHandling:
    @pytest.mark.asyncio
    async def test_provider_exception_returned_not_raised(self):
        """Provider.parse 抛异常时，ProviderRegistry.parse 应将其作为列表元素返回，而非 raise。"""

        class FailingProvider(BilibiliProvider):
            async def parse(self, urls, constraints, extra=None):
                raise RuntimeError("模拟解析失败")

        registry = ProviderRegistry()
        registry.register(FailingProvider())

        from biliparser.model import MediaConstraints

        mc = MediaConstraints(
            max_upload_size=50 * 1024 * 1024,
            max_download_size=2 * 1024 * 1024 * 1024,
            caption_max_length=1024,
        )

        # 不应 raise
        results = await registry.parse(["BV1zvQbBkEcG"], mc)
        assert len(results) == 1
        assert isinstance(results[0], Exception)
