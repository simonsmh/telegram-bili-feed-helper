import asyncio
import json
import logging
import os
import re
import sys
from io import BytesIO
from uuid import uuid4

import requests
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InputMediaPhoto,
    InputTextMessageContent,
)
from telegram.error import BadRequest, TimedOut
from telegram.ext import InlineQueryHandler, MessageHandler, Updater
from telegram.ext.dispatcher import run_async
from telegram.ext.filters import Filters

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger("Telegram_Bili_Feed_Helper")


def dynamic_parser(url):
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
        }
    )
    post = s.get(url)
    dynamic_id = re.search(r"t\.bilibili\.com.*\/(\d+)", post.url).group(1)
    logger.info(f"动态ID: {dynamic_id}")
    data = s.get(
        "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
        params={"dynamic_id": dynamic_id},
    ).json()
    try:
        detail = json.loads(data.get("data").get("card").get("card"))
    except AttributeError:
        return
    logger.debug(f"动态解析: {detail}")
    user = detail.get("user").get("name", detail.get("user").get("uname"))
    content = detail.get("item").get("description", detail.get("item").get("content"))
    imgs = (
        [t.get("img_src") for t in detail.get("item").get("pictures")]
        if detail.get("item").get("pictures")
        else []
    )
    logger.debug(f"用户: {user}\n内容: {content}\n图片: {imgs}")
    return s, user, content, imgs, dynamic_id


@run_async
def parse(update, context):
    message = update.message
    data = message.text
    urls = re.findall(
        r"https?:\/\/t\.bilibili\.com[\/\w]*\/\d+|https?:\/\/b23\.tv\/(?!av)\w+", data,
    )
    logger.info(f"Parse: {urls}")

    async def get_img(s, url):
        imgraw = await loop.run_in_executor(None, s.get, url)
        img = BytesIO(imgraw.content)
        img.seek(0)
        while not imgraw.ok:
            asyncio.sleep(1)
        return img

    def callback(caption, dynamic_url, reply_markup, imgs, imgraws):
        if len(imgs) == 1:
            if ".gif" in imgs[0]:
                message.reply_animation(
                    imgraws[0], caption=caption, reply_markup=reply_markup
                )
            else:
                message.reply_photo(
                    imgraws[0], caption=caption, reply_markup=reply_markup
                )
        else:
            media = [InputMediaPhoto(img) for img in imgraws]
            message.reply_media_group(media)
            message.reply_text(caption, reply_markup=reply_markup, quote=False)

    async def parse_queue(url):
        try:
            s, user, content, imgs, dynamic_id = dynamic_parser(url)
        except TypeError:
            logger.warning("解析错误！")
            return
        dynamic_url = f"https://t.bilibili.com/{dynamic_id}"
        caption = f"@{user}:\n{content}"
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="动态源地址", url=dynamic_url)]]
        )
        if imgs:
            try:
                callback(caption, dynamic_url, reply_markup, imgs, imgs)
            except (TimedOut, BadRequest) as err:
                logger.info(f"{err} -> 下载中: {dynamic_id}")
                tasks = [get_img(s, img) for img in imgs]
                imgraws = await asyncio.gather(*tasks)
                logger.info(f"上传中: {dynamic_id}")
                callback(caption, dynamic_url, reply_markup, imgs, imgraws)
        else:
            message.reply_text(caption, reply_markup=reply_markup)

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
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="源代码",
                            url="https://github.com/simonsmh/telegram-bili-feed-helper",
                        )
                    ]
                ]
            ),
            input_message_content=InputTextMessageContent(
                "欢迎使用 @bilifeedbot 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组自动匹配消息。"
            ),
        )
    ]
    if not query:
        inline_query.answer(helpmsg)
        return
    try:
        url = re.search(
            r"https?:\/\/t\.bilibili\.com[\/\w]*\/\d+|https?:\/\/b23\.tv\/(?!av)\w+",
            query,
        ).group(0)
    except AttributeError:
        inline_query.answer(helpmsg)
        return
    logger.info(f"Inline: {url}")
    try:
        _, user, content, imgs, dynamic_id = dynamic_parser(url)
    except TypeError:
        logger.warning("解析错误！")
        return
    dynamic_url = f"https://t.bilibili.com/{dynamic_id}"
    caption = f"@{user}:\n{content}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="动态源地址", url=dynamic_url)]]
    )
    if not imgs:
        results = [
            InlineQueryResultArticle(
                id=uuid4(),
                title=user,
                description=caption,
                reply_markup=reply_markup,
                input_message_content=InputTextMessageContent(caption),
            )
        ]
    else:
        results = [
            InlineQueryResultGif(
                id=uuid4(),
                caption=caption,
                title=user,
                description=caption,
                reply_markup=reply_markup,
                gif_url=img,
                thumb_url=img,
            )
            if ".gif" in img
            else InlineQueryResultPhoto(
                id=uuid4(),
                caption=caption,
                title=user,
                description=caption,
                reply_markup=reply_markup,
                photo_url=img,
                thumb_url=img + "@428w_428h_1e_1c.png",
            )
            for img in imgs
        ]
        if len(imgs) == 1:
            results.extend(helpmsg)
    inline_query.answer(results)


@run_async
def error(update, context):
    logger.warning(f"Update {context} caused error {error}")


def load_json(filename="config.json"):
    try:
        with open(filename, "r") as file:
            config = json.load(file)
    except FileNotFoundError:
        try:
            filename = f"{os.path.split(os.path.realpath(__file__))[0]}/{filename}"
            with open(filename, "r") as file:
                config = json.load(file)
        except FileNotFoundError:
            logger.exception(f"Cannot find {filename}.")
            sys.exit(1)
    logger.info(f"Json: Loaded {filename}")
    return config


if __name__ == "__main__":
    if len(sys.argv) >= 2 and os.path.exists(sys.argv[1]):
        config = load_json(sys.argv[1])
    else:
        config = load_json()
    updater = Updater(config.get("TOKEN"), use_context=True)
    updater.dispatcher.add_handler(
        MessageHandler(
            Filters.regex(
                r"https?:\/\/t\.bilibili\.com[\/\w]*\/\d+|https?:\/\/b23\.tv\/(?!av)\w+"
            ),
            parse,
        )
    )
    updater.dispatcher.add_handler(InlineQueryHandler(inlineparse))
    updater.dispatcher.add_error_handler(error)
    updater.start_polling()
    updater.idle()
