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
        self.user = None
        self.uid = None
        self.content = None
        self.mediaurls = list()
        self.mediaraws = None
        self.mediatype = None
        self.mediathumb = None
        self.mediatitle = None

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
        self.rawcontent = None
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
        self.rawcontent = None
        self.video_id = None

    @cached_property
    def url(self):
        return f"https://vc.bilibili.com/video/{self.video_id}"


class audio(feed):
    def __init__(self, rawurl):
        super(audio, self).__init__(rawurl)
        self.infocontent = None
        self.mediacontent = None
        self.audio_id = None

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/audio/au{self.audio_id}"


class live(feed):
    def __init__(self, rawurl):
        super(live, self).__init__(rawurl)
        self.rawcontent = None
        self.room_id = None

    @cached_property
    def url(self):
        return f"https://live.bilibili.com/{self.room_id}"


class video(feed):
    def __init__(self, rawurl):
        super(video, self).__init__(rawurl)
        self.aid = None
        self.cid = None
        self.cidcontent = None
        self.infocontent = None
        self.mediacontent = None

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/video/av{self.aid}"


def dynamic_parser(s, url):
    match = re.search(r"[th]\.bilibili\.com[\/\w]*\/(\d+)", url)
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
        # Getting audio link from audio parser
        s, fu = audio_parser(s, f"https://www.bilibili.com/audio/au{au_id}")
        f.mediaurls = fu.mediaurls
        f.mediatype = fu.mediatype
        f.mediathumb = fu.mediathumb
        f.mediatitle = fu.mediatitle
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
            f.mediaurls = [t.get("img_src") for t in f.card.get("item").get("pictures")]
            f.mediatype = "picture"
        elif f.card.get("item").get("video_playurl"):
            f.mediaurls = [f.card.get("item").get("video_playurl")]
            f.mediathumb = f.card.get("item").get("cover").get("unclipped")
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
        f.forward_content = escape_markdown(f.forward_card.get("item").get("content"))
    s.headers.update({"Referer": f.url})
    return s, f


def clip_parser(s, url):
    match = re.search(r"vc\.bilibili\.com[\D]*(\d+)", url)
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
    f.mediaurls = [detail.get("item").get("video_playurl")]
    f.mediathumb = detail.get("item").get("first_pic")
    f.mediatype = "video"
    s.headers.update({"Referer": f.url})
    return s, f


def audio_parser(s, url):
    match = re.search(r"bilibili\.com\/audio\/au(\d+)", url)
    f = audio(url)
    f.audio_id = match.group(1)
    params = {"sid": f.audio_id}
    f.infocontent = s.get(
        "https://www.bilibili.com/audio/music-service-c/web/song/info", params=params,
    ).json()
    f.mediacontent = s.get(
        "https://www.bilibili.com/audio/music-service-c/web/url", params=params,
    ).json()
    if not (detail := f.infocontent.get("data")):
        logger.warning(f"音频解析错误: {url}")
        return s, None
    logger.info(f"音频ID: {f.audio_id}")
    f.user = detail.get("uname")
    f.uid = detail.get("uid")
    f.content = escape_markdown(detail.get("intro"))
    f.mediaurls = f.mediacontent.get("data").get("cdns")
    f.mediathumb = detail.get("cover")
    f.mediatitle = detail.get("title")
    f.mediatype = "audio"
    s.headers.update({"Referer": f.url})
    return s, f


def live_parser(s, url):
    match = re.search(r"live.bilibili\.com\/(\d+)", url)
    f = live(url)
    f.room_id = match.group(1)
    f.rawcontent = s.get(
        "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
        params={"room_id": f.room_id},
    ).json()
    if not (detail := f.rawcontent.get("data")):
        logger.warning(f"直播解析错误: {url}")
        return s, None
    logger.info(f"直播ID: {f.room_id}")
    f.user = detail.get("anchor_info").get("base_info").get("uname")
    roominfo = detail.get("room_info")
    f.uid = roominfo.get("uid")
    f.content = escape_markdown(
        f"{roominfo.get('title')} - {roominfo.get('area_name')} - {roominfo.get('parent_area_name')}"
    )
    f.mediaurls = [roominfo.get("keyframe")]
    f.mediatype = "picture"
    s.headers.update({"Referer": f.url})
    return s, f


def video_parser(s, url):
    match = re.search(
        r"(?i)(?:www\.|m\.)?(?:bilibili\.com/video|b23\.tv|acg\.tv)/(?:av(?P<aid>\d+)|(?P<bvid>bv\w+))",
        url,
    )
    f = video(url)
    if bvid := match.group("bvid"):
        params = {"bvid": bvid}
    elif aid := match.group("aid"):
        params = {"aid": aid}
    f.infocontent = s.get(
        "https://api.bilibili.com/x/web-interface/view", params=params,
    ).json()
    if not (detail := f.infocontent.get("data")):
        logger.warning(f"视频解析错误: {url}")
        return s, None
    f.aid = detail.get("aid")
    logger.info(f"视频ID: {f.aid}")
    f.cid = detail.get("cid")
    f.user = detail.get("owner").get("name")
    f.uid = detail.get("owner").get("mid")
    f.content = escape_markdown(f"{detail.get('dynamic')}\n{detail.get('title')}")
    f.mediacontent = s.get(
        "https://api.bilibili.com/x/player/playurl",
        params={"avid": f.aid, "cid": f.cid, "fnval": 16},
    ).json()
    f.mediaurls = [
        f.mediacontent.get("data").get("dash").get("video")[0].get("base_url")
    ]
    f.mediathumb = detail.get("pic")
    f.mediatype = "video"
    f.mediaraws = True
    s.headers.update({"Referer": f.url})
    return s, f


def feedparser(url):
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
        }
    )
    post = s.get(url)
    # dynamic
    if re.search(r"[th]\.bilibili\.com", post.url):
        s, f = dynamic_parser(s, post.url)
    # live picture
    elif re.search(r"live\.bilibili\.com", post.url):
        s, f = live_parser(s, post.url)
    # vc video
    elif re.search(r"vc\.bilibili\.com", post.url):
        s, f = clip_parser(s, post.url)
    # au audio
    # elif re.search(r"bilibili\.com/audio", post.url):
    #     s, f = audio_parser(s, post.url)
    # main video
    # elif re.search(r"bilibili\.com/video", post.url):
    #     s, f = video_parser(s, post.url)
    else:
        return s, None
    logger.info(
        f"用户: {f.user}\n"
        f"内容: {f.content}\n"
        f"链接: {f.url}\n"
        f"媒体: {f.mediaurls}\n"
        f"媒体种类: {f.mediatype}\n"
        f"媒体预览: {f.mediathumb}\n"
        f"媒体标题: {f.mediatitle}\n"
    )
    return s, f
