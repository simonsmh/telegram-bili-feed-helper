import asyncio
import os
import pathlib
import re
import sys
from io import BytesIO
from uuid import uuid4

import httpx
import pytz
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
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    MessageEntity,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from biliparser import biliparser
from biliparser.utils import (
    LOCAL_MODE,
    compress,
    escape_markdown,
    headers,
    logger,
    referer_url,
)
from database import db_close, db_init, file_cache

BILIBILI_URL_REGEX = r"(?i)(?:https?://)?[\w\.]*?(?:bilibili(?:bb)?\.com|(?:b23(?:bb)?|acg)\.tv)\S+|BV\w{10}"
BILIBILI_SHARE_URL_REGEX = r"(?i)【.*】 https://[\w\.]*?(?:bilibili\.com|b23\.tv)\S+"

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


def origin_link(content: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="原链接", url=content),
            ]
        ]
    )


async def get_description(context: ContextTypes.DEFAULT_TYPE):
    bot_me = await context.bot.get_me()
    return f"欢迎使用 @{bot_me.username} 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组或频道自动匹配消息。\nInline 模式限制: 只可发单张图，消耗设备流量，安全性低\n群组模式限制: 图片小于10M，视频小于50M，通过 Bot 上传速度较慢"


async def get_cache_media(filename):
    file = await file_cache.get_or_none(mediafilename=filename)
    if file:
        return file.file_id


async def get_media(
    client: httpx.AsyncClient,
    referer,
    url: str,
    filename: str,
    compression: bool = True,
    size: int = 320,
    media_check_ignore: bool = False,
) -> bytes | pathlib.Path | str:
    file_id: str | None = await get_cache_media(filename)
    if file_id:
        return file_id
    header = headers.copy()
    header["Referer"] = referer
    async with client.stream("GET", url, headers=header) as response:
        if response.status_code != 200:
            raise NetworkError(
                f"媒体文件获取错误: {response.status_code} {url}->{referer}"
            )
        mediatype = response.headers.get("content-type").split("/")
        if mediatype[0] in ["video", "audio", "application"]:
            if not os.path.exists(".tmp"):
                os.mkdir(".tmp")
            media = pathlib.Path(os.path.abspath(f".tmp/{filename}"))
            with open(f".tmp/{filename}", "wb") as file:
                async for chunk in response.aiter_bytes():
                    file.write(chunk)
        elif media_check_ignore or mediatype[0] == "image":
            media = await response.aread()
            if compression and mediatype[1] in ["jpeg", "png"]:
                logger.info(f"压缩: {url} {mediatype[1]}")
                media = compress(BytesIO(media), size).getvalue()
        else:
            raise NetworkError(f"媒体文件类型错误: {mediatype} {url}->{referer}")
        return media


async def cache_media(
    mediafilename: str,
    file,
):
    if not file:
        return
    try:
        return await file_cache.update_or_create(
            mediafilename=mediafilename, defaults=dict(file_id=file.file_id)
        )
    except Exception as e:
        logger.exception(e)
        return


def message_to_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if (
        message is None
        or (
            type(message.forward_origin) == MessageOriginUser
            and (
                message.forward_origin.sender_user.is_bot
                and message.forward_origin.sender_user.username == context.bot.username
            )
        )
        or (
            type(message.forward_origin) == MessageOriginHiddenUser
            and message.forward_origin.sender_user_name == context.bot.first_name
        )
        or (
            (
                type(message.forward_origin) == MessageOriginChat
                or type(message.forward_origin) == MessageOriginChannel
            )
            and message.forward_origin.author_signature == context.bot.first_name
        )
    ):
        return message, []
    urls = re.findall(BILIBILI_URL_REGEX, message.text or message.caption or "")
    if message.entities:
        for entity in message.entities:
            if entity.url:
                urls.extend(re.findall(BILIBILI_URL_REGEX, entity.url))
    return message, urls


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, urls = message_to_urls(update, context)
    if message is None or not urls:
        return
    logger.info(f"Parse: {urls}")
    try:
        await message.reply_chat_action(ChatAction.TYPING)
    except:
        pass

    for f in await biliparser(urls):
        for i in range(1, 5):
            if isinstance(f, Exception):
                logger.warning(f"解析错误! {f}")
                if message.text and message.text.startswith("/parse"):
                    await message.reply_text(str(f))
                break
            try:
                if not f.mediaurls:
                    await message.reply_text(f.caption, reply_markup=origin_link(f.url))
                else:
                    medias = []
                    try:
                        async with httpx.AsyncClient(
                            http2=True, timeout=90, follow_redirects=True
                        ) as client:
                            if f.mediaraws:
                                mediathumb = (
                                    await get_media(
                                        client,
                                        f.url,
                                        f.mediathumb,
                                        f.mediathumbfilename,
                                        size=320,
                                    )
                                    if f.mediathumb
                                    else None
                                )
                                tasks = [
                                    get_media(client, f.url, media, filename, size=1280)
                                    for media, filename in zip(
                                        f.mediaurls, f.mediafilename
                                    )
                                ]
                                logger.info(f"下载中: {f.url}")
                                media = await asyncio.gather(*tasks)
                                logger.info(f"下载完成: {f.url}")
                            else:
                                mediathumb = (
                                    referer_url(f.mediathumb, f.url)
                                    if f.mediathumb
                                    else None
                                )
                                if f.mediatype == "image":
                                    media = [
                                        i if ".gif" in i else i + "@1280w.jpg"
                                        for i in f.mediaurls
                                    ]
                                elif f.mediatype in ["video", "audio"]:
                                    media = [referer_url(f.mediaurls[0], f.url)]
                                else:
                                    media = f.mediaurls
                            if f.mediatype == "video":
                                result = await message.reply_video(
                                    media[0],
                                    caption=f.caption,
                                    reply_markup=origin_link(f.url),
                                    supports_streaming=True,
                                    thumbnail=mediathumb,
                                    duration=f.mediaduration,
                                    write_timeout=60,
                                    filename=f.mediafilename[0],
                                    width=(
                                        f.mediadimention["height"]
                                        if f.mediadimention["rotate"]
                                        else f.mediadimention["width"]
                                    ),
                                    height=(
                                        f.mediadimention["width"]
                                        if f.mediadimention["rotate"]
                                        else f.mediadimention["height"]
                                    ),
                                )
                            elif f.mediatype == "audio":
                                result = await message.reply_audio(
                                    media[0],
                                    caption=f.caption,
                                    duration=f.mediaduration,
                                    performer=f.user,
                                    reply_markup=origin_link(f.url),
                                    thumbnail=mediathumb,
                                    title=f.mediatitle,
                                    write_timeout=60,
                                    filename=f.mediafilename[0],
                                )
                            elif len(f.mediaurls) == 1:
                                if ".gif" in f.mediaurls[0]:
                                    result = await message.reply_animation(
                                        media[0],
                                        caption=f.caption,
                                        reply_markup=origin_link(f.url),
                                        write_timeout=60,
                                        filename=f.mediafilename[0],
                                    )
                                else:
                                    result = await message.reply_photo(
                                        media[0],
                                        caption=f.caption,
                                        reply_markup=origin_link(f.url),
                                        write_timeout=60,
                                        filename=f.mediafilename[0],
                                    )
                            else:
                                result = await message.reply_media_group(
                                    [
                                        (
                                            InputMediaVideo(
                                                img,
                                                caption=f.caption,
                                                filename=filename,
                                                supports_streaming=True,
                                            )
                                            if ".gif" in mediaurl
                                            else InputMediaPhoto(
                                                img,
                                                caption=f.caption,
                                                filename=filename,
                                            )
                                        )
                                        for img, mediaurl, filename in zip(
                                            media, f.mediaurls, f.mediafilename
                                        )
                                    ],
                                    write_timeout=60,
                                )
                                await message.reply_text(
                                    f.caption, reply_markup=origin_link(f.url)
                                )
                            # store file caches
                            if isinstance(result, tuple):  # media group
                                for filename, item in zip(f.mediafilename, result):
                                    if isinstance(
                                        item.effective_attachment, tuple
                                    ):  # PhotoSize
                                        await cache_media(
                                            filename, item.effective_attachment[0]
                                        )
                                    else:
                                        await cache_media(
                                            filename, item.effective_attachment
                                        )
                            else:
                                if isinstance(
                                    result.effective_attachment, tuple
                                ):  # PhotoSize
                                    await cache_media(
                                        f.mediafilename[0],
                                        result.effective_attachment[0],
                                    )
                                else:  # others
                                    if (
                                        hasattr(
                                            result.effective_attachment, "thumbnail"
                                        )
                                        and f.mediathumbfilename
                                    ):  # mediathumb
                                        await cache_media(
                                            f.mediathumbfilename,
                                            result.effective_attachment.thumbnail,
                                        )
                                    await cache_media(
                                        f.mediafilename[0], result.effective_attachment
                                    )
                            medias = [mediathumb, *media]
                    finally:
                        for item in medias:
                            if isinstance(item, pathlib.Path):
                                os.remove(item)
            except BadRequest as err:
                if (
                    "Not enough rights to send" in err.message
                    or "Need administrator rights in the channel chat" in err.message
                ):
                    await message.chat.leave()
                    logger.warning(
                        f"{err} 第{i}次异常->权限不足, 无法发送给{'@'+message.chat.username if message.chat.username else message.chat.id}"
                    )
                    break
                elif (
                    "Topic_deleted" in err.message
                    or "Topic_closed" in err.message
                    or "Message thread not found" in err.message
                ):
                    logger.warning(
                        f"{err} 第{i}次异常->主题/话题已删除、关闭或早于加入时间，无法发送给{'@'+message.chat.username if message.chat.username else message.chat.id}"
                    )
                    break
                else:
                    logger.error(f"{err} 第{i}次异常->下载后上传: {f.url}")
                    f.mediaraws = True
                continue
            except RetryAfter as err:
                await asyncio.sleep(err.retry_after)
                logger.error(f"{err} 第{i}次异常->限流: {f.url}")
                continue
            except NetworkError as err:
                logger.error(f"{err} 第{i}次异常->服务错误: {f.url}")
            except httpx.HTTPError as err:
                logger.error(f"{err} 第{i}次异常->请求异常: {f.url}")
            except Exception as err:
                logger.exception(err)
            else:
                try:
                    # for link sharing privacy under group
                    if (
                        len(urls) == 1
                        and not update.channel_post
                        and not message.reply_to_message
                        and message.text is not None
                    ):
                        # try to delete only if bot have delete permission and this message is only for sharing
                        match = re.match(BILIBILI_SHARE_URL_REGEX, message.text)
                        if urls[0] == message.text or (
                            match and match.group(0) == message.text
                        ):
                            await message.delete()
                finally:
                    break
            f = (await biliparser(f.url))[0]  # 重试获取该条链接信息


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, urls = message_to_urls(update, context)
    if message is None or not urls:
        return
    logger.info(f"Fetch: {urls}")
    for f in await biliparser(urls):
        if isinstance(f, Exception):
            logger.warning(f"解析错误! {f}")
            await message.reply_text(str(f))
            continue
        if f.mediaurls:
            medias = []
            try:
                async with httpx.AsyncClient(
                    http2=True, timeout=90, follow_redirects=True
                ) as client:
                    tasks = [
                        get_media(
                            client,
                            f.url,
                            media,
                            filename,
                            compression=False,
                            media_check_ignore=True,
                        )
                        for media, filename in zip(f.mediaurls, f.mediafilename)
                    ]
                    medias = await asyncio.gather(*tasks)
                    logger.info(f"上传中: {f.url}")
                    if len(medias) > 1:
                        result = await message.reply_media_group(
                            [
                                InputMediaDocument(media, filename=filename)
                                for media, filename in zip(medias, f.mediafilename)
                            ],
                            write_timeout=60,
                        )
                        await message.reply_text(
                            f.caption, reply_markup=origin_link(f.url)
                        )
                        for filename, item in zip(f.mediafilename, result):
                            if isinstance(
                                item.effective_attachment, tuple
                            ):  # PhotoSize
                                await cache_media(
                                    filename, item.effective_attachment[0]
                                )
                            else:
                                await cache_media(filename, item.effective_attachment)
                    else:
                        result = await message.reply_document(
                            document=medias[0],
                            caption=f.caption,
                            reply_markup=origin_link(f.url),
                            write_timeout=60,
                            filename=f.mediafilename[0],
                        )
                        await cache_media(
                            f.mediafilename[0], result.effective_attachment
                        )
            finally:
                for item in medias:
                    if isinstance(item, pathlib.Path):
                        os.remove(item)


async def inlineparse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def inline_query_answer(inline_query: InlineQuery, msg):
        try:
            await inline_query.answer(msg, cache_time=0, is_personal=True)
        except BadRequest as err:
            if (
                "Query is too old and response timeout expired or query id is invalid"
                in err.message
            ):
                logger.error(f"{err} -> Inline请求超时: {f.url}")
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
            description="将 Bot 添加到群组或频道可以自动匹配消息, 请注意 Inline 模式存在限制: 只可发单张图，消耗设备流量。",
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
    [f] = await biliparser(url)
    if isinstance(f, Exception):
        logger.warning(f"解析错误! {f}")
        results = [
            InlineQueryResultArticle(
                id=uuid4().hex,
                title="解析错误!",
                description=escape_markdown(f.__str__()),
                input_message_content=InputTextMessageContent(str(f)),
            )
        ]
        return await inline_query_answer(inline_query, results)

    if not f.mediaurls:
        results = [
            InlineQueryResultArticle(
                id=uuid4().hex,
                title=f.user,
                description=f.content,
                reply_markup=origin_link(f.url),
                input_message_content=InputTextMessageContent(f.caption),
            )
        ]
    else:
        if f.mediatype == "video":
            cache_file_id = await get_cache_media(f.mediafilename[0])
            results = [
                InlineQueryResultCachedVideo(
                    id=uuid4().hex,
                    video_file_id=cache_file_id,
                    caption=f.caption,
                    title=f.mediatitle,
                    description=f"{f.user}: {f.content}",
                    reply_markup=origin_link(f.url),
                )
                if cache_file_id
                else InlineQueryResultVideo(
                    id=uuid4().hex,
                    caption=f.caption,
                    title=f.mediatitle,
                    description=f"{f.user}: {f.content}",
                    mime_type="video/mp4",
                    reply_markup=origin_link(f.url),
                    thumbnail_url=f.mediathumb,
                    video_url=referer_url(f.mediaurls[0], f.url),
                    video_duration=f.mediaduration,
                    video_width=(
                        f.mediadimention["height"]
                        if f.mediadimention["rotate"]
                        else f.mediadimention["width"]
                    ),
                    video_height=(
                        f.mediadimention["width"]
                        if f.mediadimention["rotate"]
                        else f.mediadimention["height"]
                    ),
                )
            ]
        elif f.mediatype == "audio":
            cache_file_id = await get_cache_media(f.mediafilename[0])
            results = [
                InlineQueryResultCachedAudio(
                    id=uuid4().hex,
                    audio_file_id=cache_file_id,
                    caption=f.caption,
                    reply_markup=origin_link(f.url),
                )
                if cache_file_id
                else InlineQueryResultAudio(
                    id=uuid4().hex,
                    caption=f.caption,
                    title=f.mediatitle,
                    audio_duration=f.mediaduration,
                    audio_url=referer_url(f.mediaurls[0], f.url),
                    performer=f.user,
                    reply_markup=origin_link(f.url),
                ),
            ]
        else:
            cache_file_ids = await asyncio.gather(
                *[get_cache_media(filename) for filename in f.mediafilename]
            )
            results = [
                (
                    (
                        InlineQueryResultCachedGif(
                            id=uuid4().hex,
                            gif_file_id=cache_file_id,
                            caption=f.caption,
                            title=f"{f.user}: {f.content}",
                            reply_markup=origin_link(f.url),
                        )
                        if ".gif" in mediaurl
                        else InlineQueryResultCachedPhoto(
                            id=uuid4().hex,
                            photo_file_id=cache_file_id,
                            caption=f.caption,
                            title=f.user,
                            description=f.content,
                            reply_markup=origin_link(f.url),
                        )
                    )
                    if cache_file_id
                    else (
                        InlineQueryResultGif(
                            id=uuid4().hex,
                            caption=f.caption,
                            title=f"{f.user}: {f.content}",
                            gif_url=mediaurl,
                            reply_markup=origin_link(f.url),
                            thumbnail_url=mediaurl,
                        )
                        if ".gif" in mediaurl
                        else InlineQueryResultPhoto(
                            id=uuid4().hex,
                            caption=f.caption,
                            title=f.user,
                            description=f.content,
                            photo_url=mediaurl + "@1280w.jpg",
                            reply_markup=origin_link(f.url),
                            thumbnail_url=mediaurl + "@512w_512h.jpg",
                        )
                    )
                )
                for mediaurl, cache_file_id in zip(f.mediaurls, cache_file_ids)
            ]
    return await inline_query_answer(inline_query, results)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception(context.error)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        await get_description(context), reply_markup=SOURCE_CODE_MARKUP
    )


async def post_init(application: Application):
    await db_init()
    await application.bot.set_my_commands(
        [
            ["start", "关于本 Bot"],
            ["file", "获取匹配内容原始文件"],
            ["parse", "获取匹配内容"],
        ]
    )
    bot_me = await application.bot.get_me()
    logger.info(f"Bot @{bot_me.username} started.")


async def post_shutdown(application: Application):
    await db_close()


def add_handler(application: Application):
    application.add_handler(CommandHandler("start", start, block=False))
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
    application.add_handler(CommandHandler("parse", parse, block=False))
    application.add_handler(CommandHandler("file", fetch, block=False))
    application.add_error_handler(error_handler)


if __name__ == "__main__":
    if os.environ.get("TOKEN"):
        TOKEN = os.environ["TOKEN"]
    elif len(sys.argv) >= 2:
        TOKEN = sys.argv[1]
    else:
        logger.error("Need TOKEN.")
        sys.exit(1)
    application = (
        Application.builder()
        .defaults(
            Defaults(
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_notification=True,
                allow_sending_without_reply=True,
                block=False,
                tzinfo=pytz.timezone("Asia/Shanghai"),
            )
        )
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .read_timeout(60)
        .write_timeout(60)
        .base_url(os.environ.get("API_BASE_URL", "https://api.telegram.org/bot"))
        .base_file_url(
            os.environ.get("API_BASE_FILE_URL", "https://api.telegram.org/file/bot")
        )
        .local_mode(bool(LOCAL_MODE))
        .concurrent_updates(
            int(os.environ.get("SEMAPHORE_SIZE", 256))
            if os.environ.get("SEMAPHORE_SIZE")
            else True
        )
        .build()
    )
    add_handler(application)
    if os.environ.get("DOMAIN"):
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 9000)),
            url_path=TOKEN,
            webhook_url=f'{os.environ.get("DOMAIN")}{TOKEN}',
            max_connections=100,
        )
    else:
        application.run_polling()
