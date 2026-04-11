import os
import re
import sys
from uuid import uuid4

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultAudio,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
    InputTextMessageContent,
    Message,
    MessageEntity,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
    Update,
)
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    AIORateLimiter,
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from ...model import Comment, MediaConstraints, ParsedContent
from ...provider import ProviderRegistry
from ...storage.cache import RedisCache
from ...storage import db_init, db_close
from ...utils import escape_markdown, logger

BILIBILI_URL_REGEX = r"(?i)(?:https?://)?[\w\.]*?(?:bilibili(?:bb)?\.com|(?:b23(?:bb)?|acg)\.tv|bili2?2?3?3?\.cn)\S+|BV\w{10}"
BILIBILI_SHARE_URL_REGEX = (
    r"(?i)【.*】 https://[\w\.]*?(?:bilibili\.com|b23\.tv|bili2?2?3?3?\.cn)\S+"
)

SOURCE_CODE_MARKUP = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                text="源代码",
                url="https://github.com/simonsmh/telegram-bili-feed-helper",
            )
        ]
    ]
)


def format_caption_for_telegram(
    content: ParsedContent, constraints: MediaConstraints
) -> str:
    """Format ParsedContent into a Telegram MarkdownV2 caption string."""
    max_len = constraints.caption_max_length

    # Build user markdown link
    user_markdown = ""
    if content.author.name and content.author.uid:
        user_markdown = (
            f"[@{escape_markdown(content.author.name)}]"
            f"(https://space.bilibili.com/{content.author.uid})"
        )

    # Build extra/url part
    if content.extra_markdown:
        extra_part = content.extra_markdown
    else:
        extra_part = escape_markdown(content.url)

    # Build content part (prefer markdown version)
    content_text = content.content_markdown or escape_markdown(content.content)

    # Build comments part
    comment_lines = []
    for c in content.comments:
        author_str = escape_markdown(c.author.name)
        text_str = escape_markdown(c.text)
        if c.is_target:
            comment_lines.append(f"💬\\> @{author_str}:\n{text_str}")
        elif c.is_top:
            comment_lines.append(f"🔝\\> @{author_str}:\n{text_str}")
    comment_text = "\n".join(comment_lines)

    # Assemble caption parts
    parts = []
    if extra_part:
        parts.append(extra_part)
    if user_markdown:
        parts.append(user_markdown)
    if content_text:
        parts.append(content_text)
    if comment_text:
        parts.append(comment_text)

    caption = "\n".join(parts)

    # Truncate to max length
    if len(caption) > max_len:
        caption = caption[:max_len]

    return caption


async def get_description(context: ContextTypes.DEFAULT_TYPE) -> str:
    bot_me = await context.bot.get_me()
    local_mode = bool(os.environ.get("LOCAL_MODE", False))
    description: str = (
        f"欢迎使用 @{bot_me.username} 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组或频道自动匹配消息。\n"
        f"Inline 模式限制：只可发单张图，消耗设备流量，安全性低。\n"
        f"群组模式限制：{'图片小于 10MB，视频小于 50MB，' if not local_mode else ''}通过 Bot 上传速度较慢。\n"
    )
    return description


def message_to_urls_sync(message: Message, bot_username: str, bot_first_name: str) -> list[str]:
    """Extract Bilibili URLs from a message (sync helper)."""
    urls = re.findall(BILIBILI_URL_REGEX, message.text or message.caption or "")
    if message.entities:
        for entity in message.entities:
            if entity.url:
                urls.extend(re.findall(BILIBILI_URL_REGEX, entity.url))
    return urls


async def message_to_urls(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[Message | None, list[str]]:
    """Extract message and Bilibili URLs from an update, filtering bot's own forwards."""
    message = update.message or update.channel_post
    if message is None:
        return message, []

    if isinstance(message.forward_origin, MessageOriginUser):
        if (
            message.forward_origin.sender_user.is_bot
            and message.forward_origin.sender_user.username == context.bot.username
        ):
            return message, []
    elif isinstance(message.forward_origin, MessageOriginHiddenUser):
        if message.forward_origin.sender_user_name == context.bot.first_name:
            return message, []
    elif isinstance(message.forward_origin, MessageOriginChat):
        if message.forward_origin.author_signature == context.bot.first_name:
            return message, []
    elif isinstance(message.forward_origin, MessageOriginChannel):
        if message.forward_origin.author_signature == context.bot.first_name:
            return message, []
        else:
            try:
                self_user = await message.forward_origin.chat.get_member(context.bot.id)
                if self_user.status == "administrator":
                    return message, []
            except Exception:
                pass

    urls = re.findall(BILIBILI_URL_REGEX, message.text or message.caption or "")
    if message.entities:
        for entity in message.entities:
            if entity.url:
                urls.extend(re.findall(BILIBILI_URL_REGEX, entity.url))
    return message, urls


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Bilibili URL parse requests."""
    from .uploader import UploadTask, UploadQueueManager

    message, urls = await message_to_urls(update, context)
    if message is None:
        return

    is_parse = bool(message.text and message.text.startswith("/parse"))
    is_video = bool(message.text and message.text.startswith("/video"))
    extra = None

    if is_video:
        if (
            not message.text
            or message.text == "/video"
            or len(texts := message.text.split(" ")) < 2
        ):
            await message.reply_text("参数不正确，例如：/video 720P BV1Y25Nz4EZ3")
            return
        extra = {"quality": texts[1]}

    if not urls:
        if is_parse or is_video or message.chat.type == ChatType.PRIVATE:
            await message.reply_text("链接不正确")
        return

    logger.info(
        f"Parse: {urls} (用户: {message.from_user.id if message.from_user else 'unknown'})"
    )

    try:
        await message.reply_chat_action(ChatAction.TYPING)
    except Exception:
        pass

    registry: ProviderRegistry = context.bot_data["provider_registry"]
    telegram_channel = context.bot_data["telegram_channel"]
    mc = telegram_channel.media_constraints

    parsed_results = await registry.parse(urls, mc, extra=extra)

    for f in parsed_results:
        if isinstance(f, Exception):
            logger.warning(f"解析错误: {f}")
            if is_parse or is_video:
                await message.reply_text(str(f))
            continue

        if not f.media or not f.media.urls:
            caption = format_caption_for_telegram(f, mc)
            await message.reply_text(caption)
            continue

        user_id = message.from_user.id if message.from_user else message.chat.id
        task = UploadTask(
            user_id=user_id,
            message=message,
            parsed_content=f,
            media=[],
            mediathumb=None,
            is_parse_cmd=is_parse,
            is_video_cmd=is_video,
            urls=urls,
        )

        upload_queue_manager: UploadQueueManager = context.bot_data["upload_queue_manager"]
        await upload_queue_manager.submit(task)
        logger.info(f"已提交上传任务: {f.url} (用户: {user_id})")


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /file and /cover commands."""
    from .uploader import UploadTask, UploadQueueManager

    message, urls = await message_to_urls(update, context)
    if message is None or not message.text:
        return
    if not urls:
        await message.reply_text("链接不正确")
        return

    fetch_mode = "cover" if message.text.startswith("/cover") else "file"
    logger.info(
        f"Fetch ({fetch_mode}): {urls} (用户: {message.from_user.id if message.from_user else 'unknown'})"
    )

    registry: ProviderRegistry = context.bot_data["provider_registry"]
    telegram_channel = context.bot_data["telegram_channel"]
    mc = telegram_channel.media_constraints

    parsed_results = await registry.parse(urls, mc)

    for f in parsed_results:
        if isinstance(f, Exception):
            logger.warning(f"解析错误: {f}")
            await message.reply_text(str(f))
            continue

        if not f.media or not f.media.urls:
            continue

        user_id = message.from_user.id if message.from_user else message.chat.id
        task = UploadTask(
            user_id=user_id,
            message=message,
            parsed_content=f,
            media=[],
            mediathumb=None,
            is_parse_cmd=False,
            is_video_cmd=False,
            urls=urls,
            task_type="fetch",
            fetch_mode=fetch_mode,
        )

        upload_queue_manager: UploadQueueManager = context.bot_data["upload_queue_manager"]
        await upload_queue_manager.submit(task)
        logger.info(f"已提交 fetch 任务: {f.url} (用户: {user_id}, 模式: {fetch_mode})")


async def inlineparse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries."""
    from ...provider.bilibili.api import referer_url
    from .uploader import get_cached_media_file_id

    async def inline_query_answer(inline_query: InlineQuery, msg):
        try:
            await inline_query.answer(msg, cache_time=0, is_personal=True)
        except BadRequest as err:
            if (
                "Query is too old and response timeout expired or query id is invalid"
                in err.message
            ):
                logger.error(f"{err} -> Inline请求超时")
            else:
                logger.exception(err)
                raise err
        except Exception as err:
            logger.exception(err)
            raise err

    inline_query = update.inline_query
    if inline_query is None:
        return
    query = inline_query.query
    helpmsg = [
        InlineQueryResultArticle(
            id=uuid4().hex,
            title="帮助",
            description="将 Bot 添加到群组或频道可以自动匹配消息，请注意 Inline 模式存在限制：只可发单张图，消耗设备流量。",
            reply_markup=SOURCE_CODE_MARKUP,
            input_message_content=InputTextMessageContent(
                await get_description(context)
            ),
        )
    ]
    if not query:
        return await inline_query_answer(inline_query, helpmsg)
    url_re = re.search(BILIBILI_URL_REGEX, query)
    if url_re is None:
        return await inline_query_answer(inline_query, helpmsg)
    url = url_re.group(0)
    logger.info(f"Inline: {url}")

    registry: ProviderRegistry = context.bot_data["provider_registry"]
    telegram_channel = context.bot_data["telegram_channel"]
    mc = telegram_channel.media_constraints

    parsed_list = await registry.parse([url], mc)
    if not parsed_list:
        return await inline_query_answer(inline_query, helpmsg)

    f = parsed_list[0]
    if isinstance(f, Exception):
        logger.warning(f"解析错误! {f}")
        results = [
            InlineQueryResultArticle(
                id=uuid4().hex,
                title="解析错误！",
                description=escape_markdown(str(f)),
                input_message_content=InputTextMessageContent(str(f)),
            )
        ]
        return await inline_query_answer(inline_query, results)

    caption = format_caption_for_telegram(f, mc)

    if not f.media or not f.media.urls:
        results = [
            InlineQueryResultArticle(
                id=uuid4().hex,
                title=f.author.name,
                description=f.content,
                input_message_content=InputTextMessageContent(caption),
            )
        ]
    else:
        if f.media.type == "video":
            cache_file_id = await get_cached_media_file_id(f.media.filenames[0]) if f.media.filenames else None
            results = [
                (
                    InlineQueryResultCachedVideo(
                        id=uuid4().hex,
                        video_file_id=cache_file_id,
                        caption=caption,
                        title=f.media.title,
                        description=f"{f.author.name}: {f.content}",
                    )
                    if cache_file_id
                    else InlineQueryResultVideo(
                        id=uuid4().hex,
                        caption=caption,
                        title=f.media.title,
                        description=f"{f.author.name}: {f.content}",
                        mime_type="video/mp4",
                        thumbnail_url=f.media.thumbnail,
                        video_url=referer_url(f.media.urls[0], f.url),
                        video_duration=f.media.duration,
                        video_width=f.media.dimension.get("width", 0),
                        video_height=f.media.dimension.get("height", 0),
                    )
                )
            ]
        elif f.media.type == "audio":
            cache_file_id = await get_cached_media_file_id(f.media.filenames[0]) if f.media.filenames else None
            results = [
                (
                    InlineQueryResultCachedAudio(
                        id=uuid4().hex,
                        audio_file_id=cache_file_id,
                        caption=caption,
                    )
                    if cache_file_id
                    else InlineQueryResultAudio(
                        id=uuid4().hex,
                        caption=caption,
                        title=f.media.title,
                        audio_duration=f.media.duration,
                        audio_url=referer_url(f.media.urls[0], f.url),
                        performer=f.author.name,
                    )
                )
            ]
        else:
            import asyncio
            cache_file_ids = await asyncio.gather(
                *[get_cached_media_file_id(fn) for fn in f.media.filenames]
            ) if f.media.filenames else []
            results = [
                (
                    (
                        InlineQueryResultCachedGif(
                            id=uuid4().hex,
                            gif_file_id=cache_file_id,
                            caption=caption,
                            title=f"{f.author.name}: {f.content}",
                        )
                        if ".gif" in mediaurl
                        else InlineQueryResultCachedPhoto(
                            id=uuid4().hex,
                            photo_file_id=cache_file_id,
                            caption=caption,
                            title=f.author.name,
                            description=f.content,
                        )
                    )
                    if cache_file_id
                    else (
                        InlineQueryResultGif(
                            id=uuid4().hex,
                            caption=caption,
                            title=f"{f.author.name}: {f.content}",
                            gif_url=mediaurl,
                            thumbnail_url=mediaurl,
                        )
                        if ".gif" in mediaurl
                        else InlineQueryResultPhoto(
                            id=uuid4().hex,
                            caption=caption,
                            title=f.author.name,
                            description=f.content,
                            photo_url=mediaurl + "@1280w.jpg",
                            thumbnail_url=mediaurl + "@512w_512h.jpg",
                        )
                    )
                )
                for mediaurl, cache_file_id in zip(f.media.urls, cache_file_ids)
            ]
    return await inline_query_answer(inline_query, results)


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear Redis cache for given URLs."""
    message, urls = await message_to_urls(update, context)
    if message is None:
        return
    if not urls:
        await message.reply_text("链接不正确")
        return
    logger.info(f"Clear: {urls}")

    registry: ProviderRegistry = context.bot_data["provider_registry"]
    telegram_channel = context.bot_data["telegram_channel"]
    mc = telegram_channel.media_constraints

    for f in await registry.parse(urls, mc):
        if isinstance(f, Exception):
            await message.reply_text(str(f))
            continue
        for key, value in f.cache_keys.items():
            if value:
                await RedisCache().delete(value)
        await message.reply_text(f"清除缓存成功：{escape_markdown(f.url)}\n请重新获取")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel all queued tasks for the user."""
    from .uploader import UploadQueueManager

    message = update.effective_message
    if message is None:
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    upload_queue_manager: UploadQueueManager = context.bot_data["upload_queue_manager"]
    cancelled_count = await upload_queue_manager.cancel_user_tasks(user_id)

    if cancelled_count > 0:
        await message.reply_text(f"已取消 {cancelled_count} 个排队中的任务")
        logger.info(f"用户 {user_id} 通过 /cancel 命令取消了 {cancelled_count} 个任务")
    else:
        await message.reply_text("当前没有正在排队的任务")


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current tasks for the user."""
    from .uploader import UploadQueueManager

    message = update.effective_message
    if message is None:
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    upload_queue_manager: UploadQueueManager = context.bot_data["upload_queue_manager"]
    user_tasks = await upload_queue_manager.get_user_tasks(user_id)

    if user_tasks:
        await message.reply_text(
            "当前正在进行的任务:\n"
            + "\n".join(escape_markdown(task) for task in user_tasks)
        )
    else:
        await message.reply_text("当前没有正在进行的任务")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome/help message."""
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        await get_description(context), reply_markup=SOURCE_CODE_MARKUP
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception(context.error)


def add_handlers(application: Application) -> None:
    """Register all handlers on the application."""
    application.add_handler(CommandHandler("start", start, block=False))
    application.add_handler(CommandHandler("file", fetch, block=False))
    application.add_handler(CommandHandler("cover", fetch, block=False))
    application.add_handler(CommandHandler("cancel", cancel, block=False))
    application.add_handler(CommandHandler("tasks", tasks, block=False))
    application.add_handler(CommandHandler("clear", clear, block=False))
    application.add_handler(CommandHandler("video", parse, block=False))
    application.add_handler(CommandHandler("parse", parse, block=False))
    application.add_handler(
        MessageHandler(
            filters.Entity(MessageEntity.URL)
            | filters.Entity(MessageEntity.TEXT_LINK)
            | filters.Regex(BILIBILI_URL_REGEX)
            | filters.CaptionRegex(BILIBILI_URL_REGEX),
            parse,
            block=False,
        )
    )
    application.add_handler(InlineQueryHandler(inlineparse, block=False))
    application.add_error_handler(error_handler)


def run_bot(channel, provider_registry: ProviderRegistry) -> None:
    """Build and run the Telegram bot application."""
    from .uploader import UploadQueueManager

    if os.environ.get("TOKEN"):
        token = os.environ["TOKEN"]
    elif len(sys.argv) >= 2:
        token = sys.argv[1]
    else:
        logger.error("Need TOKEN.")
        sys.exit(1)

    local_mode = bool(os.environ.get("LOCAL_MODE", False))

    async def post_init(application: Application) -> None:
        await db_init()

        max_workers = int(os.environ.get("UPLOAD_WORKERS", 4))
        max_user_tasks = int(os.environ.get("MAX_USER_TASKS", 5))
        max_queue_size = int(os.environ.get("MAX_QUEUE_SIZE", 200))
        upload_queue_manager = UploadQueueManager(
            max_workers=max_workers,
            max_user_tasks=max_user_tasks,
            max_queue_size=max_queue_size,
        )
        await upload_queue_manager.start_workers()

        application.bot_data["upload_queue_manager"] = upload_queue_manager
        application.bot_data["provider_registry"] = provider_registry
        application.bot_data["telegram_channel"] = channel

        await channel.start(provider_registry)

        logger.info(
            f"上传队列管理器已启动 ({max_workers} 个 worker, 单用户任务上限: {max_user_tasks})"
        )

        await application.bot.set_my_commands(
            [
                ["start", "关于本 Bot"],
                ["parse", "获取匹配内容"],
                ["file", "获取匹配内容原始文件"],
                ["cover", "获取匹配内容原始文件预览"],
                ["video", "获取匹配清晰度视频，需参数：/video 720P BV号"],
                ["clear", "清除匹配内容缓存"],
                ["tasks", "查看当前任务"],
                ["cancel", "取消正在排队的任务"],
            ]
        )
        bot_me = await application.bot.get_me()
        logger.info(f"Bot @{bot_me.username} started.")

    async def post_shutdown(application: Application) -> None:
        upload_queue_manager: UploadQueueManager | None = application.bot_data.get(
            "upload_queue_manager"
        )
        if upload_queue_manager:
            await upload_queue_manager.stop_workers()
            logger.info("上传队列管理器已停止")

        await channel.stop()
        await db_close()

    application = (
        Application.builder()
        .defaults(
            Defaults(
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_notification=True,
                allow_sending_without_reply=True,
                block=False,
            )
        )
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .media_write_timeout(300)
        .read_timeout(60)
        .write_timeout(60)
        .base_url(os.environ.get("API_BASE_URL", "https://api.telegram.org/bot"))
        .base_file_url(
            os.environ.get("API_BASE_FILE_URL", "https://api.telegram.org/file/bot")
        )
        .local_mode(local_mode)
        .concurrent_updates(
            int(os.environ.get("SEMAPHORE_SIZE", 256))
            if os.environ.get("SEMAPHORE_SIZE")
            else True
        )
        .rate_limiter(
            AIORateLimiter(max_retries=int(os.environ.get("API_MAX_RETRIES", 5)))
        )
        .build()
    )

    add_handlers(application)

    if os.environ.get("DOMAIN"):
        application.run_webhook(
            listen=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", 9000)),
            url_path=token,
            webhook_url=f"{os.environ.get('DOMAIN')}{token}",
            max_connections=100,
        )
    else:
        application.run_polling()
