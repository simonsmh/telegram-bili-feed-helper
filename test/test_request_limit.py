import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from biliparser.channel.telegram import bot
from biliparser.storage.cache import FakeRedis


def _make_cache(tmpdir):
    cache = FakeRedis()
    cache.cache_file = Path(tmpdir) / "request_limit_cache.json"
    cache.cache = {"__version": 2}
    return cache


@pytest.mark.asyncio
async def test_request_limit_disabled(monkeypatch):
    monkeypatch.delenv("REQUEST_LIMIT_COUNT", raising=False)
    monkeypatch.delenv("REQUEST_LIMIT_TTL", raising=False)

    allowed, remaining, ttl = await bot.check_request_limit(123)

    assert allowed is True
    assert remaining == 0
    assert ttl == 0


@pytest.mark.asyncio
async def test_request_limit_counts_with_ttl(monkeypatch):
    limit_ttl = 60
    monkeypatch.setenv("REQUEST_LIMIT_COUNT", "2")
    monkeypatch.setenv("REQUEST_LIMIT_TTL", str(limit_ttl))
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = _make_cache(tmpdir)
        monkeypatch.setattr(bot, "RedisCache", lambda: cache)

        first = await bot.check_request_limit(123)
        second = await bot.check_request_limit(123)
        third = await bot.check_request_limit(123)

        assert first[0] is True
        assert second[0] is True
        assert third[0] is False
        assert third[1] == 0
        assert 0 < third[2] <= limit_ttl


@pytest.mark.asyncio
async def test_request_limit_requires_count_and_ttl(monkeypatch):
    monkeypatch.setenv("REQUEST_LIMIT_COUNT", "2")
    monkeypatch.setenv("REQUEST_LIMIT_TTL", "0")

    allowed, remaining, ttl = await bot.check_request_limit(123)

    assert allowed is True
    assert remaining == 0
    assert ttl == 0


@pytest.mark.asyncio
async def test_request_limit_disabled_without_count(monkeypatch):
    monkeypatch.setenv("REQUEST_LIMIT_COUNT", "0")
    monkeypatch.setenv("REQUEST_LIMIT_TTL", "60")

    allowed, remaining, ttl = await bot.check_request_limit(123)

    assert allowed is True
    assert remaining == 0
    assert ttl == 0


@pytest.mark.asyncio
async def test_message_request_limit_can_be_silent(monkeypatch):
    monkeypatch.setattr(bot, "check_request_limit", AsyncMock(return_value=(False, 0, 60)))
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        chat=SimpleNamespace(id=456),
        reply_text=AsyncMock(),
    )

    allowed = await bot.check_message_request_limit(message, reply_on_limit=False)

    assert allowed is False
    message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_message_request_limit_replies_by_default(monkeypatch):
    monkeypatch.setattr(bot, "check_request_limit", AsyncMock(return_value=(False, 0, 60)))
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        chat=SimpleNamespace(id=456),
        reply_text=AsyncMock(),
    )

    allowed = await bot.check_message_request_limit(message)

    assert allowed is False
    message.reply_text.assert_called_once()
