import httpx
from tortoise import timezone
from tortoise.expressions import Q

from ..database import (
    reply_cache,
)
from ..utils import (
    BILI_API,
    logger,
    retry_catcher,
)


@retry_catcher
async def parse_reply(client: httpx.AsyncClient, oid, reply_type):
    query = Q(oid=oid, reply_type=reply_type)
    cache = await reply_cache.get_or_none(
        query,
        created__gte=timezone.now() - reply_cache.timeout,
    )
    if cache:
        logger.info(f"拉取评论缓存: {cache.created}")
        reply = cache.content
    else:
        r = await client.get(
            BILI_API + "/x/v2/reply/main",
            params={"oid": oid, "type": reply_type},
            headers={"Referer": "https://www.bilibili.com/client"},
        )
        response = r.json()
        if not response.get("data"):
            logger.warning(
                f"评论ID: {oid}, 评论类型: {reply_type}, 获取错误: {response}"
            )
            return {}
        reply = response.get("data")
        if not reply:
            logger.warning(
                f"评论ID: {oid}, 评论类型: {reply_type}, 解析错误: {response}"
            )
            return {}
            # raise ParserException("评论解析错误", reply, r)
    logger.info(f"评论ID: {oid}, 评论类型: {reply_type}")
    if not cache:
        logger.info(f"评论缓存: {oid}")
        cache = await reply_cache.get_or_none(query)
        try:
            if cache:
                cache.content = reply
                await cache.save(update_fields=["content", "created"])
            else:
                await reply_cache(oid=oid, reply_type=reply_type, content=reply).save()
        except Exception as e:
            logger.exception(f"评论缓存错误: {e}")
    return reply
