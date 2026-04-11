import pytest
from unittest.mock import patch
import tempfile
import os

def test_fake_redis_set_get():
    """Test using a fresh FakeRedis with temp dir"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.dict(os.environ, {"LOCAL_TEMP_FILE_PATH": tmpdir}):
            # Need fresh import to pick up new env
            from biliparser.storage.cache import FakeRedis
            cache = FakeRedis()
            cache.cache_file = os.path.join(tmpdir, "cache.json")
            import asyncio
            asyncio.run(_test_set_get(cache))

async def _test_set_get(cache):
    await cache.set("test_key", "test_value", ex=60)
    result = await cache.get("test_key")
    assert result == "test_value"

def test_fake_redis_nx():
    with tempfile.TemporaryDirectory() as tmpdir:
        from biliparser.storage.cache import FakeRedis
        cache = FakeRedis()
        cache.cache_file = os.path.join(tmpdir, "cache.json")
        import asyncio
        asyncio.run(_test_nx(cache))

async def _test_nx(cache):
    await cache.set("nx_key", "first", nx=True)
    await cache.set("nx_key", "second", nx=True)
    result = await cache.get("nx_key")
    assert result == "first"

def test_telegram_file_cache_model():
    from biliparser.storage.models import TelegramFileCache
    assert TelegramFileCache._meta.db_table is not None
