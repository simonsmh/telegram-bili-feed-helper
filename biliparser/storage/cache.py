import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import redis.asyncio as redis

from ..utils import logger

LOCAL_FILE_PATH = Path(os.environ.get("LOCAL_TEMP_FILE_PATH", str(Path.cwd())))


class FakeLock:
    def __init__(self, store, lock_key, timeout=10):
        self.store = store
        self.lock_key = lock_key
        self.timeout = timeout
        self._acquired = False

    async def acquire(self):
        current_time = int(time.time())
        lock_value = await self.store.get(self.lock_key)
        if lock_value:
            if current_time - float(lock_value) > self.timeout:
                await self.store.set(self.lock_key, str(current_time))
                self._acquired = True
                return True
            return False
        await self.store.set(self.lock_key, str(current_time))
        self._acquired = True
        return True

    async def release(self):
        if self._acquired:
            await self.store.delete(self.lock_key)
            self._acquired = False

    async def extend(self, additional_time: int, replace_ttl: bool = False) -> bool:
        """Mirror the subset of redis-py's Lock API used by AutoRenewingLock."""
        if not self._acquired:
            return False
        await self.store.expire(self.lock_key, additional_time)
        return True

    async def __aenter__(self):
        while not await self.acquire():
            await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()


class FakeRedis:
    def __init__(self):
        self.cache_file = LOCAL_FILE_PATH / "cache.json"
        self.cache = self._load_cache()

    def _load_cache(self) -> dict[Any, Any]:
        try:
            with self.cache_file.open(encoding="utf-8") as f:
                result = json.load(f)
                if isinstance(result, dict) and result.get("__version") == 2:
                    return result
        except (OSError, json.JSONDecodeError):
            pass
        return {"__version": 2}

    def _save_cache(self):
        with self.cache_file.open("w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False)

    async def get(self, key: str):
        if key == "__version":
            return None
        target = self.cache.get(key)
        if target and isinstance(target, dict):
            if target.get("timeout") and target["timeout"] < int(time.time()):
                del self.cache[key]
                self._save_cache()
                return None
            return target.get("value")
        return None

    async def set(
        self, key: str, value: str | bytes, ex: int | None = None, nx: bool | None = None, *args, **kwargs
    ) -> None:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if nx and key in self.cache:
            return
        self.cache[key] = {"value": value}
        if isinstance(ex, int):
            self.cache[key]["timeout"] = int(time.time()) + ex
        self._save_cache()

    async def incr(self, key: str) -> int:
        value = await self.get(key)
        count = int(value or 0) + 1
        timeout = self.cache.get(key, {}).get("timeout")
        self.cache[key] = {"value": str(count)}
        if timeout:
            self.cache[key]["timeout"] = timeout
        self._save_cache()
        return count

    async def expire(self, key: str, time_seconds: int) -> bool:
        if key not in self.cache:
            return False
        self.cache[key]["timeout"] = int(time.time()) + time_seconds
        self._save_cache()
        return True

    async def ttl(self, key: str) -> int:
        if await self.get(key) is None:
            return -2
        timeout = self.cache.get(key, {}).get("timeout")
        if not timeout:
            return -1
        return max(0, timeout - int(time.time()))

    async def delete(self, key: str) -> None:
        if key in self.cache:
            del self.cache[key]
            self._save_cache()

    def lock(self, key: str, timeout: int = 3600):
        return FakeLock(self, key, timeout)


class RedisCache:
    def __new__(cls):
        if not hasattr(cls, "instance"):
            if os.environ.get("REDIS_URL"):
                cls.instance = redis.Redis.from_url(os.environ["REDIS_URL"])
            else:
                cls.instance = FakeRedis()
        return cls.instance


class AutoRenewingLock:
    """Keep a Redis lock alive while an upload or download is still running."""

    def __init__(self, lock: Any, timeout: int):
        self._lock = lock
        self._timeout = timeout
        self._renewal_task: asyncio.Task[None] | None = None

    async def __aenter__(self):
        await self._lock.__aenter__()
        self._renewal_task = asyncio.create_task(self._renew_periodically())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._renewal_task:
            self._renewal_task.cancel()
            await asyncio.gather(self._renewal_task, return_exceptions=True)
        return await self._lock.__aexit__(exc_type, exc_val, exc_tb)

    async def _renew_periodically(self) -> None:
        # Renew well before the lease expires. The production callers always use
        # whole-second timeouts, while the lower bound keeps this testable.
        interval = max(self._timeout / 2, 0.01)
        while True:
            await asyncio.sleep(interval)
            try:
                extended = await self._lock.extend(self._timeout, replace_ttl=True)
                if not extended:
                    logger.warning("Redis lock renewal failed because ownership was lost")
                    return
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.warning(f"Redis lock renewal failed: {err}")
                return


def auto_renewing_lock(key: str, timeout: int) -> AutoRenewingLock:
    """Return a lock whose TTL is renewed until the surrounding work finishes."""
    return AutoRenewingLock(RedisCache().lock(key, timeout=timeout), timeout)
