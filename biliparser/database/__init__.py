import asyncio
import os
from datetime import datetime, timedelta

from tortoise import Tortoise, fields
from tortoise.models import Model

from ..utils import logger


class read_cache(Model):
    read_id = fields.IntField(pk=True, unique=True)
    graphurl = fields.TextField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta(Model.Meta):
        table = "read"


# class file_cache(Model):
#     mediafilename = fields.CharField(50, pk=True, unique=True)
#     file_id = fields.CharField(50, unique=True)
#     created = fields.DatetimeField(auto_now=True)

#     class Meta(Model.Meta):
#         table = "file"


CACHES_MAP = {"read": read_cache}


async def db_init() -> None:
    db_url = os.environ.get("DATABASE_URL", "sqlite://cache.db")
    logger.info(f"db_url: {db_url}")
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        logger.info(f"redis_url: {redis_url}")
    await Tortoise.init(
        db_url=db_url,
        modules={"models": ["biliparser.database"]},
        use_tz=True,
    )
    await Tortoise.generate_schemas()


async def db_close() -> None:
    await Tortoise.close_connections()


async def db_status():
    tasks = [item.all().count() for item in CACHES_MAP.values()]
    result = await asyncio.gather(*tasks)
    ans = ""
    for key, item in zip(CACHES_MAP.keys(), await asyncio.gather(*tasks)):
        ans += f"{key}: {item}\n"
    ans += f"总计: {sum(result)}"
    return ans


async def cache_clear():
    for item in CACHES_MAP.values():
        await item.filter(created__lt=datetime.utcnow() - item.timeout).delete()
    return await db_status()


async def db_clear(target):
    if CACHES_MAP.get(target):
        await (
            CACHES_MAP[target]
            .filter(created__lt=datetime.utcnow() - CACHES_MAP[target].timeout)
            .delete()
        )
    else:
        return await cache_clear()
    return await db_status()
