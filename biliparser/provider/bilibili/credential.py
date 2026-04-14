import asyncio
import os

import orjson
from bilibili_api import Credential
from loguru import logger

from ...storage.cache import RedisCache
from .api import CACHES_TIMER


class CredentialFactory:
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._credential = None
        return cls._instance

    async def get(self):
        async with self._lock:
            if self._credential is None:
                self._credential = Credential(
                    sessdata=os.environ.get("SESSDATA"),
                    bili_jct=os.environ.get("BILI_JCT"),
                    buvid3=os.environ.get("BUVID3"),
                    buvid4=os.environ.get("BUVID4"),
                    dedeuserid=os.environ.get("DEDEUSERID"),
                    ac_time_value=os.environ.get("AC_TIME_VALUE"),
                )
                logger.info(f"Credential 从环境变量初始化, keys={list(self._credential.get_cookies().keys())}")
                if not os.environ.get("FORCE_REFRESH_COOKIE"):
                    try:
                        result = await RedisCache().get("credential")
                        if result:
                            loaded_cookies = orjson.loads(result)
                            logger.info(f"Redis credential 原始值: {result[:200]}...")
                            self._credential = Credential().from_cookies(loaded_cookies)
                            logger.info(f"Credential 从 Redis 加载成功, cookies={self._credential.get_cookies()}")
                        else:
                            logger.info("Redis 中无 credential 缓存，使用环境变量")
                    except Exception:
                        logger.exception("Failed to load credential from Redis.")
                else:
                    logger.info("FORCE_REFRESH_COOKIE 已设置，跳过 Redis 读取")
            try:
                if self._credential.ac_time_value and await self._credential.check_refresh():
                    logger.info("Credential 需要刷新，正在刷新...")
                    await self._credential.refresh()
                    logger.info("Credential 刷新成功")
                    try:
                        await RedisCache().set(
                            "credential",
                            orjson.dumps(self._credential.get_cookies()),
                            ex=CACHES_TIMER["CREDENTIAL"],
                        )
                        logger.info("Credential 已保存到 Redis")
                    except Exception:
                        logger.exception("Failed to save credential to Redis.")
            except Exception as e:
                logger.exception(e)
            return self._credential


credentialFactory = CredentialFactory()
