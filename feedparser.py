import json
import logging
import re
from functools import cached_property, lru_cache

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
        self.user = None
        self.uid = None
        self.content = None
        self.mediaurls = list()
        self.mediatype = None

    @staticmethod
    def make_user_markdown(user, uid):
        return f"[@{user}](https://space.bilibili.com/{uid})"

    @cached_property
    def user_markdown(self):
        return self.make_user_markdown(self.user, self.uid)

    @cached_property
    def content_markdown(self):
        return self.content

    @cached_property
    def url(self):
        return self.rawurl


class dynamic(feed):
    def __init__(self, rawurl):
        super(dynamic, self).__init__(rawurl)
        self.dynamic_id = None
        self.__user = None
        self.__content = None
        self.forward_user = None
        self.forward_uid = None
        self.forward_content = None

    @cached_property
    def forward_card(self):
        return json.loads(self.rawcontent.get("data").get("card").get("card"))

    @cached_property
    def has_forward(self):
        return bool(self.forward_card.get("origin"))

    @cached_property
    def card(self):
        return (
            json.loads(self.forward_card.get("origin"))
            if self.has_forward
            else self.forward_card
        )

    @property
    @lru_cache(maxsize=None)
    def user(self):
        return self.forward_user if self.has_forward else self.__user

    @user.setter
    def user(self, user):
        self.__user = user

    @cached_property
    def user_markdown(self):
        return (
            self.make_user_markdown(self.forward_user, self.forward_uid)
            if self.has_forward
            else self.make_user_markdown(self.__user, self.uid)
        )

    @property
    @lru_cache(maxsize=None)
    def content(self):
        return (
            f"{self.forward_content}//{self.__user}:\n{self.__content}"
            if self.has_forward
            else self.__content
        )

    @content.setter
    def content(self, content):
        self.__content = content

    @cached_property
    def content_markdown(self):
        return (
            f"{self.forward_content}//{self.make_user_markdown(self.__user, self.uid)}:\n{self.__content}"
            if self.has_forward
            else self.__content
        )

    @cached_property
    def url(self):
        return f"https://t.bilibili.com/{self.dynamic_id}"


class clip(feed):
    def __init__(self, rawurl):
        super(clip, self).__init__(rawurl)
        self.video_id = None

    @cached_property
    def url(self):
        return f"https://vc.bilibili.com/video/{self.video_id}"


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
        f = dynamic(url)
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
        # bv video
        if av_id := f.card.get("aid"):
            f.user = f.card.get("owner").get("name")
            f.uid = f.card.get("owner").get("mid")
            f.content = f"{escape_markdown(f.card.get('dynamic')) if f.card.get('dynamic') else None}\n[{escape_markdown(f.card.get('title'))}](https://b23.tv/av{av_id})"
            f.mediaurls = [f.card.get("pic")]
            f.mediatype = "picture"
        # cv article
        elif f.card.get("words"):
            cv_id = f.card.get("id")
            f.user = f.card.get("author").get("name")
            f.uid = f.card.get("author").get("mid")
            f.content = f"{escape_markdown(f.card.get('dynamic')) if f.card.get('dynamic') else None}\n[{escape_markdown(f.card.get('title'))}](https://www.bilibili.com/read/cv{cv_id})"
            if f.card.get("banner_url"):
                f.mediaurls = f.card.get("banner_url")
            else:
                f.mediaurls.extend(f.card.get("image_urls"))
            f.mediatype = "picture"
        # au audio
        elif f.card.get("typeInfo"):
            au_id = f.card.get("id")
            f.user = f.card.get("upper")
            f.uid = f.card.get("upId")
            f.content = f"{escape_markdown(f.card.get('intro'))}\n[{escape_markdown(f.card.get('title'))}](https://www.bilibili.com/audio/au{au_id})"
            f.mediaurls = [f.card.get("cover")]
            f.mediatype = "picture"
        # live
        elif f.card.get("roomid"):
            room_id = f.card.get("roomid")
            f.user = f.card.get("uname")
            f.uid = f.card.get("uid")
            f.content = f"[{escape_markdown(f.card.get('title'))}](https://live.bilibili.com/{room_id})"
            f.mediaurls = [f.card.get("user_cover")]
            f.mediatype = "picture"
        # dynamic pictures/gifs/videos
        elif f.card.get("user").get("name"):
            f.user = f.card.get("user").get("name")
            f.uid = f.card.get("user").get("uid")
            f.content = escape_markdown(f.card.get("item").get("description"))
            if f.card.get("item").get("pictures"):
                f.mediaurls = [
                    t.get("img_src") for t in f.card.get("item").get("pictures")
                ]
                f.mediatype = "picture"
            elif f.card.get("item").get("video_playurl"):
                f.mediaurls = [
                    f.card.get("item").get("video_playurl"),
                    f.card.get("item").get("cover").get("unclipped"),
                ]
                f.mediatype = "video"
        # dynamic text
        elif f.card.get("user").get("uname"):
            f.user = f.card.get("user").get("uname")
            f.uid = f.card.get("user").get("uid")
            f.content = escape_markdown(f.card.get("item").get("content"))
        # forward text
        if f.has_forward:
            f.forward_user = f.forward_card.get("user").get("uname")
            f.forward_uid = f.forward_card.get("user").get("uid")
            f.forward_content = escape_markdown(
                f.forward_card.get("item").get("content")
            )
    # vc video
    elif match := re.search(r"vc\.bilibili\.com[\D]*(\d+)", post.url):
        f = clip(url)
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
    logger.info(f"用户: {f.user}\n内容: {f.content}\n媒体: {f.mediaurls}\n链接: {f.url}")
    return s, f
