import json
import logging
import re
from functools import cached_property, lru_cache

import aiohttp

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger("Bili_Feed_Parser")

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
}


def escape_markdown(text):
    return re.sub(r"([_*\[\]()~`>\#\+\-=|{}\.!])", r"\\\1", text)


class feed:
    def __init__(self, rawurl):
        self.rawurl = rawurl
        self.user = None
        self.uid = None
        self.content = None
        self.mediaurls = list()
        self.mediaraws = False
        self.mediatype = None
        self.mediathumb = None
        self.mediaduration = None
        self.mediatitle = None

    @staticmethod
    def make_user_markdown(user, uid):
        return f"[@{escape_markdown(user)}](https://space.bilibili.com/{uid})"

    @cached_property
    def user_markdown(self):
        return self.make_user_markdown(self.user, self.uid)

    @cached_property
    def content_markdown(self):
        return escape_markdown(self.content)

    @cached_property
    def url(self):
        return self.rawurl

    @cached_property
    def mediafilename(self):
        return [
            re.search(r"\/([^\/]*\.\w{3,4})(?:$|\?)", i).group(1)
            for i in self.mediaurls
        ]


class dynamic(feed):
    def __init__(self, rawurl):
        super(dynamic, self).__init__(rawurl)
        self.detailcontent = None
        self.replycontent = None
        self.dynamic_id = None
        self.rid = None
        self.__user = None
        self.__content = None
        self.forward_user = None
        self.forward_uid = None
        self.forward_content = None
        self.extra_markdown = str()

    @cached_property
    def forward_card(self):
        return json.loads(self.detailcontent.get("data").get("card").get("card"))

    @cached_property
    def has_forward(self):
        return bool(
            self.detailcontent.get("data").get("card").get("desc").get("orig_type")
        )

    @cached_property
    def forward_type(self):
        return self.detailcontent.get("data").get("card").get("desc").get("type")

    @cached_property
    def origin_type(self):
        return (
            self.detailcontent.get("data").get("card").get("desc").get("orig_type")
            if self.has_forward
            else self.forward_type
        )

    @cached_property
    def reply_type(self):
        if self.forward_type == 2:
            return 11
        elif self.forward_type == 16:
            return 5
        elif self.forward_type == 64:
            return 12
        elif self.forward_type == 256:
            return 14
        elif self.forward_type in [8, 512, *range(4000, 4200)]:
            return 1
        elif self.forward_type in [1, 4, *range(4200, 4300), *range(2048, 2100)]:
            return 17

    @cached_property
    def oid(self):
        if self.forward_type in [1, 4, *range(4200, 4300), *range(2048, 2100)]:
            return self.dynamic_id
        else:
            return self.rid

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

    @cached_property
    def comment(self):
        comment = str()
        if top := self.replycontent.get("data").get("upper").get("top"):
            comment += f'置顶>@{top.get("member").get("uname")}:{top.get("content").get("message")}\n'
        if hots := self.replycontent.get("data").get("hots"):
            comment += f'热评>@{hots[0].get("member").get("uname")}:{hots[0].get("content").get("message")}\n'
        return comment

    @cached_property
    def comment_markdown(self):
        comment_markdown = str()
        if top := self.replycontent.get("data").get("upper").get("top"):
            comment_markdown += f'置顶\>{self.make_user_markdown(top.get("member").get("uname"), top.get("member").get("mid"))}:{escape_markdown(top.get("content").get("message"))}\n'
        if hots := self.replycontent.get("data").get("hots"):
            comment_markdown += f'热评\>{self.make_user_markdown(hots[0].get("member").get("uname"), hots[0].get("member").get("mid"))}:{escape_markdown(hots[0].get("content").get("message"))}\n'
        return comment_markdown

    @property
    @lru_cache(maxsize=None)
    def content(self):
        return (
            f"{self.forward_content}//{self.__user}:\n" if self.has_forward else str()
        ) + f"{self.__content}\n\n{self.comment}"

    @content.setter
    def content(self, content):
        self.__content = content

    @cached_property
    def content_markdown(self):
        return (
            (
                f"{escape_markdown(self.forward_content)}//{self.make_user_markdown(self.__user, self.uid)}:\n"
                if self.has_forward
                else str()
            )
            + f"{escape_markdown(self.__content)}\n{self.extra_markdown}\n\n{self.comment_markdown}"
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


async def dynamic_parser(s, url):
    match = re.search(r"[th]\.bilibili\.com[\/\w]*\/(\d+)", url)
    f = dynamic(url)
    async with s.get(
        "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
        params={"rid": match.group(1), "type": 2}
        if "type=2" in match.group(0) or "h.bilibili.com" in match.group(0)
        else {"dynamic_id": match.group(1)},
    ) as resp:
        f.detailcontent = await resp.json(content_type="application/json")
    try:
        f.type = f.detailcontent.get("data").get("card").get("desc").get("type")
    except AttributeError:
        logger.warning(f"动态解析错误: {url}")
        return
    f.dynamic_id = (
        f.detailcontent.get("data").get("card").get("desc").get("dynamic_id_str")
    )
    f.rid = f.detailcontent.get("data").get("card").get("desc").get("rid_str")
    logger.info(f"动态ID: {f.dynamic_id}")
    # extract from detail.js
    detail_types_list = {
        # REPOST WORD
        "WORD": [1, 4],
        "PIC": [2],
        "VIDEO": [8],
        "CLIP": [16],
        "ARTICLE": [64],
        "MUSIC": [256],
        # LIVE LIVE_ROOM
        "LIVE": range(4200, 4300),
        # H5_SHARE COMIC_SHARE
        "SHARE": range(2048, 2100),
        # BANGUMI PGC_BANGUMI FILM TV GUOCHUANG DOCUMENTARY
        "EPS": [512, *range(4000, 4200)],
        # NONE MEDIA_LIST CHEESE_SERIES CHEESE_UPDATE
        "NONE": [2024, *range(4300, 4400)],
    }
    # bv video
    if f.origin_type in detail_types_list.get("VIDEO"):
        av_id = f.card.get("aid")
        f.user = f.card.get("owner").get("name")
        f.uid = f.card.get("owner").get("mid")
        f.content = f.card.get("dynamic") if f.card.get("dynamic") else str()
        f.extra_markdown = (
            f"[{escape_markdown(f.card.get('title'))}](https://b23.tv/av{av_id})"
        )
        f.mediaurls = [f.card.get("pic")]
        f.mediatype = "image"
    # cv article
    elif f.origin_type in detail_types_list.get("ARTICLE"):
        cv_id = f.card.get("id")
        f.user = f.card.get("author").get("name")
        f.uid = f.card.get("author").get("mid")
        f.content = f.card.get("dynamic") if f.card.get("dynamic") else str()
        f.extra_markdown = f"[{escape_markdown(f.card.get('title'))}](https://www.bilibili.com/read/cv{cv_id})"
        if f.card.get("banner_url"):
            f.mediaurls = f.card.get("banner_url")
        else:
            f.mediaurls.extend(f.card.get("image_urls"))
        f.mediatype = "image"
    # au audio
    elif f.origin_type in detail_types_list.get("MUSIC"):
        au_id = f.card.get("id")
        f.user = f.card.get("upper")
        f.uid = f.card.get("upId")
        f.content = f.card.get("intro")
        f.extra_markdown = f"[{escape_markdown(f.card.get('title'))}](https://www.bilibili.com/audio/au{au_id})"
        # Getting audio link from audio parser
        fu = await audio_parser(s, f"https://www.bilibili.com/audio/au{au_id}")
        f.mediaurls = fu.mediaurls
        f.mediatype = fu.mediatype
        f.mediathumb = fu.mediathumb
        f.mediatitle = fu.mediatitle
        f.mediaduration = fu.mediaduration
    # live
    elif f.origin_type in detail_types_list.get("LIVE"):
        room_id = f.card.get("roomid")
        f.user = f.card.get("uname")
        f.uid = f.card.get("uid")
        f.extra_markdown = f"[{escape_markdown(f.card.get('title'))}](https://live.bilibili.com/{room_id})"
        # Getting live link from live parser
        fu = await live_parser(s, f"https://live.bilibili.com/{room_id}")
        f.content = fu.content
        f.mediaurls = fu.mediaurls
        f.mediatype = fu.mediatype
    # dynamic images/videos
    elif f.origin_type in [
        *detail_types_list.get("PIC"),
        *detail_types_list.get("CLIP"),
    ]:
        f.user = f.card.get("user").get("name")
        f.uid = f.card.get("user").get("uid")
        f.content = f.card.get("item").get("description")
        if f.origin_type in detail_types_list.get("PIC"):
            f.mediaurls = [t.get("img_src") for t in f.card.get("item").get("pictures")]
            f.mediatype = "image"
        elif f.origin_type in detail_types_list.get("CLIP"):
            f.mediaurls = [f.card.get("item").get("video_playurl")]
            f.mediathumb = f.card.get("item").get("cover").get("unclipped")
            f.mediatype = "video"
    # dynamic text
    elif f.origin_type in detail_types_list.get("WORD"):
        f.user = f.card.get("user").get("uname")
        f.uid = f.card.get("user").get("uid")
        f.content = f.card.get("item").get("content")
    # forward text
    if f.has_forward:
        f.forward_user = f.forward_card.get("user").get("uname")
        f.forward_uid = f.forward_card.get("user").get("uid")
        f.forward_content = f.forward_card.get("item").get("content")
    async with s.get(
        "https://api.bilibili.com/x/v2/reply",
        params={"oid": f.oid, "type": f.reply_type},
    ) as resp:
        f.replycontent = await resp.json(content_type="application/json")
    return f


async def clip_parser(s, url):
    match = re.search(r"vc\.bilibili\.com[\D]*(\d+)", url)
    f = clip(url)
    f.video_id = match.group(1)
    async with s.get(
        "https://api.vc.bilibili.com/clip/v1/video/detail",
        params={"video_id": f.video_id},
    ) as resp:
        f.rawcontent = await resp.json(content_type="text/json")
    try:
        detail = f.rawcontent.get("data")
    except AttributeError:
        logger.warning(f"短视频解析错误: {url}")
        return
    logger.info(f"短视频ID: {f.video_id}")
    f.user = detail.get("user").get("name")
    f.uid = detail.get("user").get("uid")
    f.content = detail.get("item").get("description")
    f.mediaurls = [detail.get("item").get("video_playurl")]
    f.mediathumb = detail.get("item").get("first_pic")
    f.mediatype = "video"
    return f


async def audio_parser(s, url):
    match = re.search(r"bilibili\.com\/audio\/au(\d+)", url)
    f = audio(url)
    f.audio_id = match.group(1)
    async with s.get(
        "https://api.bilibili.com/audio/music-service-c/songs/playing",
        params={"song_id": f.audio_id},
    ) as resp:
        f.infocontent = await resp.json(content_type="application/json")
    if not (detail := f.infocontent.get("data")):
        logger.warning(f"音频解析错误: {url}")
        return
    mid = detail.get("mid")
    async with s.get(
        "https://api.bilibili.com/audio/music-service-c/url",
        params={
            "songid": f.audio_id,
            "mid": mid,
            "privilege": 2,
            "quality": 3,
            "platform": "",
        },
    ) as resp:
        f.mediacontent = await resp.json(content_type="application/json")
    logger.info(f"音频ID: {f.audio_id}")
    f.user = detail.get("author")
    f.uid = detail.get("uid")
    f.content = detail.get("intro")
    f.mediathumb = detail.get("cover_url")
    f.mediatitle = detail.get("title")
    f.mediaduration = detail.get("duration")
    f.mediaurls = f.mediacontent.get("data").get("cdns")
    f.mediatype = "audio"
    f.mediaraws = True
    return f


async def live_parser(s, url):
    match = re.search(r"live.bilibili\.com\/(\d+)", url)
    f = live(url)
    f.room_id = match.group(1)
    async with s.get(
        "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
        params={"room_id": f.room_id},
    ) as resp:
        f.rawcontent = await resp.json(content_type="application/json")
    if not (detail := f.rawcontent.get("data")):
        logger.warning(f"直播解析错误: {url}")
        return
    logger.info(f"直播ID: {f.room_id}")
    f.user = detail.get("anchor_info").get("base_info").get("uname")
    roominfo = detail.get("room_info")
    f.uid = roominfo.get("uid")
    f.content = f"{roominfo.get('title')} - {roominfo.get('area_name')} - {roominfo.get('parent_area_name')}"
    f.mediaurls = [roominfo.get("keyframe")]
    f.mediatype = "image"
    return f


async def video_parser(s, url):
    match = re.search(
        r"(?i)(?:www\.|m\.)?(?:bilibili\.com/video|b23\.tv|acg\.tv)/(?:av(?P<aid>\d+)|(?P<bvid>bv\w+))",
        url,
    )
    f = video(url)
    if bvid := match.group("bvid"):
        params = {"bvid": bvid}
    elif aid := match.group("aid"):
        params = {"aid": aid}
    async with s.get(
        "https://api.bilibili.com/x/web-interface/view", params=params,
    ) as resp:
        f.infocontent = await resp.json(content_type="application/json")
    if not (detail := f.infocontent.get("data")):
        logger.warning(f"视频解析错误: {url}")
        return
    f.aid = detail.get("aid")
    logger.info(f"视频ID: {f.aid}")
    f.cid = detail.get("cid")
    f.user = detail.get("owner").get("name")
    f.uid = detail.get("owner").get("mid")
    f.content = f"{detail.get('dynamic')}\n{detail.get('title')}"
    async with s.get(
        "https://api.bilibili.com/x/player/playurl",
        params={"avid": f.aid, "cid": f.cid, "fnval": 16},
    ) as resp:
        f.mediacontent = await resp.json(content_type="application/json")
    f.mediaurls = [
        f.mediacontent.get("data").get("dash").get("video")[0].get("base_url")
    ]
    f.mediathumb = detail.get("pic")
    f.mediatype = "video"
    f.mediaraws = True
    return f


async def feedparser(url):
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(url) as resp:
            url = str(resp.url)
            # dynamic
            if re.search(r"[th]\.bilibili\.com", url):
                f = await dynamic_parser(s, url)
            # live image
            elif re.search(r"live\.bilibili\.com", url):
                f = await live_parser(s, url)
            # vc video
            elif re.search(r"vc\.bilibili\.com", url):
                f = await clip_parser(s, url)
            # au audio
            elif re.search(r"bilibili\.com/audio", url):
                f = await audio_parser(s, url)
            # main video
            # elif re.search(r"bilibili\.com/video", url):
            #     f = await video_parser(s, url)
            else:
                return
    logger.info(
        f"用户: {f.user}\n"
        f"内容: {f.content}\n"
        f"链接: {f.url}\n"
        f"媒体: {f.mediaurls}\n"
        f"媒体种类: {f.mediatype}\n"
        f"媒体预览: {f.mediathumb}\n"
        f"媒体标题: {f.mediatitle}\n"
    )
    return f
