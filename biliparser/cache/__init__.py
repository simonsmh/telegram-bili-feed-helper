import asyncio
import os
import time
import json
import redis.asyncio as redis

CACHES_TIMER = {
    # seconds * minutes * hours * days
    "lock": 60 * 5,
    "audio": 60 * 60 * 24 * 10,
    "bangumi": 60 * 60 * 24 * 10,
    "opus": 60 * 60 * 24 * 10,
    "live": 60 * 5,
    "read": 60 * 60 * 24 * 10,
    "reply": 60 * 25,
    "video": 60 * 60 * 24 * 10,
}


class FakeLock:
    def __init__(self, store, lock_key, timeout=10):
        self.store = store
        self.lock_key = lock_key
        self.timeout = timeout
        self._acquired = False

    async def acquire(self):
        current_time = time.time()
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
        self.cache_file = "cache.json"
        self.cache = self._load_cache()

    def _load_cache(self):
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False)

    async def get(self, key: str):
        return self.cache.get(key)

    async def set(self, key: str, value: str | bytes, *args, **kwargs) -> None:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        self.cache[key] = value
        self._save_cache()

    async def delete(self, key: str) -> None:
        if key in self.cache:
            del self.cache[key]
            self._save_cache()

    def lock(self, key: str, timeout: int = CACHES_TIMER["lock"]):
        return FakeLock(self, key, timeout)


class RedisCache:
    def __new__(cls):
        if not hasattr(cls, "instance"):
            if os.environ.get("REDIS_URL"):
                cls.instance = redis.Redis.from_url(os.environ["REDIS_URL"])
            else:
                cls.instance = FakeRedis()
        return cls.instance
