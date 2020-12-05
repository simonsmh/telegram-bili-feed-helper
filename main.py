import asyncio
import os
import re
import sys
from functools import lru_cache
from io import BytesIO
from typing import IO, Union
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
    InputTextMessageContent,
    ParseMode,
)
from telegram.error import BadRequest, TimedOut
from telegram.ext import (
    CommandHandler,
    Filters,
    InlineQueryHandler,
    MessageHandler,
    Updater,
)
from telegram.ext.callbackcontext import CallbackContext
from telegram.ext.filters import Filters
from telegram.update import Update

from biliparser import biliparser, feed
from utils import compress, headers, logger

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


def origin_link(content: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(text="原链接", url=content)]])


@lru_cache(maxsize=16)
def captions(f: Union[feed, Exception]) -> str:
    def parser_helper(content: str) -> str:
        charegex = r"\W"
        content = re.sub(
            r"\\#([^#]+)\\#?",
            lambda x: f"\\#{re.sub(charegex, '', x.group(1))} ",
            content,
        )
        return content

    if isinstance(f, Exception):
        return parser_helper(f.__str__())
    captions = f"{f.user_markdown}:\n"
    if f.content_markdown:
        captions += f.content_markdown
    if f.comment_markdown:
        captions += f"\n\\-\\-\\-\\-\\-\\-\n{f.comment_markdown}"
    return parser_helper(captions)


async def get_media(
    f: feed, url: str, compression: bool = True, size: int = 320, filename: str = None
) -> IO[bytes]:

    async with httpx.AsyncClient(
        headers=headers, http2=True, timeout=None, verify=False
    ) as client:
        r = await client.get(url, headers={"Referer": f.url})
        media = BytesIO(r.read())
        mediatype = r.headers.get("content-type")
    if compression:
        if mediatype in ["image/jpeg", "image/png"]:
            logger.info(f"压缩: {url} {mediatype}")
            media = compress(media, size)
    if filename:
        media.name = filename
    media.seek(0)
    return media


def parse(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    data = message.text
    urls = re.findall(regex, data)
    logger.info(f"Parse: {urls}")

    async def callback(f: feed) -> None:
        mediathumb = (
            await get_media(f, f.mediathumb, size=320) if f.mediathumb else None
        )
        if f.mediaraws:
            tasks = [get_media(f, img) for img in f.mediaurls]
            media = await asyncio.gather(*tasks)
            logger.info(f"上传中: {f.url}")
        else:
            if f.mediatype == "image":
                media = [i if ".gif" in i else i + "@1280w.jpg" for i in f.mediaurls]
            else:
                media = f.mediaurls
        if f.mediatype == "video":
            message.reply_video(
                media[0],
                caption=captions(f),
                parse_mode=ParseMode.MARKDOWN_V2,
                quote=False,
                reply_markup=origin_link(f.url),
                supports_streaming=True,
                thumb=mediathumb,
            )
        elif f.mediatype == "audio":
            message.reply_audio(
                media[0],
                caption=captions(f),
                duration=f.mediaduration,
                parse_mode=ParseMode.MARKDOWN_V2,
                performer=f.user,
                quote=False,
                reply_markup=origin_link(f.url),
                thumb=mediathumb,
                title=f.mediatitle,
            )
        elif len(f.mediaurls) == 1:
            if ".gif" in f.mediaurls[0]:
                message.reply_animation(
                    media[0],
                    caption=captions(f),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    quote=False,
                    reply_markup=origin_link(f.url),
                )
            else:
                message.reply_photo(
                    media[0],
                    caption=captions(f),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    quote=False,
                    reply_markup=origin_link(f.url),
                )
        else:
            media = [
                InputMediaPhoto(
                    img, caption=captions(f), parse_mode=ParseMode.MARKDOWN_V2
                )
                for img in media
            ]
            message.reply_media_group(media, quote=False)
            message.reply_text(
                captions(f),
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN_V2,
                quote=False,
                reply_markup=origin_link(f.url),
            )

    async def parse_queue(urls) -> None:
        fs = await biliparser(urls)
        for num, f in enumerate(fs):
            if isinstance(f, Exception):
                logger.warning(f"解析错误! {f}")
                if data.startswith("/parse"):
                    message.reply_text(
                        captions(f),
                        disable_web_page_preview=True,
                        quote=False,
                        reply_markup=origin_link(urls[num]),
                    )
                continue
            if f.mediaurls:
                try:
                    await callback(f)
                except (TimedOut, BadRequest) as err:
                    logger.exception(err)
                    logger.info(f"{err} -> 下载中: {f.url}")
                    f.mediaraws = True
                    await callback(f)
            else:
                message.reply_text(
                    captions(f),
                    disable_web_page_preview=True,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    quote=False,
                    reply_markup=origin_link(f.url),
                )

    asyncio.run(parse_queue(urls))


def fetch(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    data = message.text
    urls = re.findall(regex, data)
    logger.info(f"Fetch: {urls}")

    async def fetch_queue(urls) -> None:
        fs = await biliparser(urls)
        for num, f in enumerate(fs):
            if isinstance(f, Exception):
                logger.warning(f"解析错误! {f}")
                message.reply_text(
                    captions(f),
                    disable_web_page_preview=True,
                    quote=False,
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
                    message.reply_media_group(
                        medias,
                        quote=False,
                    )
                    message.reply_text(
                        captions(f),
                        disable_web_page_preview=True,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        quote=False,
                        reply_markup=origin_link(f.url),
                    )
                else:
                    message.reply_document(
                        document=medias[0],
                        caption=captions(f),
                        quote=False,
                        reply_markup=origin_link(f.url),
                    )

    asyncio.run(fetch_queue(urls))


def inlineparse(update: Update, context: CallbackContext) -> None:
    inline_query = update.inline_query
    query = inline_query.query
    helpmsg = [
        InlineQueryResultArticle(
            id=str(uuid4()),
            title="帮助",
            description="将 Bot 添加到群组可以自动匹配消息, Inline 模式只可发单张图。",
            reply_markup=sourcecodemarkup,
            input_message_content=InputTextMessageContent(
                "欢迎使用 @bilifeedbot 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组自动匹配消息。"
            ),
        )
    ]
    if not query:
        inline_query.answer(helpmsg)
        return
    try:
        url = re.search(regex, query).group(0)
    except AttributeError:
        inline_query.answer(helpmsg)
        return
    logger.info(f"Inline: {url}")
    [f] = asyncio.run(biliparser(url))
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
                    disable_web_page_preview=True,
                ),
            )
        ]
    else:
        if not f.mediaurls:
            results = [
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f.user,
                    description=f.content,
                    reply_markup=origin_link(f.url),
                    input_message_content=InputTextMessageContent(
                        captions(f),
                        parse_mode=ParseMode.MARKDOWN_V2,
                        disable_web_page_preview=True,
                    ),
                )
            ]
        else:
            if f.mediatype == "video":
                results = [
                    InlineQueryResultVideo(
                        id=str(uuid4()),
                        caption=captions(f),
                        title=f.user,
                        description=f.content,
                        mime_type="video/mp4",
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=origin_link(f.url),
                        thumb_url=f.mediathumb,
                        video_url=f.mediaurls[0],
                    )
                ]
            if f.mediatype == "audio":
                results = [
                    InlineQueryResultAudio(
                        id=str(uuid4()),
                        caption=captions(f),
                        title=f.mediatitle,
                        description=f.content,
                        audio_duration=f.mediaduration,
                        audio_url=f.mediaurls[0],
                        parse_mode=ParseMode.MARKDOWN_V2,
                        performer=f.user,
                        reply_markup=origin_link(f.url),
                        thumb_url=f.mediathumb,
                    )
                ]
            else:
                results = [
                    InlineQueryResultGif(
                        id=str(uuid4()),
                        caption=captions(f),
                        title=f"{f.user}: {f.content}",
                        gif_url=img,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=origin_link(f.url),
                        thumb_url=img,
                    )
                    if ".gif" in img
                    else InlineQueryResultPhoto(
                        id=str(uuid4()),
                        caption=captions(f),
                        title=f.user,
                        description=f.content,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        photo_url=img + "@1280w.jpg",
                        reply_markup=origin_link(f.url),
                        thumb_url=img + "@512w_512h.jpg",
                    )
                    for img in f.mediaurls
                ]
    inline_query.answer(results)


def start(update: Update, context: CallbackContext) -> None:
    update.effective_message.reply_text(
        f"欢迎使用 @{context.bot.get_me().username} 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组自动匹配消息。",
        reply_markup=sourcecodemarkup,
    )


if __name__ == "__main__":
    if os.environ.get("TOKEN"):
        TOKEN = os.environ["TOKEN"]
    elif len(sys.argv) >= 2:
        TOKEN = sys.argv[1]
    else:
        logger.error(f"Need TOKEN.")
        sys.exit(1)
    updater = Updater(TOKEN, use_context=True)
    updater.dispatcher.add_handler(
        CommandHandler(
            "start", start, filters=Filters.chat_type.private, run_async=True
        )
    )
    updater.dispatcher.add_handler(CommandHandler("file", fetch, run_async=True))
    updater.dispatcher.add_handler(CommandHandler("parse", parse, run_async=True))
    updater.dispatcher.add_handler(
        MessageHandler(Filters.regex(regex), parse, run_async=True)
    )
    updater.dispatcher.add_handler(InlineQueryHandler(inlineparse, run_async=True))
    if DOMAIN := os.environ.get("DOMAIN"):
        updater.start_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 8443)),
            url_path=TOKEN,
            webhook_url=DOMAIN + TOKEN,
        )
        updater.bot.setWebhook(DOMAIN + TOKEN)
    else:
        updater.start_polling()
    logger.info(f"Bot @{updater.bot.get_me().username} started.")
    updater.bot.set_my_commands(
        [["start", "关于本 Bot"], ["file", "获取匹配内容原始文件"], ["parse", "获取匹配内容"]]
    )
    updater.idle()
