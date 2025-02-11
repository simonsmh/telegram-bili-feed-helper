import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import redis.asyncio as redis

LOCAL_FILE_PATH = Path(os.environ.get("LOCAL_TEMP_FILE_PATH", os.getcwd()))

CACHE_TIMER_DEFAULTS = {
    # seconds * minutes * hours
    "CREDENTIAL": 60 * 60 * 24 * 7 * 4,
    "LOCK": 60 * 5,
    "AUDIO": 60 * 60,
    "BANGUMI": 60 * 60,
    "OPUS": 60 * 60,
    "LIVE": 60 * 5,
    "READ": 60 * 60,
    "REPLY": 60 * 60,
    "VIDEO": 60 * 60,
}

CACHES_TIMER = {
    k: int(os.environ.get(f"{k}_CACHE_TIME", v))
    for k, v in CACHE_TIMER_DEFAULTS.items()
}


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
                # Lock expired, replace it
                await self.store.set(self.lock_key, str(current_time))
                self._acquired = True
                return True
            else:
                # Lock is still valid
                return False
        else:
            # Lock is not present, create it
            await self.store.set(self.lock_key, str(current_time))
            self._acquired = True
            return True

    async def release(self):
        if self._acquired:
            await self.store.delete(self.lock_key)
            self._acquired = False

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
            with open(self.cache_file, "r", encoding="utf-8") as f:
                result = json.load(f)
                if isinstance(result, dict):
                    if result.get("__version") == 2:
                        return result
        except (IOError, json.JSONDecodeError):
            pass
        return {"__version": 2}

    def _save_cache(self):
        with open(self.cache_file, "w", encoding="utf-8") as f:
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
        self,
        key: str,
        value: str | bytes,
        ex: int | None = None,
        nx: bool | None = None,
        *args,
        **kwargs,
    ) -> None:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if nx and key in self.cache:
            return
        self.cache[key] = {"value": value}
        if isinstance(ex, int):
            self.cache[key]["timeout"] = int(time.time()) + ex
        self._save_cache()

    async def delete(self, key: str) -> None:
        if key in self.cache:
            del self.cache[key]
            self._save_cache()

    def lock(self, key: str, timeout: int = CACHES_TIMER["LOCK"]):
        return FakeLock(self, key, timeout)


class RedisCache:
    def __new__(cls):
        if not hasattr(cls, "instance"):
            if os.environ.get("REDIS_URL"):
                cls.instance = redis.Redis.from_url(os.environ["REDIS_URL"])
            else:
                cls.instance = FakeRedis()
        return cls.instance
