"""测试 storage 层 — RedisCache、FakeRedis、FakeLock、TelegramFileCache"""

import tempfile
from pathlib import Path

import pytest

from biliparser.storage.cache import FakeLock, FakeRedis, RedisCache
from biliparser.storage.models import TelegramFileCache


class TestFakeRedis:
    def _make_cache(self, tmpdir):
        cache = FakeRedis()
        cache.cache_file = Path(tmpdir) / "test_cache.json"
        cache.cache = {"__version": 2}
        return cache

    @pytest.mark.asyncio
    async def test_set_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            await cache.set("key1", "value1", ex=60)
            result = await cache.get("key1")
            assert result == "value1"

    @pytest.mark.asyncio
    async def test_set_bytes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            await cache.set("key1", b"bytes_value")
            result = await cache.get("key1")
            assert result == "bytes_value"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            result = await cache.get("nonexistent")
            assert result is None

    @pytest.mark.asyncio
    async def test_nx_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            await cache.set("nx_key", "first", nx=True)
            await cache.set("nx_key", "second", nx=True)
            result = await cache.get("nx_key")
            assert result == "first"

    @pytest.mark.asyncio
    async def test_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            await cache.set("del_key", "value")
            await cache.delete("del_key")
            result = await cache.get("del_key")
            assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        """删除不存在的 key 不应报错"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            await cache.delete("nonexistent")

    @pytest.mark.asyncio
    async def test_expiry(self):
        """过期的 key 应返回 None"""
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            await cache.set("exp_key", "value", ex=1)
            # 手动设置过期时间为过去
            cache.cache["exp_key"]["timeout"] = int(time.time()) - 10
            result = await cache.get("exp_key")
            assert result is None

    @pytest.mark.asyncio
    async def test_version_key_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            result = await cache.get("__version")
            assert result is None

    def test_lock_returns_fake_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            lock = cache.lock("test_lock", timeout=10)
            assert isinstance(lock, FakeLock)

    @pytest.mark.asyncio
    async def test_persistence(self):
        """写入后文件应存在"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = self._make_cache(tmpdir)
            await cache.set("persist_key", "persist_value")
            assert cache.cache_file.exists()


class TestFakeLock:
    @pytest.mark.asyncio
    async def test_acquire_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FakeRedis()
            store.cache_file = Path(tmpdir) / "lock_cache.json"
            store.cache = {"__version": 2}
            lock = FakeLock(store, "test_lock", timeout=10)
            acquired = await lock.acquire()
            assert acquired is True
            await lock.release()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FakeRedis()
            store.cache_file = Path(tmpdir) / "lock_cache.json"
            store.cache = {"__version": 2}
            lock = FakeLock(store, "ctx_lock", timeout=10)
            async with lock:
                pass  # 不应抛异常


class TestRedisCacheSingleton:
    def test_singleton(self):
        """RedisCache 应返回同一实例"""
        a = RedisCache()
        b = RedisCache()
        assert a is b


class TestTelegramFileCache:
    def test_model_table(self):
        assert TelegramFileCache._meta.db_table is not None

    def test_model_fields(self):
        field_names = {f for f in TelegramFileCache._meta.fields_map}
        assert "mediafilename" in field_names
        assert "file_id" in field_names
        assert "created" in field_names
