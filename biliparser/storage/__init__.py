import os
from collections.abc import Iterator
from contextlib import contextmanager

from tortoise import Tortoise
from tortoise.context import TortoiseContext

from .cache import LOCAL_FILE_PATH


@contextmanager
def db_context() -> Iterator[None]:
    """
    在主线程 root context 里绑定一个 TortoiseContext。

    tortoise-orm 通过 contextvars 管理 ORM 状态；由于 python-telegram-bot
    用多个独立的 loop.run_until_complete 调用驱动生命周期（post_init、
    run_forever 等），在某个 Task 里调用 Tortoise.init() 设置的 ContextVar
    只在那个 Task 内可见，inline 等 handler Task 拿不到。

    把整个 application 的同步运行过程包在这个 context manager 里，
    ContextVar 在主线程 root context 上设一次，event loop 之后通过
    copy_context() 创建的所有 Task 都会继承，db_init/db_close 在 Task
    里调用时会复用这同一个 TortoiseContext 对象。
    """
    with TortoiseContext():
        yield


async def db_init() -> None:
    from ..utils import logger

    db_url = os.environ.get("DATABASE_URL", "sqlite://" + str(LOCAL_FILE_PATH / "cache.db"))
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
