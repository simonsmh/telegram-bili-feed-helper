import asyncio
import json
import logging
import re
import traceback
from datetime import datetime, timedelta
from functools import cached_property, lru_cache

import httpx
import uvloop
from tortoise import Tortoise, fields
from tortoise.exceptions import IntegrityError
from tortoise.query_utils import Q

from database import (
    audio_cache,
    bangumi_cache,
    clip_cache,
    dynamic_cache,
    live_cache,
    reply_cache,
    video_cache,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger("Bili_Parser")

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
}


def escape_markdown(text):
    return re.sub(r"([_*\[\]()~`>\#\+\-=|{}\.!\\])", r"\\\1", text) if text else str()


class ParserException(Exception):
    def __init__(self, msg, url, res=str()):
        self.msg = msg
        self.url = url
        self.res = res

    def __str__(self):
        return f"{self.msg}: {self.url} ->\n{self.res}"


class feed:
    def __init__(self, rawurl):
        self.rawurl = rawurl
        self.user = None
        self.uid = None
        self.__content = None
        self.__mediaurls = None
        self.mediaraws = False
        self.mediatype = None
        self.mediathumb = None
        self.mediaduration = None
        self.mediatitle = None
        self.extra_markdown = str()
        self.replycontent = None

    @staticmethod
    def make_user_markdown(user, uid):
        return (
            f"[@{escape_markdown(user)}](https://space.bilibili.com/{uid})"
            if user and uid
            else str()
        )

    @staticmethod
    def shrink_line(text):
        return (
            re.sub(r"\n*\n", r"\n", re.sub(r"\r\n", r"\n", text.strip()))
            if text
            else str()
        )

    @cached_property
    def user_markdown(self):
        return self.make_user_markdown(self.user, self.uid)

    @property
    @lru_cache(maxsize=1)
    def content(self):
        return self.shrink_line(self.__content)

    @content.setter
    def content(self, content):
        self.__content = content

    @cached_property
    def content_markdown(self):
        content_markdown = escape_markdown(self.content)
        if not content_markdown.endswith("\n"):
            content_markdown += "\n"
        if self.extra_markdown:
            content_markdown += self.extra_markdown
        return self.shrink_line(content_markdown)

    @cached_property
    def has_comment(self):
        return bool(self.replycontent)

    @cached_property
    def comment(self):
        comment = str()
        if self.has_comment:
            if top := self.replycontent.get("data").get("upper").get("top"):
                comment += f'置顶> @{top.get("member").get("uname")}:\n{top.get("content").get("message")}\n'
            if hots := self.replycontent.get("data").get("hots"):
                comment += f'热评> @{hots[0].get("member").get("uname")}:\n{hots[0].get("content").get("message")}\n'
        return self.shrink_line(comment)

    @cached_property
    def comment_markdown(self):
        comment_markdown = str()
        if self.has_comment:
            if top := self.replycontent.get("data").get("upper").get("top"):
                comment_markdown += f'置顶\\> {self.make_user_markdown(top.get("member").get("uname"), top.get("member").get("mid"))}:\n{escape_markdown(top.get("content").get("message"))}\n'
            if hots := self.replycontent.get("data").get("hots"):
                comment_markdown += f'热评\\> {self.make_user_markdown(hots[0].get("member").get("uname"), hots[0].get("member").get("mid"))}:\n{escape_markdown(hots[0].get("content").get("message"))}\n'
        return self.shrink_line(comment_markdown)

    @property
    @lru_cache(maxsize=1)
    def mediaurls(self):
        return self.__mediaurls

    @mediaurls.setter
    def mediaurls(self, content):
        if isinstance(content, list):
            self.__mediaurls = content
        else:
            self.__mediaurls = [content]

    @cached_property
    def mediafilename(self):
        return (
            [
                re.search(r"\/([^\/]*\.\w{3,4})(?:$|\?)", i).group(1)
                for i in self.__mediaurls
            ]
            if self.__mediaurls
            else list()
        )

    @cached_property
    def url(self):
        return self.rawurl


class dynamic(feed):
    def __init__(self, rawurl):
        super(dynamic, self).__init__(rawurl)
        self.detailcontent = None
        self.dynamic_id = None
        self.rid = None
        self.__user = None
        self.__content = str()
        self.forward_user = None
        self.forward_uid = None
        self.forward_content = str()

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
    @lru_cache(maxsize=1)
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
    @lru_cache(maxsize=1)
    def content(self):
        content = str()
        if self.has_forward:
            content = self.forward_content
            if self.__user:
                content += f"//@{self.__user}:\n"
        content += self.__content
        return self.shrink_line(content)

    @content.setter
    def content(self, content):
        self.__content = content

    @cached_property
    def content_markdown(self):
        content_markdown = str()
        if self.has_forward:
            content_markdown += escape_markdown(self.forward_content)
            if self.uid:
                content_markdown += (
                    f"//{self.make_user_markdown(self.__user, self.uid)}:\n"
                )
            elif self.__user:
                content_markdown += f"//@{escape_markdown(self.__user)}:\n"
        content_markdown += escape_markdown(self.__content)
        if not content_markdown.endswith("\n"):
            content_markdown += "\n"
        if self.extra_markdown:
            content_markdown += self.extra_markdown
        return self.shrink_line(content_markdown)

    @cached_property
    def url(self):
        return f"https://t.bilibili.com/{self.dynamic_id}"


class clip(feed):
    def __init__(self, rawurl):
        super(clip, self).__init__(rawurl)
        self.rawcontent = None
        self.video_id = None
        self.reply_type = 5

    @cached_property
    def url(self):
        return f"https://vc.bilibili.com/video/{self.video_id}"


class audio(feed):
    def __init__(self, rawurl):
        super(audio, self).__init__(rawurl)
        self.infocontent = None
        self.mediacontent = None
        self.audio_id = None
        self.reply_type = 14

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
        self.sid = None
        self.cidcontent = None
        self.infocontent = None
        self.mediacontent = None
        self.reply_type = 1

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/video/av{self.aid}"


def safe_parser(func):
    async def inner_function(*args, **kwargs):
        try:
            try:
                return await func(*args, **kwargs)
            except IntegrityError:
                ## try again with SQL race condition
                return await func(*args, **kwargs)
        except Exception as err:
            if err.__class__ == ParserException:
                logger.error(err)
            else:
                logger.exception(err)
            return err

    return inner_function


@safe_parser
async def reply_parser(client, oid, reply_type):
    if cache := await reply_cache.get_or_none(
        query := Q(Q(oid=oid), Q(reply_type=reply_type)),
        Q(created__gte=datetime.utcnow() - reply_cache.timeout),
    ).first():
        logger.info(f"拉取评论缓存: {cache.created}")
        r = cache.content
    else:
        r = (
            await client.get(
                "https://api.bilibili.com/x/v2/reply",
                params={"oid": oid, "type": reply_type},
            )
        ).json()
    if not r.get("data"):
        raise ParserException("评论解析错误", r.url, r)
    logger.info(f"评论ID: {oid}, 评论类型: {reply_type}")
    if not cache:
        logger.info(f"评论缓存: {oid}")
        if cache := await reply_cache.get_or_none(query).first():
            cache.content = r
            await cache.save(update_fields=["content", "created"])
        else:
            await reply_cache(oid=oid, reply_type=reply_type, content=r).save()
    return r


@safe_parser
async def dynamic_parser(client, url):
    if not (match := re.search(r"[th]\.bilibili\.com[\/\w]*\/(\d+)", url)):
        raise ParserException("动态链接错误", url, match)
    f = dynamic(url)
    if cache := await dynamic_cache.get_or_none(
        query := Q(rid=match.group(1))
        if "type=2" in match.group(0)
        else Q(dynamic_id=match.group(1)),
        Q(created__gte=datetime.utcnow() - dynamic_cache.timeout),
    ).first():
        logger.info(f"拉取动态缓存: {cache.created}")
        f.detailcontent = cache.content
    else:
        r = await client.get(
            "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
            params={"rid": match.group(1), "type": 2}
            if "type=2" in match.group(0) or "h.bilibili.com" in match.group(0)
            else {"dynamic_id": match.group(1)},
        )
        f.detailcontent = r.json()
    if f.detailcontent.get("data").get("card"):
        f.type = f.detailcontent.get("data").get("card").get("desc").get("type")
    else:
        raise ParserException("动态解析错误", r.url, f.detailcontent)
    f.dynamic_id = f.detailcontent.get("data").get("card").get("desc").get("dynamic_id")
    f.rid = f.detailcontent.get("data").get("card").get("desc").get("rid")
    logger.info(f"动态ID: {f.dynamic_id}")
    if not cache:
        logger.info(f"动态缓存: {f.dynamic_id}")
        if cache := await dynamic_cache.get_or_none(query).first():
            cache.content = f.detailcontent
            await cache.save(update_fields=["content", "created"])
        else:
            await dynamic_cache(
                dynamic_id=f.dynamic_id, rid=f.rid, content=f.detailcontent
            ).save()
    # extract from detail.js
    detail_types_list = {
        # REPOST WORD
        "WORD": [1, 4],
        "PIC": [2],
        "VIDEO": [8, 512, *range(4000, 4200)],
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

    # cv article
    if f.origin_type in detail_types_list.get("ARTICLE"):
        cv_id = f.card.get("id")
        f.user = f.card.get("author").get("name")
        f.uid = f.card.get("author").get("mid")
        f.content = f.card.get("dynamic") if f.card.get("dynamic") else str()
        f.extra_markdown = f"[{escape_markdown(f.card.get('title'))}](https://www.bilibili.com/read/cv{cv_id})"
        if f.card.get("banner_url"):
            f.mediaurls = f.card.get("banner_url")
        else:
            f.mediaurls = f.card.get("image_urls")
        f.mediatype = "image"
    # extra parsers
    elif f.origin_type in [
        *detail_types_list.get("MUSIC"),
        *detail_types_list.get("VIDEO"),
        *detail_types_list.get("LIVE"),
    ]:
        # au audio
        if f.origin_type in detail_types_list.get("MUSIC"):
            fu = await audio_parser(client, f'bilibili.com/audio/au{f.card.get("id")}')
            f.content = fu.content
        # live
        elif f.origin_type in detail_types_list.get("LIVE"):
            fu = await live_parser(client, f'live.bilibili.com/{f.card.get("roomid")}')
            f.content = fu.content
        # bv video
        elif f.origin_type in detail_types_list.get("VIDEO"):
            fu = await video_parser(client, f'b23.tv/av{f.card.get("aid")}')
            f.content = f.card.get("new_desc") if f.card.get("new_desc") else fu.content
        else:
            fu = None
        if fu:
            f.user = fu.user
            f.uid = fu.uid
            f.extra_markdown = fu.extra_markdown
            f.mediathumb = fu.mediathumb
            f.mediatitle = fu.mediatitle
            f.mediaduration = fu.mediaduration
            f.mediaurls = fu.mediaurls
            f.mediatype = fu.mediatype
            f.mediaraws = fu.mediaraws
    # dynamic images/videos
    elif f.origin_type in [
        *detail_types_list.get("PIC"),
        *detail_types_list.get("CLIP"),
    ]:
        f.user = f.card.get("user").get("name")
        f.uid = f.card.get("user").get("uid")
        f.content = f'{f.card.get("item").get("title", str())}\n{f.card.get("item").get("description", str())}'
        if f.origin_type in detail_types_list.get("PIC"):
            f.mediaurls = [t.get("img_src") for t in f.card.get("item").get("pictures")]
            f.mediatype = "image"
        elif f.origin_type in detail_types_list.get("CLIP"):
            f.mediaurls = f.card.get("item").get("video_playurl")
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
    f.replycontent = await reply_parser(client, f.oid, f.reply_type)
    return f


@safe_parser
async def clip_parser(client, url):
    if not (match := re.search(r"vc\.bilibili\.com[\D]*(\d+)", url)):
        raise ParserException("短视频链接错误", url, match)
    f = clip(url)
    f.video_id = match.group(1)
    if cache := await clip_cache.get_or_none(
        query := Q(video_id=f.video_id),
        Q(created__gte=datetime.utcnow() - clip_cache.timeout),
    ).first():
        logger.info(f"拉取短视频缓存: {cache.created}")
        f.rawcontent = cache.content
    else:
        r = await client.get(
            "https://api.vc.bilibili.com/clip/v1/video/detail",
            params={"video_id": f.video_id},
        )
        f.rawcontent = r.json()
    if f.rawcontent.get("data").get("user"):
        detail = f.rawcontent.get("data")
    else:
        raise ParserException("短视频解析错误", r.url, f.rawcontent)
    logger.info(f"短视频ID: {f.video_id}")
    if not cache:
        logger.info(f"短视频缓存")
        if cache := await clip_cache.get_or_none(query).first():
            cache.content = f.rawcontent
            await cache.save(update_fields=["content", "created"])
        else:
            await clip_cache(video_id=f.video_id, content=f.rawcontent).save()
    f.user = detail.get("user").get("name")
    f.uid = detail.get("user").get("uid")
    f.content = detail.get("item").get("description")
    f.mediaurls = detail.get("item").get("video_playurl")
    f.mediathumb = detail.get("item").get("first_pic")
    f.mediatype = "video"
    f.replycontent = await reply_parser(client, f.video_id, f.reply_type)
    return f


@safe_parser
async def audio_parser(client, url):
    if not (match := re.search(r"bilibili\.com\/audio\/au(\d+)", url)):
        raise ParserException("音频链接错误", url, match)
    f = audio(url)
    f.audio_id = match.group(1)
    if cache := await audio_cache.get_or_none(
        query := Q(audio_id=f.audio_id),
        Q(created__gte=datetime.utcnow() - audio_cache.timeout),
    ).first():
        logger.info(f"拉取音频缓存: {cache.created}")
        f.infocontent = cache.content
    else:
        r = await client.get(
            "https://api.bilibili.com/audio/music-service-c/songs/playing",
            params={"song_id": f.audio_id},
        )
        f.infocontent = r.json()
    if not (detail := f.infocontent.get("data")):
        raise ParserException("音频解析错误", r.url, f.infocontent)
    logger.info(f"音频ID: {f.audio_id}")
    if not cache:
        logger.info(f"音频缓存: {f.audio_id}")
        if cache := await audio_cache.get_or_none(query).first():
            cache.content = f.infocontent
            await cache.save(update_fields=["content", "created"])
        else:
            await audio_cache(audio_id=f.audio_id, content=f.infocontent).save()
    f.uid = detail.get("mid")
    r = await client.get(
        "https://api.bilibili.com/audio/music-service-c/url",
        params={
            "songid": f.audio_id,
            "mid": f.uid,
            "privilege": 2,
            "quality": 3,
            "platform": "",
        },
    )
    f.mediacontent = r.json()
    f.user = detail.get("author")
    f.content = detail.get("intro")
    f.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({f.url})"
    f.mediathumb = detail.get("cover_url")
    f.mediatitle = detail.get("title")
    f.mediaduration = detail.get("duration")
    f.mediaurls = f.mediacontent.get("data").get("cdns")
    f.mediatype = "audio"
    f.mediaraws = True
    f.replycontent = await reply_parser(client, f.audio_id, f.reply_type)
    return f


@safe_parser
async def live_parser(client, url):
    if not (match := re.search(r"live\.bilibili\.com[\/\w]*\/(\d+)", url)):
        raise ParserException("直播链接错误", url, match)
    f = live(url)
    f.room_id = match.group(1)
    if cache := await live_cache.get_or_none(
        query := Q(room_id=f.room_id),
        Q(created__gte=datetime.utcnow() - live_cache.timeout),
    ).first():
        logger.info(f"拉取直播缓存: {cache.created}")
        f.rawcontent = cache.content
    else:
        r = await client.get(
            "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
            params={"room_id": f.room_id},
        )
        f.rawcontent = r.json()
    if not (detail := f.rawcontent.get("data")):
        raise ParserException("直播解析错误", r.url, f.rawcontent)
    logger.info(f"直播ID: {f.room_id}")
    if not cache:
        logger.info(f"直播缓存: {f.room_id}")
        if cache := await live_cache.get_or_none(query).first():
            cache.content = f.rawcontent
            await cache.save(update_fields=["content", "created"])
        else:
            await live_cache(room_id=f.room_id, content=f.rawcontent).save()
    f.user = detail.get("anchor_info").get("base_info").get("uname")
    roominfo = detail.get("room_info")
    f.uid = roominfo.get("uid")
    f.content = f"{roominfo.get('title')} - {roominfo.get('area_name')} - {roominfo.get('parent_area_name')}"
    f.extra_markdown = f"[{escape_markdown(f.user)}的直播间]({f.url})"
    f.mediaurls = roominfo.get("keyframe")
    f.mediatype = "image"
    return f


@safe_parser
async def video_parser(client, url):
    if not (
        match := re.search(
            r"(?i)(?:bilibili\.com/(?:video|bangumi/play)|b23\.tv|acg\.tv)/(?:(?P<bvid>bv\w+)|av(?P<aid>\d+)|ep(?P<epid>\d+)|ss(?P<ssid>\d+))",
            url,
        )
    ):
        raise ParserException("视频链接错误", url, match)
    f = video(url)
    if bvid := match.group("bvid"):
        params = {"bvid": bvid}
    elif aid := match.group("aid"):
        params = {"aid": aid}
    elif epid := match.group("epid"):
        params = {"ep_id": epid}
    elif ssid := match.group("ssid"):
        params = {"season_id": ssid}
    if "ep_id" in params or "season_id" in params:
        if cache := await bangumi_cache.get_or_none(
            query := Q(
                Q(epid=params.get("ep_id")),
                Q(ssid=params.get("season_id")),
                join_type="OR",
            ),
            Q(created__gte=datetime.utcnow() - video_cache.timeout),
        ).first():
            logger.info(f"拉取番剧缓存: {cache.created}")
            f.infocontent = cache.content
        else:
            r = await client.get(
                "https://api.bilibili.com/pgc/view/web/season", params=params,
            )
            f.infocontent = r.json()
        if not (detail := f.infocontent.get("result")):
            # Anime detects non-China IP
            raise ParserException("番剧解析错误", r.url, f.infocontent)
        f.sid = detail.get("season_id")
        if epid:
            for episode in detail.get("episodes"):
                if str(episode.get("id")) == epid:
                    f.aid = episode.get("aid")
        if not f.aid:
            f.aid = detail.get("episodes")[-1].get("aid")
            epid = detail.get("episodes")[-1].get("id")
        logger.info(f"番剧ID: {epid}")
        if not cache:
            logger.info(f"番剧缓存: {epid}")
            if cache := await bangumi_cache.get_or_none(query).first():
                cache.content = f.infocontent
                await cache.save(update_fields=["content", "created"])
            else:
                await bangumi_cache(epid=epid, ssid=f.sid, content=f.infocontent).save()
        params = {"aid": f.aid}
    # elif "aid" in params or "bvid" in params:
    if cache := await video_cache.get_or_none(
        query := Q(
            Q(aid=params.get("aid")), Q(bvid=params.get("bvid")), join_type="OR"
        ),
        Q(created__gte=datetime.utcnow() - video_cache.timeout),
    ).first():
        logger.info(f"拉取视频缓存: {cache.created}")
        f.infocontent = cache.content
    else:
        r = await client.get(
            "https://api.bilibili.com/x/web-interface/view", params=params,
        )
        # Video detects non-China IP
        f.infocontent = r.json()
    if not (detail := f.infocontent.get("data")):
        raise ParserException("视频解析错误", r.url, f.infocontent)
    bvid = detail.get("bvid")
    f.aid = detail.get("aid")
    f.cid = detail.get("cid")
    logger.info(f"视频ID: {f.aid}")
    if not cache:
        logger.info(f"视频缓存: {f.aid}")
        if cache := await video_cache.get_or_none(query).first():
            cache.content = f.infocontent
            await cache.save(update_fields=["content", "created"])
        else:
            await video_cache(aid=f.aid, bvid=bvid, content=f.infocontent).save()
    f.user = detail.get("owner").get("name")
    f.uid = detail.get("owner").get("mid")
    f.content = detail.get("dynamic")
    f.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({f.url})"
    f.mediatitle = detail.get("title")
    f.mediaurls = detail.get("pic")
    f.mediatype = "image"
    f.replycontent = await reply_parser(client, f.aid, f.reply_type)
    # r = await client.get(
    #     "https://api.bilibili.com/x/player/playurl",
    #     params={"avid": f.aid, "cid": f.cid, "fnval": 16},
    # )
    # f.mediacontent = r.json()
    # f.mediaurls = f.mediacontent.get("data").get("dash").get("video")[0].get("base_url")
    # f.mediathumb = detail.get("pic")
    # f.mediatype = "video"
    # f.mediaraws = True
    return f


@safe_parser
async def feed_parser(client, url, video=True):
    r = await client.get(url)
    url = str(r.url)
    # API link
    print(url)
    if re.search(r"api\..*\.bilibili", url):
        pass
    # dynamic
    elif re.search(r"[th]\.bilibili\.com", url):
        return await dynamic_parser(client, url)
    # live image
    elif re.search(r"live\.bilibili\.com", url):
        return await live_parser(client, url)
    # vc video
    elif re.search(r"vc\.bilibili\.com", url):
        return await clip_parser(client, url)
    # au audio
    elif re.search(r"bilibili\.com/audio", url):
        return await audio_parser(client, url)
    # main video
    elif re.search(r"bilibili\.com/(?:video|bangumi/play)", url):
        if video:
            return await video_parser(client, url)
        else:
            logger.info(f"暂不匹配视频内容: {url}")
    raise ParserException("URL错误", url)


def db_init(func):
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    async def inner_function(*args, **kwargs):
        await Tortoise.init(
            db_url="sqlite://cache.db", modules={"models": ["database"]}
        )
        await Tortoise.generate_schemas()
        result = await func(*args, **kwargs)
        await Tortoise.close_connections()
        return result

    return inner_function


@db_init
async def biliparser(urls, video=True):
    if isinstance(urls, str):
        urls = [urls]
    elif isinstance(urls, tuple):
        urls = list(urls)
    async with httpx.AsyncClient(
        headers=headers, http2=True, timeout=None, verify=False
    ) as client:
        tasks = list(
            feed_parser(
                client,
                f"http://{url}" if not url.startswith(("http:", "https:")) else url,
                video,
            )
            for url in urls
        )
    callbacks = await asyncio.gather(*tasks)
    for num, f in enumerate(callbacks):
        if isinstance(f, Exception):
            logger.warn(f"排序: {num}\n异常: {f}\n")
        else:
            logger.debug(
                f"排序: {num}\n"
                f"类型: {type(f)}\n"
                f"链接: {f.url}\n"
                f"用户: {f.user_markdown}\n"
                f"内容: {f.content_markdown}\n"
                f"评论: {f.comment_markdown}\n"
                f"媒体: {f.mediaurls}\n"
                f"媒体种类: {f.mediatype}\n"
                f"媒体预览: {f.mediathumb}\n"
                f"媒体标题: {f.mediatitle}\n"
                f"媒体文件名: {f.mediafilename}"
            )
    return callbacks
