"""
多通道启动入口

支持同时运行多个 channel（Telegram、Discord），通过环境变量控制：
  TOKEN         → 启动 Telegram bot
  DISCORD_TOKEN → 启动 Discord bot

两者可同时设置，asyncio.gather 并发运行，共享同一个 ProviderRegistry 和数据库连接。
"""

import asyncio
import os
import sys

from .channel.discord import DiscordChannel
from .channel.discord.bot import run_bot as run_discord
from .channel.telegram import TelegramChannel
from .channel.telegram.bot import run_bot_async as run_telegram
from .provider import ProviderRegistry
from .provider.bilibili import BilibiliProvider
from .storage import db_close, db_context, db_init
from .utils import logger


async def _run_all(registry: ProviderRegistry) -> None:
    await db_init()

    coroutines = []

    if os.environ.get("TOKEN"):
        logger.info("启动 Telegram channel...")
        coroutines.append(run_telegram(TelegramChannel(), registry))

    if os.environ.get("DISCORD_TOKEN"):
        logger.info("启动 Discord channel...")
        coroutines.append(run_discord(DiscordChannel(), registry))

    if not coroutines:
        logger.error("至少需要配置一个 channel（TOKEN 或 DISCORD_TOKEN）")
        sys.exit(1)

    try:
        await asyncio.gather(*coroutines)
    finally:
        await db_close()


def main() -> None:
    # 支持通过命令行参数传入 Telegram token（向后兼容）
    if not os.environ.get("TOKEN") and len(sys.argv) >= 2:
        os.environ["TOKEN"] = sys.argv[1]

    if not os.environ.get("TOKEN") and not os.environ.get("DISCORD_TOKEN"):
        logger.error("Need TOKEN or DISCORD_TOKEN (env var or first argument).")
        sys.exit(1)

    registry = ProviderRegistry()
    registry.register(BilibiliProvider())
    # 未来可扩展：
    # registry.register(YouTubeProvider())

    with db_context():
        asyncio.run(_run_all(registry))


if __name__ == "__main__":
    main()
