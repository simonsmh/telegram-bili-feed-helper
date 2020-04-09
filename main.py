import asyncio
import json
import logging
import os
import re
import sys
from io import BytesIO
from uuid import uuid4

import aiohttp
from PIL import Image
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultAudio,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
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
from telegram.ext.dispatcher import run_async
from telegram.ext.filters import Filters
from telegram.utils.helpers import escape_markdown

from feedparser import feedparser, headers

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger("Telegram_Bili_Feed_Helper")

regex = r"(?i)https?:\/\/(?:vc\.bilibili\.com[\D]*\d+|[th]\.bilibili\.com[\/\w]*\/\d+|b23\.tv\/(?!ep)\w+|(?:www\.|m\.)?(?:bilibili\.com/audio/au\d+|(?:bilibili\.com/video|acg\.tv)/(?:av\d+|bv\w+))|live\.bilibili\.com/\d+)"

sourcecodemarkup = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                text="源代码", url="https://github.com/simonsmh/telegram-bili-feed-helper",
            )
        ]
    ]
)


def tag_parser(content):
    return re.sub(r"#([^#?=\s|$]+)#?", lambda x: f"#{x.group(1)} ", content)


@run_async
def parse(update, context):
    if not (message := update.channel_post):
        message = update.message
    data = message.text
    urls = re.findall(regex, data)
    logger.info(f"Parse: {urls}")

    async def get_img(f, url, size=1280, compression=True):
        def compress(inpil):
            pil = Image.open(inpil)
            pil.thumbnail((size, size), Image.LANCZOS)
            outpil = BytesIO()
            pil.save(outpil, "JPEG", optimize=True)
            logger.info(f"压缩比: {inpil.__sizeof__()/outpil.__sizeof__()}")
            return outpil

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, headers={"Referer": f.url}) as resp:
                img = BytesIO(await resp.read())
        if compression:
            if ".jpg" in url:
                img = compress(img)
        img.seek(0)
        return img

    async def callback(caption, reply_markup, f):
        if f.mediathumb:
            mediathumb = await get_img(f, f.mediathumb, size=320)
        if f.mediaraws:
            tasks = [get_img(f, img) for img in f.mediaurls]
            media = await asyncio.gather(*tasks)
            logger.info(f"上传中: {f.url}")
        else:
            media = f.mediaurls
        if f.mediatype == "video":
            message.reply_video(
                media[0],
                caption=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup,
                supports_streaming=True,
                thumb=mediathumb,
                timeout=120,
            )
        elif f.mediatype == "audio":
            message.reply_audio(
                media[0],
                caption=caption,
                duration=f.mediaduration,
                parse_mode=ParseMode.MARKDOWN_V2,
                performer=f.user,
                reply_markup=reply_markup,
                thumb=mediathumb,
                timeout=120,
                title=f.mediatitle,
            )
        elif len(f.mediaurls) == 1:
            if ".gif" in f.mediaurls[0]:
                message.reply_animation(
                    media[0],
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup,
                    timeout=60,
                )
            else:
                message.reply_photo(
                    media[0],
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup,
                    timeout=60,
                )
        else:
            media = [
                InputMediaPhoto(img, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
                for img in media
            ]
            message.reply_media_group(media, timeout=120)
            message.reply_text(
                caption,
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN_V2,
                quote=False,
                reply_markup=reply_markup,
            )

    async def parse_queue(url):
        f = await feedparser(url)
        if not f:
            logger.warning("解析错误！")
            return
        caption = f"{f.user_markdown}:\n{tag_parser(f.content_markdown)}"
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="原链接", url=f.url)]]
        )
        if f.mediaurls:
            try:
                if f.mediatype == "picture":
                    f.mediaurls = [
                        i + "@1280w_1e_1c.jpg" if not ".gif" in i else i
                        for i in f.mediaurls
                    ]
                await callback(caption, reply_markup, f)
            except (TimedOut, BadRequest) as err:
                logger.exception(err)
                logger.info(f"{err} -> 下载中: {f.url}")
                f.mediaraws = True
                await callback(caption, reply_markup, f)
        else:
            message.reply_text(
                caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
            )

    loop = asyncio.new_event_loop()
    tasks = [parse_queue(url) for url in urls]
    loop.run_until_complete(asyncio.gather(*tasks, loop=loop))
    loop.close()


@run_async
def inlineparse(update, context):
    inline_query = update.inline_query
    query = inline_query.query
    helpmsg = [
        InlineQueryResultArticle(
            id=uuid4(),
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
    f = asyncio.run(feedparser(url))
    if not f:
        logger.warning("解析错误！")
        return
    caption = f"{f.user_markdown}:\n{tag_parser(f.content_markdown)}"
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(text="原链接", url=f.url)]])
    if not f.mediaurls:
        results = [
            InlineQueryResultArticle(
                id=uuid4(),
                title=f.user,
                description=f.content,
                reply_markup=reply_markup,
                input_message_content=InputTextMessageContent(
                    caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_web_page_preview=True,
                ),
            )
        ]
    else:
        if f.mediatype == "video":
            results = [
                InlineQueryResultVideo(
                    id=uuid4(),
                    caption=caption,
                    title=f.user,
                    description=f.content,
                    mime_type="video/mp4",
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup,
                    thumb_url=f.mediathumb,
                    video_url=f.mediaurls[0],
                )
            ]
        if f.mediatype == "audio":
            results = [
                InlineQueryResultAudio(
                    id=uuid4(),
                    caption=caption,
                    title=f.mediatitle,
                    description=f.content,
                    audio_duration=f.mediaduration,
                    audio_url=f.mediaurls[0],
                    parse_mode=ParseMode.MARKDOWN_V2,
                    performer=f.user,
                    reply_markup=reply_markup,
                    thumb_url=f.mediathumb,
                )
            ]
        else:
            results = [
                InlineQueryResultGif(
                    id=uuid4(),
                    caption=caption,
                    title=f"{f.user}: {f.content}",
                    gif_url=img,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup,
                    thumb_url=img,
                )
                if ".gif" in img
                else InlineQueryResultPhoto(
                    id=uuid4(),
                    caption=caption,
                    title=f.user,
                    description=f.content,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    photo_url=img + "@1280w_1e_1c.jpg",
                    reply_markup=reply_markup,
                    thumb_url=img + "@512w_512h_1e_1c.jpg",
                )
                for img in f.mediaurls
            ]
        if len(results) == 1:
            results.extend(helpmsg)
    inline_query.answer(results)


@run_async
def error(update, context):
    logger.warning(f"Update {context} caused error {error}")


@run_async
def start(update, context):
    update.message.reply_text(
        "欢迎使用 @bilifeedbot 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组自动匹配消息。",
        reply_markup=sourcecodemarkup,
    )


if __name__ == "__main__":
    if os.environ.get("TOKEN"):
        TOKEN = os.environ.get("TOKEN")
    elif len(sys.argv) >= 2:
        TOKEN = sys.argv[1]
    else:
        logger.exception(f"Need TOKEN.")
        sys.exit(1)
    logger.info("Starting...")
    updater = Updater(TOKEN, use_context=True)
    updater.dispatcher.add_handler(
        CommandHandler("start", start, filters=Filters.private)
    )
    updater.dispatcher.add_handler(MessageHandler(Filters.regex(regex), parse))
    updater.dispatcher.add_handler(InlineQueryHandler(inlineparse))
    updater.dispatcher.add_error_handler(error)
    updater.start_polling()
    updater.idle()
