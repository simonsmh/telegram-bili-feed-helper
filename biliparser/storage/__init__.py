import os

from tortoise import Tortoise

from .cache import LOCAL_FILE_PATH


async def db_init() -> None:
    from ..utils import logger
    db_url = os.environ.get(
        "DATABASE_URL", "sqlite://" + str(LOCAL_FILE_PATH / "cache.db")
    )
    logger.info(f"db_url: {db_url}")
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        logger.info(f"redis_url: {redis_url}")
    await Tortoise.init(
        db_url=db_url,
        modules={"models": ["biliparser.storage.models"]},
        use_tz=True,
    )
    await Tortoise.generate_schemas()


async def db_close() -> None:
    await Tortoise.close_connections()
