import asyncio
import os
import pathlib
import re
import sys
import time
from functools import lru_cache
from io import BytesIO
from typing import IO, Optional, Union
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
from telegram.error import BadRequest, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from biliparser import biliparser, feed
from database import cache_clear, db_close, db_init, db_status
from utils import LOCAL_MODE, compress, escape_markdown, headers, logger

regex = r"(?i)[\w\.]*?(?:bilibili\.com|(?:b23|acg)\.tv)\S+"


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

client = httpx.AsyncClient(headers=headers, http2=True, timeout=60, verify=False)


def origin_link(content: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="原链接", url=content),
            ]
        ]
    )


def referer_url(url, referer):
    if not referer:
        return url
    params = {
        "url": url,
        "referer": referer,
    }
    final = f"https://referer.simonsmh.workers.dev/?{urlencode(params)}"
    logger.debug(final)
    return final


async def get_description(context: ContextTypes.DEFAULT_TYPE):
    bot_me = await context.bot.get_me()
    return f"欢迎使用 @{bot_me.username} 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组自动匹配消息。\nInline 模式限制: 只可发单张图，消耗设备流量，安全性低\n群组模式限制: 图片小于10M，视频小于50M，通过 Bot 上传速度较慢"


@lru_cache(maxsize=16)
def captions(
    f: Union[feed, Exception], fallback: bool = False, is_caption: bool = False
) -> str:
    def parser_helper(content: str, md_flag: bool = True) -> str:
        if not content:
            return str()
        if md_flag:
            content = re.sub(r"\\#\\#", "\\# ", content)
            content = re.sub(r"\\# ", " ", content)
            content = re.sub(r"\\#$", "", content)
        else:
            content = re.sub(r"##", "# ", content)
            content = re.sub(r"# ", " ", content)
            content = re.sub(r"#$", "", content)
        return content

    if isinstance(f, Exception):
        return f.__str__()
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
    f: feed,
    url: str,
    compression: bool = True,
    size: int = 320,
    filename: Optional[str] = None,
) -> IO[bytes]: #  | pathlib.Path
    async with client.stream("GET", url, headers={"Referer": f.url}) as response:
        mediatype = response.headers.get("content-type")
        media = BytesIO(await response.aread())
        if mediatype in ["image/jpeg", "image/png"]:
            if compression:
                logger.info(f"压缩: {url} {mediatype}")
                media = compress(media, size)
        if filename:
            media.name = filename
        media.seek(0)
        return media
        # else:
            # if not os.path.exists(".tmp"):
            #     os.mkdir(".tmp")
            # if not filename:
            #     filename = f"{time.time()}.mp4"
            # with open(f".tmp/{filename}", "wb") as file:
            #     async for chunk in response.aiter_bytes():
            #         file.write(chunk)
            # media = pathlib.Path(os.path.abspath(f".tmp/{filename}"))
            # return media


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
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
    logger.info(f"Parse: {urls}")
    try:
        await message.reply_chat_action(ChatAction.TYPING)
    except:
        pass

    async def parse_send(f: feed, fallback: bool = False) -> None:
        if not f.mediaurls:
            await message.reply_text(
                captions(f, fallback),
                parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                allow_sending_without_reply=True,
                reply_markup=origin_link(f.url),
            )
        else:
            mediathumb = (
                await get_media(f, f.mediathumb, size=320) if f.mediathumb else None
            )
            if f.mediaraws:
                tasks = [
                    get_media(f, img, size=1280, filename=filename)
                    for img, filename in zip(f.mediaurls, f.mediafilename)
                ]
                media = await asyncio.gather(*tasks)
                logger.info(f"上传中: {f.url}")
            else:
                if f.mediatype == "image":
                    media = [
                        i if ".gif" in i else i + "@1280w.jpg" for i in f.mediaurls
                    ]
                elif f.mediatype == "video":
                    media = [referer_url(f.mediaurls[0], f.url)]
                else:
                    media = f.mediaurls
            if f.mediatype == "video":
                await message.reply_video(
                    media[0],
                    caption=captions(f, fallback, True),
                    parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    allow_sending_without_reply=True,
                    reply_markup=origin_link(f.url),
                    supports_streaming=True,
                    thumbnail=mediathumb,
                    duration=f.mediaduration,
                    write_timeout=600,
                    width=f.mediadimention["height"]
                    if f.mediadimention["rotate"]
                    else f.mediadimention["width"],
                    height=f.mediadimention["width"]
                    if f.mediadimention["rotate"]
                    else f.mediadimention["height"],
                )
            elif f.mediatype == "audio":
                await message.reply_audio(
                    media[0],
                    caption=captions(f, fallback, True),
                    duration=f.mediaduration,
                    parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    performer=f.user,
                    allow_sending_without_reply=True,
                    reply_markup=origin_link(f.url),
                    thumbnail=mediathumb,
                    title=f.mediatitle,
                    write_timeout=600,
                )
            elif len(f.mediaurls) == 1:
                if ".gif" in f.mediaurls[0]:
                    await message.reply_animation(
                        media[0],
                        caption=captions(f, fallback, True),
                        parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                        write_timeout=600,
                    )
                else:
                    await message.reply_photo(
                        media[0],
                        caption=captions(f, fallback, True),
                        parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                        write_timeout=600,
                    )
            else:
                medias = [
                    InputMediaVideo(
                        img,
                        caption=captions(f, fallback, True),
                        parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    )
                    if ".gif" in mediaurl
                    else InputMediaPhoto(
                        img,
                        caption=captions(f, fallback, True),
                        parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    )
                    for img, mediaurl in zip(media, f.mediaurls)
                ]
                await message.reply_media_group(
                    media=medias,
                    allow_sending_without_reply=True,
                    write_timeout=600,
                )
                await message.reply_text(
                    captions(f, fallback),
                    parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    allow_sending_without_reply=True,
                    reply_markup=origin_link(f.url),
                )
            for item in [mediathumb, *media]:
                if isinstance(item, pathlib.Path):
                    os.remove(item)

    fs = await biliparser(urls)
    for num, f in enumerate(fs):
        if isinstance(f, Exception):
            logger.warning(f"解析错误! {f}")
            if data.startswith("/parse"):
                await message.reply_text(
                    captions(f),
                    allow_sending_without_reply=True,
                    reply_markup=origin_link(urls[num]),
                )
            continue
        markdown_fallback = False
        for i in range(1, 5):
            try:
                await parse_send(f, markdown_fallback)
            except TimedOut as err:
                logger.exception(err)
                logger.info(f"{err} 第{i}次异常->下载后上传: {f.url}")
                f.mediaraws = True
            except BadRequest as err:
                logger.exception(err)
                if "Not enough rights to send" in err.message:
                    await message.chat.leave()
                    logger.warning(
                        f"{err} 第{i}次异常->权限不足, 无法发送给{'@'+message.chat.username if message.chat.username else message.chat.id}"
                    )
                    break
                elif "Can't parse" in err.message:
                    logger.info(f"{err} 第{i}次异常->去除Markdown: {f.url}")
                    markdown_fallback = True
                else:
                    logger.info(f"{err} 第{i}次异常->下载后上传: {f.url}")
                    f.mediaraws = True
            except RetryAfter as err:
                await asyncio.sleep(2**i)
            except httpx.RequestError as err:
                logger.exception(err)
                logger.info(f"{err} 第{i}次异常->重试: {f.url}")
            except httpx.HTTPStatusError as err:
                logger.exception(err)
                logger.info(f"{err} 第{i}次异常->跳过： {f.url}")
                continue
            else:
                break


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
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
                allow_sending_without_reply=True,
                reply_markup=origin_link(urls[num]),
            )
            continue
        if f.mediaurls:
            tasks = [
                get_media(f, img, filename=filename, compression=False)
                for img, filename in zip(f.mediaurls, f.mediafilename)
            ]
            medias = await asyncio.gather(*tasks)
            logger.info(f"上传中: {f.url}")
            if len(medias) > 1:
                medias = [InputMediaDocument(media) for media in medias]
                await message.reply_media_group(
                    medias,
                    allow_sending_without_reply=True,
                    write_timeout=600,
                )
                try:
                    await message.reply_text(
                        captions(f),
                        parse_mode=ParseMode.MARKDOWN_V2,
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                    )
                except BadRequest as err:
                    logger.exception(err)
                    logger.info(f"{err} -> 去除Markdown: {f.url}")
                    await message.reply_text(
                        captions(f, True),
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                    )
            else:
                try:
                    await message.reply_document(
                        document=medias[0],
                        caption=captions(f, False, True),
                        parse_mode=ParseMode.MARKDOWN_V2,
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                        write_timeout=600,
                    )
                except BadRequest as err:
                    logger.exception(err)
                    logger.info(f"{err} -> 去除Markdown: {f.url}")
                    await message.reply_document(
                        document=medias[0],
                        caption=captions(f, True, True),
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                        write_timeout=600,
                    )
            for item in medias:
                if isinstance(item, pathlib.Path):
                    os.remove(item)


async def inlineparse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query = update.inline_query
    if inline_query is None:
        return
    query = inline_query.query
    helpmsg = [
        InlineQueryResultArticle(
            id=str(uuid4()),
            title="帮助",
            description="将 Bot 添加到群组可以自动匹配消息, 请注意 Inline 模式存在限制: 只可发单张图，消耗设备流量。",
            reply_markup=sourcecodemarkup,
            input_message_content=InputTextMessageContent(
                await get_description(context)
            ),
        )
    ]
    if not query:
        await inline_query.answer(helpmsg)
        return
    url_re = re.search(regex, query)
    if url_re is None:
        await inline_query.answer(helpmsg)
        return
    url = url_re.group(0)
    logger.info(f"Inline: {url}")
    [f] = await biliparser(url)
    if isinstance(f, Exception):
        logger.warning(f"解析错误! {f}")
        results = [
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="解析错误!",
                description=f.__str__(),
                reply_markup=origin_link(url),
                input_message_content=InputTextMessageContent(
                    captions(f),
                ),
            )
        ]
        await inline_query.answer(results)
    else:

        async def answer_results(f: feed, fallback: bool = False):
            if not f.mediaurls:
                results = [
                    InlineQueryResultArticle(
                        id=str(uuid4()),
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
                            id=str(uuid4()),
                            caption=captions(f, fallback, True),
                            title=f.user,
                            description=f.content,
                            mime_type="video/mp4",
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            reply_markup=origin_link(f.url),
                            thumbnail_url=f.mediathumb,
                            video_url=referer_url(f.mediaurls[0], f.url),
                            video_duration=f.mediaduration,
                            video_width=f.mediadimention["height"]
                            if f.mediadimention["rotate"]
                            else f.mediadimention["width"],
                            video_height=f.mediadimention["width"]
                            if f.mediadimention["rotate"]
                            else f.mediadimention["height"],
                        )
                    ]
                elif f.mediatype == "audio":
                    results = [
                        InlineQueryResultAudio(
                            id=str(uuid4()),
                            caption=captions(f, fallback, True),
                            title=f.mediatitle,
                            audio_duration=f.mediaduration,
                            audio_url=f.mediaurls[0],
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            performer=f.user,
                            reply_markup=origin_link(f.url),
                        )
                    ]
                else:
                    results = [
                        InlineQueryResultGif(
                            id=str(uuid4()),
                            caption=captions(f, fallback, True),
                            title=f"{f.user}: {f.content}",
                            gif_url=img,
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            reply_markup=origin_link(f.url),
                            thumbnail_url=img,
                        )
                        if ".gif" in img
                        else InlineQueryResultPhoto(
                            id=str(uuid4()),
                            caption=captions(f, fallback, True),
                            title=f.user,
                            description=f.content,
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            photo_url=img + "@1280w.jpg",
                            reply_markup=origin_link(f.url),
                            thumbnail_url=img + "@512w_512h.jpg",
                        )
                        for img in f.mediaurls
                    ]
            await inline_query.answer(results)

        try:
            await answer_results(f)
        except BadRequest as err:
            logger.exception(err)
            logger.info(f"{err} -> 去除Markdown: {f.url}")
            await answer_results(f, True)


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
    updater = application.updater
    if updater is None:
        return
    await updater.bot.set_my_commands(
        [["start", "关于本 Bot"], ["file", "获取匹配内容原始文件"], ["parse", "获取匹配内容"]]
    )
    bot_me = await updater.bot.get_me()
    logger.info(f"Bot @{bot_me.username} started.")


async def post_shutdown(application: Application):
    await db_close()
    await client.aclose()


def add_handler(application: Application):
    application.add_handler(
        CommandHandler("start", start, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("status", status, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("clear", clear_cache, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(CommandHandler("file", fetch))
    application.add_handler(CommandHandler("parse", parse))
    application.add_handler(
        MessageHandler(
            filters.Entity(MessageEntity.URL)
            | filters.Entity(MessageEntity.TEXT_LINK)
            | filters.Regex(regex)
            | filters.CaptionRegex(regex),
            parse,
        )
    )
    application.add_handler(InlineQueryHandler(inlineparse))


if __name__ == "__main__":
    if os.environ.get("TOKEN"):
        TOKEN = os.environ["TOKEN"]
    elif len(sys.argv) >= 2:
        TOKEN = sys.argv[1]
    else:
        logger.error(f"Need TOKEN.")
        sys.exit(1)
    application = (
        Application.builder()
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
        .build()
    )
    add_handler(application)
    if os.environ.get("DOMAIN"):
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 9000)),
            url_path=TOKEN,
            webhook_url=f'{os.environ.get("DOMAIN")}{TOKEN}',
        )
    else:
        application.run_polling()
