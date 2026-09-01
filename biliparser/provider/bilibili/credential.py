import asyncio

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

    @staticmethod
    def _to_cookies(credential: Credential) -> dict[str, str]:
        """Serialize only the cookie names understood by ``Credential.from_cookies``.

        ``bilibili-api-python``'s ``get_cookies`` also exposes implementation
        attributes (such as lowercase ``sessdata``).  Persisting that dict makes
        a later restore ambiguous, so keep a small canonical representation.
        """
        cookies = {
            "SESSDATA": credential.sessdata or "",
            "bili_jct": credential.bili_jct or "",
            "buvid3": credential.buvid3 or "",
            "buvid4": credential.buvid4 or "",
            "ac_time_value": credential.ac_time_value or "",
        }
        if credential.dedeuserid:
            cookies["DedeUserID"] = credential.dedeuserid
        return cookies

    @staticmethod
    def _from_cookies(cookies: dict) -> Credential:
        credential = Credential.from_cookies(cookies)
        if not credential.has_sessdata():
            raise ValueError("Bilibili 登录凭证缺少 SESSDATA")
        return credential

    async def get(self):
        async with self._lock:
            if self._credential is None:
                self._credential = Credential()
                try:
                    result = await RedisCache().get("credential")
                    if result:
                        self._credential = self._from_cookies(orjson.loads(result))
                        logger.info("Credential 从 Redis 加载成功")
                    else:
                        logger.info("Redis 中无 Bilibili credential，请管理员使用 /login 扫码登录")
                except Exception:
                    logger.exception("Failed to load credential from Redis.")
            try:
                if (
                    self._credential.has_sessdata()
                    and self._credential.ac_time_value
                    and await self._credential.check_refresh()
                ):
                    logger.info("Credential 需要刷新，正在刷新...")
                    await self._credential.refresh()
                    logger.info("Credential 刷新成功")
                    try:
                        await RedisCache().set(
                            "credential",
                            orjson.dumps(self._to_cookies(self._credential)),
                            ex=CACHES_TIMER["CREDENTIAL"],
                        )
                        logger.info("Credential 已保存到 Redis")
                    except Exception:
                        logger.exception("Failed to save credential to Redis.")
            except Exception as e:
                logger.exception(e)
            return self._credential

    async def set(self, credential: Credential) -> Credential:
        """Replace the active credential and persist it for the next restart."""
        async with self._lock:
            if not credential.has_sessdata():
                raise ValueError("Bilibili 登录凭证缺少 SESSDATA")
            self._credential = credential
            await RedisCache().set(
                "credential",
                orjson.dumps(self._to_cookies(credential)),
                ex=CACHES_TIMER["CREDENTIAL"],
            )
            logger.info("Credential 已更新并保存到 Redis")
            return credential


credentialFactory = CredentialFactory()
