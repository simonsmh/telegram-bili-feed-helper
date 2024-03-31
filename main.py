import asyncio
import os
import pathlib
import re
import sys
import time
from functools import lru_cache
from io import BytesIO
from typing import Union
from uuid import uuid4

import httpx
import pytz
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultAudio,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    MessageEntity,
    Update,
)
from telegram.constants import ChatAction, MessageLimit, ParseMode
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
from biliparser.model import Feed
from biliparser.database import cache_clear, db_close, db_init, db_status
from biliparser.utils import (
    LOCAL_MODE,
    compress,
    escape_markdown,
    headers,
    logger,
    referer_url,
)

regex = r"(?i)(?:https?://)?[\w\.]*?(?:bilibili(?:bb)?\.com|(?:b23(?:bb)?|acg)\.tv)\S+|BV\w{10}"
share_link_regex = r"(?i)【.*】 https://[\w\.]*?(?:bilibili\.com|b23\.tv)\S+"

sourcecodemarkup = InlineKeyboardMarkup(
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
    return f"欢迎使用 @{bot_me.username} 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组自动匹配消息。\nInline 模式限制: 只可发单张图，消耗设备流量，安全性低\n群组模式限制: 图片小于10M，视频小于50M，通过 Bot 上传速度较慢"


@lru_cache(maxsize=16)
def captions(
    f: Union[Feed, Exception], fallback: bool = False, is_caption: bool = False
) -> str:
    def parser_helper(content: str, md_flag: bool = True) -> str:
        if not content:
            return str()
        ## Refine cn tag style display: #abc# -> #abc
        if md_flag:
            content = re.sub(r"\\#((?:(?!\\#).)+)\\#", r"\\#\1 ", content)
        else:
            content = re.sub(r"#((?:(?!#).)+)#", r"#\1 ", content)
        return content

    if isinstance(f, Exception):
        return escape_markdown(f.__str__())
    caption = (
        f.url
        if fallback
        else (escape_markdown(f.url) if not f.extra_markdown else f.extra_markdown)
    ) + "\n"  # I don't need url twice with extra_markdown
    if f.user:
        caption += (f.user if fallback else f.user_markdown) + ":\n"
    prev_caption = caption
    if f.content:
        caption += (
            parser_helper(f.content, False)
            if fallback
            else parser_helper(f.content_markdown)
        ) + "\n"
    if is_caption and len(caption) > MessageLimit.CAPTION_LENGTH:
        return prev_caption
    prev_caption = caption
    if isinstance(f.replycontent, dict) and f.replycontent.get("data") and f.comment:
        caption += "〰〰〰〰〰〰〰〰〰〰\n" + (
            parser_helper(f.comment, False)
            if fallback
            else parser_helper(f.comment_markdown)
        )
    if is_caption and len(caption) > MessageLimit.CAPTION_LENGTH:
        return prev_caption
    return caption


async def get_media(
    client: httpx.AsyncClient,
    f: Feed,
    url: str,
    compression: bool = True,
    size: int = 320,
    media_check_ignore: bool = False,
) -> bytes | pathlib.Path:
    header = headers.copy()
    header["Referer"] = f.url
    async with client.stream("GET", url, headers=header) as response:
        if response.status_code != 200:
            raise NetworkError(
                f"媒体文件获取错误: {response.status_code} {url}->{f.url}"
            )
        mediatype = response.headers.get("content-type").split("/")
        if mediatype[0] in ["video", "audio", "application"]:
            if not os.path.exists(".tmp"):
                os.mkdir(".tmp")
            filename = f"{time.time()}.{mediatype[1]}"
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
            raise NetworkError(f"媒体文件类型错误: {mediatype} {url}->{f.url}")
        return media


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.channel_post
    if message is None:
        return
    data = message.text or message.caption
    if data is None:
        return
    urls = re.findall(regex, data)
    if message.entities:
        for entity in message.entities:
            if entity.url:
                urls.extend(re.findall(regex, entity.url))
    if not urls:
        return
    logger.info(f"Parse: {urls}")
    try:
        await message.reply_chat_action(ChatAction.TYPING)
    except:
        pass

    async def parse_send(f: Feed, fallback: bool = False) -> None:
        if not f.mediaurls:
            await message.reply_text(
                captions(f, fallback),
                parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                reply_markup=origin_link(f.url),
            )
        else:
            medias = []
            try:
                async with httpx.AsyncClient(
                    http2=True, timeout=90, follow_redirects=True
                ) as client:
                    if f.mediaraws:
                        mediathumb = (
                            await get_media(client, f, f.mediathumb, size=320)
                            if f.mediathumb
                            else None
                        )
                        tasks = [
                            get_media(client, f, img, size=1280) for img in f.mediaurls
                        ]
                        media = await asyncio.gather(*tasks)
                        logger.info(f"上传中: {f.url}")
                    else:
                        mediathumb = referer_url(f.mediathumb, f.url)
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
                        await message.reply_video(
                            media[0],
                            caption=captions(f, fallback, True),
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
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
                        await message.reply_audio(
                            media[0],
                            caption=captions(f, fallback, True),
                            duration=f.mediaduration,
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            performer=f.user,
                            reply_markup=origin_link(f.url),
                            thumbnail=mediathumb,
                            title=f.mediatitle,
                            write_timeout=60,
                            filename=f.mediafilename[0],
                        )
                    elif len(f.mediaurls) == 1:
                        if ".gif" in f.mediaurls[0]:
                            await message.reply_animation(
                                media[0],
                                caption=captions(f, fallback, True),
                                parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                                reply_markup=origin_link(f.url),
                                write_timeout=60,
                                filename=f.mediafilename[0],
                            )
                        else:
                            await message.reply_photo(
                                media[0],
                                caption=captions(f, fallback, True),
                                parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                                reply_markup=origin_link(f.url),
                                write_timeout=60,
                                filename=f.mediafilename[0],
                            )
                    else:
                        await message.reply_media_group(
                            media=[
                                (
                                    InputMediaVideo(
                                        img,
                                        caption=captions(f, fallback, True),
                                        parse_mode=(
                                            None if fallback else ParseMode.MARKDOWN_V2
                                        ),
                                        filename=f.mediafilename[0],
                                        supports_streaming=True,
                                    )
                                    if ".gif" in mediaurl
                                    else InputMediaPhoto(
                                        img,
                                        caption=captions(f, fallback, True),
                                        parse_mode=(
                                            None if fallback else ParseMode.MARKDOWN_V2
                                        ),
                                        filename=f.mediafilename[0],
                                    )
                                )
                                for img, mediaurl in zip(media, f.mediaurls)
                            ],
                            write_timeout=60,
                        )
                        await message.reply_text(
                            captions(f, fallback),
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            reply_markup=origin_link(f.url),
                        )
                    medias = [mediathumb, *media]
            finally:
                for item in medias:
                    if isinstance(item, pathlib.Path):
                        os.remove(item)

    fs = await biliparser(urls)
    for num, f in enumerate(fs):
        markdown_fallback = False
        for i in range(1, 5):
            if isinstance(f, Exception):
                logger.warning(f"解析错误! {f}")
                if data.startswith("/parse"):
                    await message.reply_text(
                        captions(f),
                    )
                break
            try:
                # for link sharing privacy
                if i == 1 and len(urls) == 1:
                    # try to delete only if bot have delete permission and this message is only for sharing
                    match = re.match(share_link_regex, data)
                    if urls[0] == data or (match and match.group(0) == data):
                        await message.delete()
            except:
                pass
            try:
                await parse_send(f, markdown_fallback)
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
                elif "Can't parse" in err.message:
                    logger.error(f"{err} 第{i}次异常->去除Markdown: {f.url}")
                    markdown_fallback = True
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
                break
            f = (await biliparser(f.url))[0]  # 重试获取该条链接信息


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.channel_post
    if message is None:
        return
    data = message.text
    if data is None:
        return
    urls = re.findall(regex, data)
    if message.entities:
        for entity in message.entities:
            if entity.url:
                urls.extend(re.findall(regex, entity.url))
    if not urls:
        return
    logger.info(f"Fetch: {urls}")
    fs = await biliparser(urls)
    for num, f in enumerate(fs):
        if isinstance(f, Exception):
            logger.warning(f"解析错误! {f}")
            await message.reply_text(
                captions(f),
            )
            continue
        if f.mediaurls:
            medias = []
            try:
                async with httpx.AsyncClient(
                    http2=True, timeout=90, follow_redirects=True
                ) as client:
                    tasks = [
                        get_media(
                            client, f, img, compression=False, media_check_ignore=True
                        )
                        for img in f.mediaurls
                    ]
                    medias = await asyncio.gather(*tasks)
                    logger.info(f"上传中: {f.url}")
                    if len(medias) > 1:
                        medias = [
                            InputMediaDocument(media, filename=filename)
                            for media, filename in zip(medias, f.mediafilename)
                        ]
                        await message.reply_media_group(
                            medias,
                            write_timeout=60,
                        )
                        try:
                            await message.reply_text(
                                captions(f),
                                reply_markup=origin_link(f.url),
                            )
                        except BadRequest as err:
                            logger.exception(err)
                            logger.info(f"{err} -> 去除Markdown: {f.url}")
                            await message.reply_text(
                                captions(f, True),
                                reply_markup=origin_link(f.url),
                            )
                    else:
                        try:
                            await message.reply_document(
                                document=medias[0],
                                caption=captions(f, False, True),
                                reply_markup=origin_link(f.url),
                                write_timeout=60,
                                filename=f.mediafilename[0],
                            )
                        except BadRequest as err:
                            logger.exception(err)
                            logger.info(f"{err} -> 去除Markdown: {f.url}")
                            await message.reply_document(
                                document=medias[0],
                                caption=captions(f, True, True),
                                reply_markup=origin_link(f.url),
                                write_timeout=60,
                                filename=f.mediafilename[0],
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
            description="将 Bot 添加到群组可以自动匹配消息, 请注意 Inline 模式存在限制: 只可发单张图，消耗设备流量。",
            reply_markup=sourcecodemarkup,
            input_message_content=InputTextMessageContent(
                await get_description(context)
            ),
        )
    ]
    if not query:
        await inline_query_answer(inline_query, helpmsg)
        return
    url_re = re.search(regex, query)
    if url_re is None:
        await inline_query_answer(inline_query, helpmsg)
        return
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
                input_message_content=InputTextMessageContent(
                    captions(f),
                ),
            )
        ]
        await inline_query_answer(inline_query, results)
    else:

        async def answer_results(f: Feed, fallback: bool = False):
            if not f.mediaurls:
                results = [
                    InlineQueryResultArticle(
                        id=uuid4().hex,
                        title=f.user,
                        description=f.content,
                        reply_markup=origin_link(f.url),
                        input_message_content=InputTextMessageContent(
                            captions(f, fallback),
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                        ),
                    )
                ]
            else:
                if f.mediatype == "video":
                    results = [
                        InlineQueryResultVideo(
                            id=uuid4().hex,
                            caption=captions(f, fallback, True),
                            title=f.mediatitle,
                            description=f"{f.user}: {f.content}",
                            mime_type="video/mp4",
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
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
                        ),
                    ]
                elif f.mediatype == "audio":
                    results = [
                        InlineQueryResultAudio(
                            id=uuid4().hex,
                            caption=captions(f, fallback, True),
                            title=f.mediatitle,
                            audio_duration=f.mediaduration,
                            audio_url=referer_url(f.mediaurls[0], f.url),
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            performer=f.user,
                            reply_markup=origin_link(f.url),
                        ),
                    ]
                else:
                    results = [
                        (
                            InlineQueryResultGif(
                                id=uuid4().hex,
                                caption=captions(f, fallback, True),
                                title=f"{f.user}: {f.content}",
                                gif_url=img,
                                parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                                reply_markup=origin_link(f.url),
                                thumbnail_url=img,
                            )
                            if ".gif" in img
                            else InlineQueryResultPhoto(
                                id=uuid4().hex,
                                caption=captions(f, fallback, True),
                                title=f.user,
                                description=f.content,
                                parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                                photo_url=img + "@1280w.jpg",
                                reply_markup=origin_link(f.url),
                                thumbnail_url=img + "@512w_512h.jpg",
                            )
                        )
                        for img in f.mediaurls
                    ]
            await inline_query_answer(inline_query, results)

        try:
            await answer_results(f)
        except BadRequest as err:
            if "Can't parse" in err.message:
                logger.info(f"{err} -> 去除Markdown: {f.url}")
                await answer_results(f, True)
            else:
                logger.exception(err)
        except Exception as err:
            logger.exception(err)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception(context.error)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        await get_description(context),
        reply_markup=sourcecodemarkup,
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    result = await db_status()
    await message.reply_text(result)


async def clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cache_clear()
    await status(update, context)


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
            | filters.Regex(regex)
            | filters.CaptionRegex(regex),
            parse,
            block=False,
        )
    )
    application.add_handler(InlineQueryHandler(inlineparse, block=False))
    application.add_handler(CommandHandler("parse", parse, block=False))
    application.add_handler(CommandHandler("file", fetch, block=False))
    application.add_handler(
        CommandHandler("status", status, filters=filters.ChatType.PRIVATE, block=False)
    )
    application.add_handler(
        CommandHandler(
            "clear", clear_cache, filters=filters.ChatType.PRIVATE, block=False
        )
    )
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
