import asyncio
import os

import orjson
from bilibili_api import Credential
from loguru import logger

from .cache import CACHES_TIMER, RedisCache


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
                    dedeuserid=os.environ.get("DEDEUSERID"),
                    ac_time_value=os.environ.get("AC_TIME_VALUE"),
                )
                if not os.environ.get("FORCE_REFRESH_COOKIE"):
                    try:
                        result = await RedisCache().get("credential")
                        if result:
                            self._credential = Credential().from_cookies(
                                orjson.loads(result)
                            )
                    except Exception:
                        logger.exception("Failed to load credential.")
            try:
                if (
                    self._credential.ac_time_value
                    and await self._credential.check_refresh()
                ):
                    await self._credential.refresh()
                    try:
                        await RedisCache().set(
                            "credential",
                            orjson.dumps(self._credential.get_cookies()),
                            ex=CACHES_TIMER["CREDENTIAL"],
                        )
                    except Exception:
                        logger.exception("Failed to save credential.")
            except Exception as e:
                logger.exception(e)
            return self._credential
