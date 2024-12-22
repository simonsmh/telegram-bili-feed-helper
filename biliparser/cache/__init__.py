import os

import json
import redis

CACHES_TIMER = {
    # seconds * minutes * hours * days
    "audio": 60 * 60 * 24 * 10,
    "bangumi": 60 * 60 * 24 * 10,
    "opus": 60 * 60 * 24 * 10,
    "live": 60 * 5,
    "read": 60 * 60 * 24 * 10,
    "reply": 60 * 25,
    "video": 60 * 60 * 24 * 10,
}


class FakeRedis:
    def __init__(self):
        try:
            with open("cache.json", "r", encoding='utf-8') as f:
                self.cache = json.load(f)
        except IOError:
            self.cache = {}

    def get(self, key: str):
        return self.cache[key].encode("utf-8") if key in self.cache else None

    def set(self, key: str, value: str | bytes, *args, **kwargs) -> None:
        with open("cache.json", "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False)
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        self.cache[key] = value


class RedisCache:
    def __new__(cls):
        if not hasattr(cls, "instance"):
            if os.environ.get("REDIS_URL"):
                cls.instance = redis.Redis.from_url(os.environ["REDIS_URL"])
            else:
                cls.instance = FakeRedis()
        return cls.instance
