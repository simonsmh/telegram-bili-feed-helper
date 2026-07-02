"""
Discord bot 实现

包含：
- format_caption_for_discord: 将 ParsedContent 格式化为 Discord markdown
- DiscordContext: 携带发送目标的上下文
- DiscordUploadTask: 在基类 UploadTask 上增加 Discord 上下文
- DiscordUploadQueueManager: 实现 _do_upload/_do_cache
- run_bot: 启动 Discord bot（async，供 __main__.py 的 asyncio.gather 调用）
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any

import discord

from ...model import MediaConstraints, ParsedContent
from ...provider import ProviderRegistry
from ...uploader.queue import UploadQueueManager, UploadTask
from ...utils import logger

BILIBILI_URL_REGEX = re.compile(
    r"(?i)(?:https?://)?[\w\.]*?(?:bilibili(?:bb)?\.com|(?:b23(?:bb)?|acg)\.tv|bili2?2?3?3?\.cn)\S+|BV\w{10}"
)


# ── Caption 格式化 ─────────────────────────────────────────────────────────────


def format_caption_for_discord(content: ParsedContent, constraints: MediaConstraints) -> str:
    """将 ParsedContent 格式化为 Discord markdown 字符串。

    Discord markdown 与 Telegram MarkdownV2 不同：
    - 引用用 > （每行前缀）
    - 剧透用 ||text||
    - 无需转义大多数特殊字符
    """
    max_len = constraints.caption_max_length
    parts: list[str] = []

    # 第一行：标题链接或原始 URL
    first_line = content.extra_markdown or f"<{content.url}>"
    # extra_markdown 是 Telegram MarkdownV2 格式，需要还原为纯文本链接
    # 如果有 title，用 Discord 的 [title](url) 格式
    if content.title and content.url:
        first_line = f"[{content.title}](<{content.url}>)"
    elif content.url:
        first_line = f"<{content.url}>"
    parts.append(first_line)

    # 作者
    if content.author.name and content.author.uid:
        parts.append(f"[@{content.author.name}](https://space.bilibili.com/{content.author.uid})")
    elif content.author.name:
        parts.append(f"@{content.author.name}")

    # 正文（用 > 引用块 + || 剧透折叠）
    body = content.content or ""
    if body:
        quoted = "\n".join(f"> {line}" for line in body.splitlines())
        parts.append(f"||{quoted}||")

    # 评论
    for c in content.comments:
        prefix = "💬" if c.is_target else "🔝"
        comment_lines = "\n".join(f"> {line}" for line in c.text.splitlines())
        parts.append(f"{prefix} @{c.author.name}:\n||{comment_lines}||")

    result = "\n".join(parts)
    if len(result) > max_len:
        result = result[: max_len - 1] + "…"
    return result


# ── DiscordContext ─────────────────────────────────────────────────────────────


@dataclass
class DiscordContext:
    """携带 Discord 发送目标的上下文"""

    message: discord.Message  # 触发消息，用于 reply


# ── DiscordUploadTask ──────────────────────────────────────────────────────────


@dataclass
class DiscordUploadTask(UploadTask):
    """在基类基础上增加 Discord 上下文"""

    discord_context: DiscordContext | None = field(default=None)


# ── DiscordUploadQueueManager ──────────────────────────────────────────────────


class DiscordUploadQueueManager(UploadQueueManager):
    """Discord 专属上传队列管理器。

    不缓存：Discord CDN URL 会过期，每次重新下载上传。
    文件大小：运行时读 guild.filesize_limit，超出则发 embed + 链接。
    """

    async def _do_upload(self, task: UploadTask) -> Any:
        assert isinstance(task, DiscordUploadTask)
        return await self._send_content(task)

    async def _do_cache(self, content: ParsedContent, result: Any) -> None:
        pass  # Discord CDN URL 会过期，不缓存

    async def _send_content(self, task: DiscordUploadTask) -> Any:
        assert task.discord_context is not None
        message = task.discord_context.message
        f = task.parsed_content
        media = task.media
        mediathumb = task.mediathumb

        caption = format_caption_for_discord(f, self.constraints)

        # 无媒体，纯文本回复
        if not media or not f.media:
            await message.reply(caption, mention_author=False)
            return None

        # 动态获取服务器文件大小限制
        max_size = message.guild.filesize_limit if message.guild else 10 * 1024 * 1024

        if f.media.type == "video":
            await self._send_video(message, f, media, mediathumb, caption, max_size)
        elif f.media.type == "audio":
            await self._send_audio(message, f, media, mediathumb, caption, max_size)
        else:
            await self._send_images(message, f, media, caption, max_size)

        return None  # Discord 不需要缓存返回值

    async def _send_video(
        self,
        message: discord.Message,
        f: ParsedContent,
        media: list,
        mediathumb: Any,
        caption: str,
        max_size: int,
    ) -> None:
        from pathlib import Path

        media_item = media[0]
        file_size = media_item.stat().st_size if isinstance(media_item, Path) else 0

        if isinstance(media_item, Path) and file_size <= max_size:
            filename = f.media.filenames[0] if f.media.filenames else "video.mp4"
            await message.reply(
                content=caption,
                file=discord.File(str(media_item), filename=filename),
                mention_author=False,
            )
        else:
            # 超出大小限制或为 URL，发 embed + fallback 链接
            video_url = f.media.fallback_url or (media_item if isinstance(media_item, str) else f.media.urls[0])
            embed = discord.Embed(description=caption, url=f.url)
            if f.media.thumbnail:
                embed.set_thumbnail(url=f.media.thumbnail)
            if f.media.title:
                embed.title = f.media.title[:256]
            embed.add_field(name="视频链接", value=f"[点击观看]({video_url})", inline=False)
            await message.reply(embed=embed, mention_author=False)

    async def _send_audio(
        self,
        message: discord.Message,
        f: ParsedContent,
        media: list,
        mediathumb: Any,
        caption: str,
        max_size: int,
    ) -> None:
        from pathlib import Path

        media_item = media[0]
        file_size = media_item.stat().st_size if isinstance(media_item, Path) else 0

        if isinstance(media_item, Path) and file_size <= max_size:
            filename = f.media.filenames[0] if f.media.filenames else "audio.mp3"
            await message.reply(
                content=caption,
                file=discord.File(str(media_item), filename=filename),
                mention_author=False,
            )
        else:
            audio_url = media_item if isinstance(media_item, str) else f.media.urls[0]
            embed = discord.Embed(description=caption, url=f.url)
            if f.media.thumbnail:
                embed.set_thumbnail(url=f.media.thumbnail)
            embed.add_field(name="音频链接", value=f"[点击收听]({audio_url})", inline=False)
            await message.reply(embed=embed, mention_author=False)

    async def _send_images(
        self,
        message: discord.Message,
        f: ParsedContent,
        media: list,
        caption: str,
        max_size: int,
    ) -> None:
        from pathlib import Path

        discord_files = []
        fallback_urls = []

        for i, (media_item, url) in enumerate(zip(media, f.media.urls, strict=False)):
            filename = f.media.filenames[i] if f.media.filenames and i < len(f.media.filenames) else f"image_{i}.jpg"
            if isinstance(media_item, Path):
                file_size = media_item.stat().st_size
                if file_size <= max_size:
                    discord_files.append(discord.File(str(media_item), filename=filename))
                else:
                    fallback_urls.append(url)
            else:
                # str 类型（URL），直接放 embed
                fallback_urls.append(media_item)

        # Discord 单条消息最多 10 个附件
        if discord_files:
            for i in range(0, len(discord_files), 10):
                batch = discord_files[i : i + 10]
                content = caption if i == 0 else None
                await message.reply(content=content, files=batch, mention_author=False)

        # 超出大小的图片用 embed 发送
        if fallback_urls:
            for i in range(0, len(fallback_urls), 4):  # embed 最多 4 张图
                batch = fallback_urls[i : i + 4]
                embed = discord.Embed(description=caption if i == 0 else None, url=f.url)
                embed.set_image(url=batch[0])
                for extra_url in batch[1:]:
                    embed.add_field(name="", value=extra_url, inline=True)
                await message.reply(embed=embed, mention_author=False)


# ── Bot 事件处理 ───────────────────────────────────────────────────────────────


class BilibiliDiscordBot(discord.Client):
    """Discord bot 主体，监听消息中的 Bilibili URL 并触发解析"""

    def __init__(self, channel, registry: ProviderRegistry, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True  # 需要在 Discord 开发者后台开启 privileged intent
        super().__init__(intents=intents, **kwargs)
        self._channel = channel
        self._registry = registry
        self._upload_manager: DiscordUploadQueueManager | None = None

    async def setup_hook(self) -> None:
        """登录后、连接 gateway 前执行，适合初始化异步资源"""
        max_workers = int(os.environ.get("UPLOAD_WORKERS", 4))
        max_user_tasks = int(os.environ.get("MAX_USER_TASKS", 5))
        max_queue_size = int(os.environ.get("MAX_QUEUE_SIZE", 200))
        self._upload_manager = DiscordUploadQueueManager(
            registry=self._registry,
            constraints=self._channel.media_constraints,
            max_workers=max_workers,
            max_user_tasks=max_user_tasks,
            max_queue_size=max_queue_size,
        )
        await self._upload_manager.start_workers()
        await self._channel.start(self._registry)
        logger.info(f"Discord 上传队列已启动 ({max_workers} workers)")

    async def close(self) -> None:
        if self._upload_manager:
            await self._upload_manager.stop_workers()
        await self._channel.stop()
        await super().close()
        logger.info("Discord bot 已停止")

    async def on_ready(self) -> None:
        logger.info(f"Discord bot 已就绪: {self.user} ({self.user.id})")

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return

        # 支持斜杠风格的文本命令 /parse <url>
        text = message.content or ""
        is_parse_cmd = text.startswith("/parse")
        if is_parse_cmd:
            text = text[len("/parse") :].strip()

        urls = BILIBILI_URL_REGEX.findall(text)
        if not urls:
            if is_parse_cmd:
                await message.reply("链接不正确", mention_author=False)
            return

        logger.info(f"Discord parse: {urls} (用户: {message.author.id})")

        async with message.channel.typing():
            parsed_results = await self._registry.parse(urls, self._channel.media_constraints)

        for f in parsed_results:
            if isinstance(f, Exception):
                logger.warning(f"Discord 解析错误: {f}")
                if is_parse_cmd:
                    await message.reply(str(f), mention_author=False)
                continue

            ctx = DiscordContext(message=message)
            task = DiscordUploadTask(
                user_id=message.author.id,
                context=ctx,
                parsed_content=f,
                media=[],
                mediathumb=None,
                urls=urls,
                discord_context=ctx,
            )
            await self._upload_manager.submit(task)
            logger.info(f"Discord 已提交任务: {f.url} (用户: {message.author.id})")


# ── 启动入口 ───────────────────────────────────────────────────────────────────


async def run_bot(channel, provider_registry: ProviderRegistry) -> None:
    """启动 Discord bot（async 协程，供 asyncio.gather 并发调用）"""
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN 未设置，Discord bot 无法启动")
        return

    bot = BilibiliDiscordBot(channel, provider_registry)
    async with bot:
        await bot.start(token)
