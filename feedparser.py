import json
import logging
import re

import requests
from telegram.utils.helpers import escape_markdown

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger("Bili_Feed_Parser")


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
