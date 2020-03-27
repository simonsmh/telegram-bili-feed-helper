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

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger("Telegram_Bili_Feed_Helper")

regex = r"(?i)https?:\/\/vc\.bilibili\.com[\D]*\d+|https?:\/\/[th]\.bilibili\.com[\/\w]*\/\d+|https?:\/\/b23\.tv\/(?!ep|av|bv)\w+"


class feed:
    def __init__(self, rawurl):
        self.rawurl = rawurl
        self.rawcontent = None
        self.dynamic_id = None
        self.video_id = None
        self.user = None
        self.uid = None
        self.content = None
        self.forward_user = None
        self.forward_uid = None
        self.forward_content = None
        self.mediaurls = list()
        self.mediatype = None

    def user_markdown(self, forward=False):
        return f"[@{self.forward_user if forward else self.user}](https://space.bilibili.com/{self.forward_uid if forward else self.uid})"

    def forward_card(self):
        return json.loads(self.rawcontent.get("data").get("card").get("card"))

    def has_forward(self):
        return bool(self.forward_card().get("origin"))

    def card(self):
        return (
            json.loads(self.forward_card().get("origin"))
            if self.has_forward()
            else self.forward_card()
        )

    def final_user(self, markdown=True):
        return (
            (self.user_markdown(forward=True) if markdown else self.forward_user)
            if self.dynamic_id and self.has_forward()
            else (self.user_markdown() if markdown else self.user)
        )

    def final_content(self, markdown=True):
        return (
            f"{self.forward_content}//{self.user_markdown() if markdown else self.user}:\n{self.content}"
            if self.dynamic_id and self.has_forward()
            else self.content
        )

    def url(self):
        if self.dynamic_id:
            return f"https://t.bilibili.com/{self.dynamic_id}"
        elif self.video_id:
            return f"https://vc.bilibili.com/video/{self.video_id}"
        return


def dynamic_parser(url):
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
        }
    )
    post = s.get(url)
    # dynamic
    if match := re.search(r"[th]\.bilibili\.com[\/\w]*\/(\d+)", post.url):
        f = feed(url)
        f.rawcontent = s.get(
            "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
            params={"rid": match.group(1), "type": 2}
            if "type=2" in match.group(0) or "h.bilibili.com" in match.group(0)
            else {"dynamic_id": match.group(1)},
        ).json()
        try:
            f.dynamic_id = (
                f.rawcontent.get("data").get("card").get("desc").get("dynamic_id_str")
            )
        except AttributeError:
            logger.warning(f"动态解析错误: {url}")
            return s, None
        logger.info(f"动态ID: {f.dynamic_id}")
        detail = f.card()
        # bv video
        if av_id := detail.get("aid"):
            f.user = detail.get("owner").get("name")
            f.uid = detail.get("owner").get("mid")
            f.content = f"{escape_markdown(detail.get('dynamic')) if detail.get('dynamic') else None}\n[{escape_markdown(detail.get('title'))}](https://b23.tv/av{av_id})"
            f.mediaurls = [detail.get("pic")]
            f.mediatype = "picture"
        # cv article
        elif detail.get("words"):
            cv_id = detail.get("id")
            f.user = detail.get("author").get("name")
            f.uid = detail.get("author").get("mid")
            f.content = f"{escape_markdown(detail.get('dynamic')) if detail.get('dynamic') else None}\n[{escape_markdown(detail.get('title'))}](https://www.bilibili.com/read/cv{cv_id})"
            if detail.get("banner_url"):
                f.mediaurls = detail.get("banner_url")
            else:
                f.mediaurls.extend(detail.get("image_urls"))
            f.mediatype = "picture"
        # au audio
        elif detail.get("typeInfo"):
            au_id = detail.get("id")
            f.user = detail.get("upper")
            f.uid = detail.get("upId")
            f.content = f"{escape_markdown(detail.get('intro'))}\n[{escape_markdown(detail.get('title'))}](https://www.bilibili.com/audio/au{au_id})"
            f.mediaurls = [detail.get("cover")]
            f.mediatype = "picture"
        # live
        elif detail.get("roomid"):
            room_id = detail.get("roomid")
            f.user = detail.get("uname")
            f.uid = detail.get("uid")
            f.content = f"[{escape_markdown(detail.get('title'))}](https://live.bilibili.com/{room_id})"
            f.mediaurls = [detail.get("user_cover")]
            f.mediatype = "picture"
        # dynamic pictures/gifs/videos
        elif detail.get("user").get("name"):
            f.user = detail.get("user").get("name")
            f.uid = detail.get("user").get("uid")
            f.content = escape_markdown(detail.get("item").get("description"))
            if detail.get("item").get("pictures"):
                f.mediaurls = [
                    t.get("img_src") for t in detail.get("item").get("pictures")
                ]
                f.mediatype = "picture"
            elif detail.get("item").get("video_playurl"):
                f.mediaurls = [
                    detail.get("item").get("video_playurl"),
                    detail.get("item").get("cover").get("unclipped"),
                ]
                f.mediatype = "video"
        # dynamic text
        elif detail.get("user").get("uname"):
            f.user = detail.get("user").get("uname")
            f.uid = detail.get("user").get("uid")
            f.content = escape_markdown(detail.get("item").get("content"))
        # forward text
        if f.has_forward():
            forward_detail = f.forward_card()
            f.forward_user = forward_detail.get("user").get("uname")
            f.forward_uid = forward_detail.get("user").get("uid")
            f.forward_content = escape_markdown(
                forward_detail.get("item").get("content")
            )
    # vc video
    elif match := re.search(r"vc\.bilibili\.com[\D]*(\d+)", post.url):
        f = feed(url)
        f.video_id = match.group(1)
        f.rawcontent = s.get(
            "https://api.vc.bilibili.com/clip/v1/video/detail",
            params={"video_id": f.video_id},
        ).json()
        try:
            detail = f.rawcontent.get("data")
        except AttributeError:
            logger.warning(f"短视频解析错误: {url}")
            return s, None
        logger.info(f"短视频ID: {f.video_id}")
        f.user = detail.get("user").get("name")
        f.uid = detail.get("user").get("uid")
        f.content = escape_markdown(detail.get("item").get("description"))
        f.mediaurls = [
            detail.get("item").get("video_playurl"),
            detail.get("item").get("first_pic"),
        ]
        f.mediatype = "video"
    else:
        return s, None
    logger.info(
        f"用户: {f.final_user()}\n内容: {f.final_content()}\n媒体: {f.mediaurls}\n链接: {f.url()}"
    )
    return s, f


def tag_parser(content):
    return re.sub(r"#([^#?=\s|$]+)#?", lambda x: f"#{x.group(1)} ", content)


@run_async
def parse(update, context):
    if not (message := update.channel_post):
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

    def callback(caption, reply_markup, imgs, imgraws):
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
        s, f = dynamic_parser(url)
        if not f:
            logger.warning("解析错误！")
            return
        caption = f"{f.final_user()}:\n{tag_parser(f.final_content())}"
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="动态源地址", url=f.url())]]
        )
        if f.mediaurls:
            try:
                f.mediaurls = [
                    i + "@1280w_1e_1c.jpg" if not ".mp4" in i and not ".gif" in i else i
                    for i in f.mediaurls
                ]
                callback(caption, reply_markup, f.mediaurls, f.mediaurls)
            except (TimedOut, BadRequest) as err:
                logger.exception(err)
                logger.info(f"{err} -> 下载中: {f.url()}")
                tasks = [get_img(s, img) for img in f.mediaurls]
                imgraws = await asyncio.gather(*tasks)
                logger.info(f"上传中: {f.url()}")
                callback(caption, reply_markup, f.mediaurls, imgraws)
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
    _, f = dynamic_parser(url)
    if not f:
        logger.warning("解析错误！")
        return
    caption = f"{f.final_user()}:\n{tag_parser(f.final_content())}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="动态源地址", url=f.url())]]
    )
    if not f.mediaurls:
        results = [
            InlineQueryResultArticle(
                id=uuid4(),
                title=f.final_user(markdown=False),
                description=f.final_content(markdown=False),
                reply_markup=reply_markup,
                input_message_content=InputTextMessageContent(
                    caption,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                ),
            )
        ]
    else:
        if ".mp4" in f.mediaurls[0]:
            results = [
                InlineQueryResultVideo(
                    id=uuid4(),
                    caption=caption,
                    title=f.final_user(markdown=False),
                    description=f.final_content(markdown=False),
                    reply_markup=reply_markup,
                    mime_type="video/mp4",
                    video_url=f.mediaurls[0],
                    thumb_url=f.mediaurls[1],
                    parse_mode=ParseMode.MARKDOWN,
                )
            ]
        else:
            results = [
                InlineQueryResultGif(
                    id=uuid4(),
                    caption=caption,
                    title=f"{f.final_user(markdown=False)}: {f.final_content(markdown=False)}",
                    reply_markup=reply_markup,
                    gif_url=img,
                    thumb_url=img,
                    parse_mode=ParseMode.MARKDOWN,
                )
                if ".gif" in img
                else InlineQueryResultPhoto(
                    id=uuid4(),
                    caption=caption,
                    title=f.final_user(markdown=False),
                    description=f.final_content(markdown=False),
                    reply_markup=reply_markup,
                    photo_url=img + "@1280w_1e_1c.jpg",
                    thumb_url=img + "@512w_512h_1e_1c.jpg",
                    parse_mode=ParseMode.MARKDOWN,
                )
                for img in f.mediaurls
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


@run_async
def start(update, context):
    update.message.reply_text(
        "欢迎使用 @bilifeedbot 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组自动匹配消息。",
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
    )


if __name__ == "__main__":
    if len(sys.argv) >= 2 and os.path.exists(sys.argv[1]):
        config = load_json(sys.argv[1])
    else:
        config = load_json()
    updater = Updater(config.get("TOKEN"), use_context=True)
    updater.dispatcher.add_handler(
        CommandHandler("start", start, filters=Filters.private)
    )
    updater.dispatcher.add_handler(MessageHandler(Filters.regex(regex), parse))
    updater.dispatcher.add_handler(InlineQueryHandler(inlineparse))
    updater.dispatcher.add_error_handler(error)
    updater.start_polling()
    updater.idle()
