import os

from tortoise import Tortoise, fields
from tortoise.models import Model

from biliparser.utils import logger


class file_cache(Model):
    mediafilename = fields.CharField(64, pk=True, unique=True)
    file_id = fields.CharField(128, unique=True)
    created = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "file"


async def db_init() -> None:
    db_url = os.environ.get("DATABASE_URL", "sqlite://cache.db")
    logger.info(f"db_url: {db_url}")
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        logger.info(f"redis_url: {redis_url}")
    await Tortoise.init(
        db_url=db_url,
        modules={"models": ["database"]},
        use_tz=True,
    )
    await Tortoise.generate_schemas()


async def db_close() -> None:
    await Tortoise.close_connections()
