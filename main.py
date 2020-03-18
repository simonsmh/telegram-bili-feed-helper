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
    InlineQueryResultVideo,
    InputMediaPhoto,
    InputTextMessageContent,
    ParseMode,
)
from telegram.error import BadRequest, TimedOut
from telegram.ext import InlineQueryHandler, MessageHandler, Updater
from telegram.ext.dispatcher import run_async
from telegram.ext.filters import Filters
from telegram.utils.helpers import escape_markdown

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger("Telegram_Bili_Feed_Helper")

regex = r"https?:\/\/vc\.bilibili\.com[\D]*\d+|https?:\/\/[th]\.bilibili\.com[\/\w]*\/\d+|https?:\/\/b23\.tv\/(?!av|ep)\w+"


def dynamic_parser(url):
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
        }
    )
    post = s.get(url)
    if match := re.search(r"[th]\.bilibili\.com[\/\w]*\/(\d+)", post.url):
        if "type=2" in match.group(0) or "h.bilibili.com" in match.group(0):
            rid = match.group(1)
            data = s.get(
                "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
                params={"rid": rid, "type": 2},
            ).json()
            dynamic_id = data.get("data").get("card").get("desc").get("dynamic_id_str")
        else:
            dynamic_id = match.group(1)
            logger.info(f"动态ID: {dynamic_id}")
            data = s.get(
                "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
                params={"dynamic_id": dynamic_id},
            ).json()
        try:
            card = json.loads(data.get("data").get("card").get("card"))
        except AttributeError:
            return
        logger.debug(f"动态解析: {card}")
        if origin := card.get("origin"):
            detail = json.loads(origin)
            logger.debug(f"源动态解析: {detail}")
        else:
            detail = card
        if av_id := detail.get("aid"):
            user = detail.get("owner").get("name")
            user_markdown = f"[@{user}](https://space.bilibili.com/{detail.get('owner').get('mid')})"
            content = f"{escape_markdown(detail.get('dynamic'))}\n[{detail.get('title')}](https://b23.tv/av{av_id})"
            imgs = [detail.get("pic")]
        elif detail.get("words"):
            cv_id = detail.get("id")
            user = detail.get("author").get("name")
            user_markdown = f"[@{user}](https://space.bilibili.com/{detail.get('author').get('mid')})"
            content = f"{escape_markdown(detail.get('dynamic'))}\n[{detail.get('title')}](https://www.bilibili.com/read/cv{cv_id})"
            imgs = [detail.get("banner_url")]
        elif detail.get("typeInfo"):
            au_id = detail.get("id")
            user = detail.get("upper")
            user_markdown = (
                f"[@{user}](https://space.bilibili.com/{detail.get('upId')})"
            )
            content = f"{escape_markdown(detail.get('intro'))}\n[{detail.get('title')}](https://www.bilibili.com/audio/au{au_id})"
            imgs = [detail.get("cover")]
        else:
            user = detail.get("user").get("name")
            user_markdown = (
                f"[@{user}](https://space.bilibili.com/{detail.get('user').get('uid')})"
            )
            if content := detail.get("item").get("description"):
                content = escape_markdown(content)
            imgs = list()
            if detail.get("item").get("pictures"):
                imgs = [t.get("img_src") for t in detail.get("item").get("pictures")]
            elif detail.get("item").get("video_playurl"):
                imgs = [
                    detail.get("item").get("video_playurl"),
                    detail.get("item").get("cover").get("unclipped"),
                ]
        url = f"https://t.bilibili.com/{dynamic_id}"
        if forward_user := card.get("user").get("uname"):
            forward_content = escape_markdown(card.get("item").get("content"))
            if origin:
                forward_content += f"//{user_markdown}:\n{content}"
            user = forward_user
            content = forward_content
            user_markdown = (
                f"[@{user}](https://space.bilibili.com/{card.get('user').get('uid')})"
            )
        logger.debug(f"用户: {user_markdown}\n内容: {content}\n图片: {imgs}")
        return s, user, user_markdown, content, imgs, url
    elif match := re.search(r"vc\.bilibili\.com[\D]*(\d+)", post.url):
        video_id = match.group(1)
        logger.info(f"短视频ID: {video_id}")
        data = s.get(
            "https://api.vc.bilibili.com/clip/v1/video/detail",
            params={"video_id": video_id},
        ).json()
        try:
            detail = data.get("data")
        except AttributeError:
            return
        logger.debug(f"短视频解析: {detail}")
        user = detail.get("user").get("name")
        user_markdown = (
            f"[@{user}](https://space.bilibili.com/{detail.get('user').get('uid')})"
        )
        content = escape_markdown(detail.get("item").get("description"))
        clip = [
            detail.get("item").get("video_playurl"),
            detail.get("item").get("first_pic"),
        ]
        url = f"https://vc.bilibili.com/video/{video_id}"
        logger.debug(f"用户: {user_markdown}\n内容: {content}\n视频: {clip}")
        return s, user, user_markdown, content, clip, url
    else:
        return


def tag_parser(content):
    return re.sub(r"#([^#?=\s|$]+)#?", lambda x: f"#{x.group(1)} ", content)


@run_async
def parse(update, context):
    if (message := update.channel_post) is None:
        message = update.message
    data = message.text
    urls = re.findall(regex, data)
    logger.info(f"Parse: {urls}")

    async def get_img(s, url):
        imgraw = await loop.run_in_executor(None, s.get, url)
        img = BytesIO(imgraw.content)
        img.seek(0)
        while not imgraw.ok:
            asyncio.sleep(1)
        return img

    def callback(caption, dynamic_url, reply_markup, imgs, imgraws):
        if ".mp4" in imgs[0]:
            message.reply_video(
                imgraws[0],
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN,
                timeout=120,
            )
        elif len(imgs) == 1:
            if ".gif" in imgs[0]:
                message.reply_animation(
                    imgraws[0],
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN,
                    timeout=60,
                )
            else:
                message.reply_photo(
                    imgraws[0],
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN,
                    timeout=60,
                )
        else:
            media = [
                InputMediaPhoto(img, caption=caption, parse_mode=ParseMode.MARKDOWN)
                for img in imgraws
            ]
            message.reply_media_group(media, timeout=120)
            message.reply_text(
                caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                quote=False,
            )

    async def parse_queue(url):
        try:
            s, _, user_markdown, content, imgs, dynamic_url = dynamic_parser(url)
        except TypeError as err:
            logger.exception(err)
            logger.warning("解析错误！")
            return
        caption = f"{user_markdown}:\n{tag_parser(content)}"
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="动态源地址", url=dynamic_url)]]
        )
        if imgs:
            try:
                callback(caption, dynamic_url, reply_markup, imgs, imgs)
            except (TimedOut, BadRequest) as err:
                logger.exception(err)
                logger.info(f"{err} -> 下载中: {dynamic_url}")
                tasks = [get_img(s, img) for img in imgs]
                imgraws = await asyncio.gather(*tasks)
                logger.info(f"上传中: {dynamic_url}")
                callback(caption, dynamic_url, reply_markup, imgs, imgraws)
        else:
            message.reply_text(
                caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
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
        url = re.search(regex, query).group(0)
    except AttributeError:
        inline_query.answer(helpmsg)
        return
    logger.info(f"Inline: {url}")
    try:
        _, user, user_markdown, content, imgs, dynamic_url = dynamic_parser(url)
    except TypeError as err:
        logger.exception(err)
        logger.warning("解析错误！")
        return
    caption = f"{user_markdown}:\n{tag_parser(content)}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="动态源地址", url=dynamic_url)]]
    )
    if not imgs:
        results = [
            InlineQueryResultArticle(
                id=uuid4(),
                title=user,
                description=content,
                reply_markup=reply_markup,
                input_message_content=InputTextMessageContent(
                    caption,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                ),
            )
        ]
    else:
        if ".mp4" in imgs[0]:
            results = [
                InlineQueryResultVideo(
                    id=uuid4(),
                    caption=caption,
                    title=user,
                    description=content,
                    reply_markup=reply_markup,
                    mime_type="video/mp4",
                    video_url=imgs[0],
                    thumb_url=imgs[1],
                    parse_mode=ParseMode.MARKDOWN,
                )
            ]
        else:
            results = [
                InlineQueryResultGif(
                    id=uuid4(),
                    caption=caption,
                    title=f"{user}: {content}",
                    reply_markup=reply_markup,
                    gif_url=img,
                    thumb_url=img,
                    parse_mode=ParseMode.MARKDOWN,
                )
                if ".gif" in img
                else InlineQueryResultPhoto(
                    id=uuid4(),
                    caption=caption,
                    title=user,
                    description=content,
                    reply_markup=reply_markup,
                    photo_url=img,
                    thumb_url=img + "@428w_428h_1e_1c.png",
                    parse_mode=ParseMode.MARKDOWN,
                )
                for img in imgs
            ]
        if len(results) == 1:
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
    updater.dispatcher.add_handler(MessageHandler(Filters.regex(regex), parse))
    updater.dispatcher.add_handler(InlineQueryHandler(inlineparse))
    updater.dispatcher.add_error_handler(error)
    updater.start_polling()
    updater.idle()
