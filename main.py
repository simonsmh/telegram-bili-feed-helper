import asyncio
import json
import logging
import os
import re
import sys
from io import BytesIO

import requests
from telegram import InputMediaAnimation, InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import MessageHandler, Updater
from telegram.ext.dispatcher import run_async
from telegram.ext.filters import Filters

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger("Telegram_Bili_Feed_Helper")


def dynamic_parser(url):
    logger.info(f"解析URL: {url}")
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
        }
    )
    post = s.get(url)
    dynamic_id = re.search(r"t.bilibili.com\/(\d+)", post.url).group(1)
    logger.info(f"动态ID: {dynamic_id}")
    data = s.get(
        "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
        params={"dynamic_id": dynamic_id},
    ).json()
    detail = json.loads(data.get("data").get("card").get("card"))
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
        r"https?:\/\/t\.bilibili\.com\/\d+|https?:\/\/b23\.tv\/(?!av)\w+", data
    )

    def get_imgs(s, urls):
        async def get_img(s, url):
            imgraw = await loop.run_in_executor(None, s.get, url)
            img = BytesIO(imgraw.content)
            img.seek(0)
            while not imgraw.ok:
                asyncio.sleep(1)
            return img

        loop = asyncio.new_event_loop()
        tasks = [get_img(s, img) for img in urls]
        results = loop.run_until_complete(asyncio.gather(*tasks, loop=loop))
        loop.close()
        return results

    def callback(imgs, caption, imgraws):
        if len(imgs) == 1:
            if ".gif" in imgs[0]:
                message.reply_animation(imgraws[0], caption=caption)
            else:
                message.reply_photo(imgraws[0], caption=caption)
        else:
            media = [InputMediaPhoto(img) for img in imgraws]
            media[0].caption=caption
            message.reply_media_group(media)

    for url in urls:
        s, user, content, imgs, dynamic_id = dynamic_parser(url)
        caption = f"@{user}:\n{content}\nhttps://t.bilibili.com/{dynamic_id}"
        if not imgs:
            message.reply_text(caption)
        else:
            # try:
            #     callback(imgs, caption, imgs)
            # except BadRequest:
            logger.info("Uploading by bot")
            imgraws = get_imgs(s, imgs)
            callback(imgs, caption, imgraws)


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
                r"https?:\/\/t\.bilibili\.com\/\d+|https?:\/\/b23\.tv\/(?!av)\w+"
            ),
            parse,
        )
    )
    updater.dispatcher.add_error_handler(error)
    updater.start_polling()
    updater.idle()
