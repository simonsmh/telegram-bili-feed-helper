"""
多通道启动入口

当前支持：TelegramChannel
未来可扩展：DiscordChannel、MatrixChannel 等
"""
import os
import sys

from .provider import ProviderRegistry
from .provider.bilibili import BilibiliProvider
from .utils import logger


def main() -> None:
    if not os.environ.get("TOKEN") and len(sys.argv) < 2:
        logger.error("Need TOKEN (env var or first argument).")
        sys.exit(1)
    if len(sys.argv) >= 2 and not os.environ.get("TOKEN"):
        os.environ["TOKEN"] = sys.argv[1]

    # 1. 注册 Provider
    registry = ProviderRegistry()
    registry.register(BilibiliProvider())
    # 未来可扩展：
    # registry.register(YouTubeProvider())

    # 2. 启动 Channel（目前只有 Telegram）
    from .channel.telegram import TelegramChannel
    from .channel.telegram.bot import run_bot

    telegram_channel = TelegramChannel()
    run_bot(telegram_channel, registry)


if __name__ == "__main__":
    main()
