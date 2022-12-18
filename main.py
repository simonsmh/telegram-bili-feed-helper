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
    InputMediaVideo,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    Updater,
    filters,
)

from biliparser import (
    biliparser,
    db_clear,
    db_close,
    db_init,
    db_status,
    escape_markdown,
    feed,
)
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
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="原链接", url=content),
            ]
        ]
    )


@lru_cache(maxsize=16)
def captions(f: Union[feed, Exception], fallback: bool = False) -> str:
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
    captions = (
        f.url
        if fallback
        else (escape_markdown(f.url) if not f.extra_markdown else f.extra_markdown)
    ) + "\n"  # I don't need url twice with extra_markdown
    if f.user:
        captions += (f.user if fallback else f.user_markdown) + ":\n"
    if f.content:
        captions += (
            parser_helper(f.content, False)
            if fallback
            else parser_helper(f.content_markdown)
        ) + "\n"
    if f.replycontent and f.replycontent.get("data") and f.comment:
        captions += "〰〰〰〰〰〰〰〰〰〰\n" + (
            parser_helper(f.comment, False)
            if fallback
            else parser_helper(f.comment_markdown)
        )
    return captions


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


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    await message.reply_chat_action(ChatAction.TYPING)
    data = message.text
    urls = re.findall(regex, data)
    logger.info(f"Parse: {urls}")

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
                tasks = [get_media(f, img, size=1280) for img in f.mediaurls]
                media = await asyncio.gather(*tasks)
                logger.info(f"上传中: {f.url}")
            else:
                if f.mediatype == "image":
                    media = [
                        i if ".gif" in i else i + "@1280w.jpg" for i in f.mediaurls
                    ]
                else:
                    media = f.mediaurls
            if f.mediatype == "video":
                await message.reply_video(
                    media[0],
                    caption=captions(f, fallback),
                    parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    allow_sending_without_reply=True,
                    reply_markup=origin_link(f.url),
                    supports_streaming=True,
                    thumb=mediathumb,
                )
            elif f.mediatype == "audio":
                await message.reply_audio(
                    media[0],
                    caption=captions(f, fallback),
                    duration=f.mediaduration,
                    parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    performer=f.user,
                    allow_sending_without_reply=True,
                    reply_markup=origin_link(f.url),
                    thumb=mediathumb,
                    title=f.mediatitle,
                )
            elif len(f.mediaurls) == 1:
                if ".gif" in f.mediaurls[0]:
                    await message.reply_animation(
                        media[0],
                        caption=captions(f, fallback),
                        parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                    )
                else:
                    await message.reply_photo(
                        media[0],
                        caption=captions(f, fallback),
                        parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                    )
            else:
                media = [
                    InputMediaVideo(
                        img,
                        caption=captions(f, fallback),
                        parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    )
                    if ".gif" in mediaurl
                    else InputMediaPhoto(
                        img,
                        caption=captions(f, fallback),
                        parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    )
                    for img, mediaurl in zip(media, f.mediaurls)
                ]
                await message.reply_media_group(media, allow_sending_without_reply=True)
                await message.reply_text(
                    captions(f, fallback),
                    parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                    allow_sending_without_reply=True,
                    reply_markup=origin_link(f.url),
                )

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
                if "Can't parse" in err.message:
                    logger.info(f"{err} 第{i}次异常->去除Markdown: {f.url}")
                    markdown_fallback = True
                else:
                    logger.info(f"{err} 第{i}次异常->下载后上传: {f.url}")
                    f.mediaraws = True
            except RetryAfter as err:
                await asyncio.sleep(1)
            except httpx.RequestError as err:
                logger.exception(err)
                logger.info(f"{err} 第{i}次异常->重试: {f.url}")
            except httpx.HTTPStatusError as err:
                logger.exception(err)
                logger.info(f"{err} 第{i}次异常->跳过： {f.url}")
                break
            else:
                break


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    await message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
    data = message.text
    urls = re.findall(regex, data)
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
                        caption=captions(f),
                        parse_mode=ParseMode.MARKDOWN_V2,
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                    )
                except BadRequest as err:
                    logger.exception(err)
                    logger.info(f"{err} -> 去除Markdown: {f.url}")
                    await message.reply_document(
                        document=medias[0],
                        caption=captions(f, True),
                        allow_sending_without_reply=True,
                        reply_markup=origin_link(f.url),
                    )


async def inlineparse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await inline_query.answer(helpmsg)
        return
    try:
        url = re.search(regex, query).group(0)
    except AttributeError:
        await inline_query.answer(helpmsg)
        return
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
                            caption=captions(f, fallback),
                            title=f.user,
                            description=f.content,
                            mime_type="video/mp4",
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            reply_markup=origin_link(f.url),
                            thumb_url=f.mediathumb,
                            video_url=f.mediaurls[0],
                        )
                    ]
                if f.mediatype == "audio":
                    results = [
                        InlineQueryResultAudio(
                            id=str(uuid4()),
                            caption=captions(f, fallback),
                            title=f.mediatitle,
                            description=f.content,
                            audio_duration=f.mediaduration,
                            audio_url=f.mediaurls[0],
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            performer=f.user,
                            reply_markup=origin_link(f.url),
                            thumb_url=f.mediathumb,
                        )
                    ]
                else:
                    results = [
                        InlineQueryResultGif(
                            id=str(uuid4()),
                            caption=captions(f, fallback),
                            title=f"{f.user}: {f.content}",
                            gif_url=img,
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            reply_markup=origin_link(f.url),
                            thumb_url=img,
                        )
                        if ".gif" in img
                        else InlineQueryResultPhoto(
                            id=str(uuid4()),
                            caption=captions(f, fallback),
                            title=f.user,
                            description=f.content,
                            parse_mode=None if fallback else ParseMode.MARKDOWN_V2,
                            photo_url=img + "@1280w.jpg",
                            reply_markup=origin_link(f.url),
                            thumb_url=img + "@512w_512h.jpg",
                        )
                        for img in f.mediaurls
                    ]
                    results.extend(helpmsg)
            await inline_query.answer(results)

        try:
            await answer_results(f)
        except BadRequest as err:
            logger.exception(err)
            logger.info(f"{err} -> 去除Markdown: {f.url}")
            await answer_results(f, True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_me = await context.bot.get_me()
    await update.effective_message.reply_text(
        f"欢迎使用 @{bot_me.username} 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组自动匹配消息。",
        reply_markup=sourcecodemarkup,
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    await message.reply_chat_action(ChatAction.TYPING)
    result = await db_status()
    await message.reply_text(result)


async def delete_cache(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    await message.reply_chat_action(ChatAction.TYPING)
    data = message.text
    data_list = data.split(" ")
    if len(data_list) > 1:
        result = await db_clear(data_list[1])
        await message.reply_text(result)


async def post_init(application: Application):
    await db_init()
    await application.updater.bot.set_my_commands(
        [["start", "关于本 Bot"], ["file", "获取匹配内容原始文件"], ["parse", "获取匹配内容"]]
    )
    bot_me = await application.updater.bot.get_me()
    logger.info(f"Bot @{bot_me.username} started.")


async def post_shutdown(application: Application):
    await db_close()


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
        .build()
    )
    application.add_handler(
        CommandHandler("start", start, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler(
            "delete_cache",
            delete_cache,
            filters=filters.ChatType.PRIVATE,
        )
    )
    application.add_handler(
        CommandHandler("status", status, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(CommandHandler("file", fetch))
    application.add_handler(CommandHandler("parse", parse))
    application.add_handler(MessageHandler(filters.Regex(regex), parse))
    application.add_handler(InlineQueryHandler(inlineparse))
    if os.environ.get("DOMAIN"):
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 9000)),
            url_path=TOKEN,
            webhook_url=f'{os.environ.get("DOMAIN")}{TOKEN}',
        )
    else:
        application.run_polling()
