import httpx
import orjson

from ..cache import (
    CACHES_TIMER,
    RedisCache,
)
from ..utils import (
    BILI_API,
    logger,
    retry_catcher,
)


@retry_catcher
async def parse_reply(client: httpx.AsyncClient, oid, reply_type):
    logger.info(f"处理评论信息: 评论ID: {oid} 评论类型: {reply_type}")
    # 1.获取缓存
    try:
        cache = RedisCache().get(f"reply:{oid}:{reply_type}")
    except Exception as e:
        logger.exception(f"拉取评论缓存错误: {e}")
        cache = None
    # 2.拉取评论
    if cache:
        logger.info(f"拉取评论缓存: {oid}")
        reply = orjson.loads(cache) # type: ignore
    else:
        try:
            r = await client.get(
                BILI_API + "/x/v2/reply/main",
                params={"oid": oid, "type": reply_type},
                headers={"Referer": "https://www.bilibili.com/client"},
            )
            response = r.json()
        except Exception as e:
            logger.exception(f"评论获取错误: {oid}-{reply_type} {e}")
            return {}
        # 3.评论解析
        if not response or not response.get("data"):
            logger.warning(f"评论解析错误: {oid}-{reply_type} {response}")
            return {}
        reply = response["data"]
        # 4.缓存评论
        try:
            RedisCache().set(
                f"reply:{oid}:{reply_type}",
                orjson.dumps(reply),
                ex=CACHES_TIMER.get("reply"),
                nx=True,
            )
        except Exception as e:
            logger.exception(f"缓存评论错误: {e}")
    return reply
