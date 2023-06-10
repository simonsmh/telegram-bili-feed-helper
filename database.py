import asyncio
import os
from datetime import datetime, timedelta

from tortoise import Tortoise, fields
from tortoise.models import Model


class reply_cache(Model):
    oid = fields.BigIntField(pk=True, unique=True)
    reply_type = fields.IntField()
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(minutes=20)

    class Meta:
        table = "reply"


class dynamic_cache(Model):
    dynamic_id = fields.BigIntField(pk=True, unique=True)
    rid = fields.BigIntField(unique=True)
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "dynamic"


class audio_cache(Model):
    audio_id = fields.IntField(pk=True, unique=True)
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "audio"


class live_cache(Model):
    room_id = fields.IntField(pk=True, unique=True)
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(minutes=5)

    class Meta:
        table = "live"


class bangumi_cache(Model):
    epid = fields.IntField(pk=True, unique=True)
    ssid = fields.IntField()
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "bangumi"


class video_cache(Model):
    aid = fields.BigIntField(pk=True, unique=True)
    bvid = fields.CharField(max_length=12, unique=True)
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "video"


class read_cache(Model):
    read_id = fields.IntField(pk=True, unique=True)
    graphurl = fields.TextField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "read"


CACHES = {
    "audio": audio_cache,
    "bangumi": bangumi_cache,
    "dynamic": dynamic_cache,
    "live": live_cache,
    "read": read_cache,
    "reply": reply_cache,
    "video": video_cache,
}


async def db_init() -> None:
    await Tortoise.init(
        db_url=os.environ.get("DATABASE_URL", "sqlite://cache.db"),
        modules={"models": ["database"]},
        use_tz=True,
    )
    await Tortoise.generate_schemas()


async def db_close() -> None:
    await Tortoise.close_connections()


async def db_status():
    tasks = [item.all().count() for item in CACHES.values()]
    result = await asyncio.gather(*tasks)
    ans = ""
    for key, item in zip(CACHES.keys(), await asyncio.gather(*tasks)):
        ans += f"{key}: {item}\n"
    ans += f"总计: {sum(result)}"
    return ans


async def cache_clear():
    for item in CACHES.values():
        await item.filter(created__lt=datetime.utcnow() - item.timeout).delete()
    return await db_status()


async def db_clear(target):
    if CACHES.get(target):
        await CACHES[target].filter(
            created__lt=datetime.utcnow() - CACHES[target].timeout
        ).delete()
    else:
        return await cache_clear()
    return await db_status()
