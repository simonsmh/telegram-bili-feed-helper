import asyncio
import html
import json
import os
import re
from functools import lru_cache, reduce
from io import BytesIO

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from telegram.constants import FileSizeLimit
from telegraph.aio import Telegraph
from tortoise import timezone
from tortoise.exceptions import IntegrityError
from tortoise.expressions import Q

from database import (
    audio_cache,
    bangumi_cache,
    dynamic_cache,
    live_cache,
    read_cache,
    reply_cache,
    video_cache,
)
from utils import BILI_API, LOCAL_MODE, compress, headers, logger

try:
    from functools import cached_property
except ImportError:
    cached_property = property


def escape_markdown(text):
    return (
        re.sub(r"([_*\[\]()~`>\#\+\-=|{}\.!\\])", r"\\\1", html.unescape(text))
        if text
        else str()
    )


class ParserException(Exception):
    def __init__(self, msg, url, res=None):
        self.msg = msg
        self.url = url
        self.res = str(res) if res else None

    def __str__(self):
        return f"{self.msg}: {self.url} ->\n{self.res}"


class feed:
    user: str = ""
    uid: str = ""
    __content: str = ""
    __mediaurls: list = []
    mediaraws: bool = False
    mediatype: str = ""
    mediathumb: str = ""
    mediaduration: int = 0
    mediadimention: dict = {"width": 0, "height": 0, "rotate": 0}
    mediatitle: str = ""
    extra_markdown: str = ""
    replycontent: dict = {}

    def __init__(self, rawurl):
        self.rawurl = rawurl

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
        # if self.extra_markdown:
        #     content_markdown += self.extra_markdown
        return self.shrink_line(content_markdown)

    @cached_property
    def comment(self):
        comment = str()
        if isinstance(self.replycontent, dict):
            top = self.replycontent.get("top")
            if top:
                for item in top.values():
                    if item:
                        comment += f'🔝> @{item["member"]["uname"]}:\n{item["content"]["message"]}\n'
        return self.shrink_line(comment)

    @cached_property
    def comment_markdown(self):
        comment_markdown = str()
        if isinstance(self.replycontent, dict):
            top = self.replycontent.get("top")
            if top:
                for item in top.values():
                    if item:
                        comment_markdown += f'🔝\\> {self.make_user_markdown(item["member"]["uname"], item["member"]["mid"])}:\n{escape_markdown(item["content"]["message"])}\n'
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
        def get_filename(url) -> str:
            target = re.search(r"\/([^\/]*\.\w{3,4})(?:$|\?)", url)
            if target:
                return target.group(1)
            return str()

        return (
            [get_filename(i) for i in self.__mediaurls] if self.__mediaurls else list()
        )

    @cached_property
    def url(self):
        return self.rawurl


class opus(feed):
    detailcontent: dict = {}
    dynamic_id: int = 0
    user: str = ""
    __content: str = ""
    forward_user: str = ""
    forward_uid: int = 0
    forward_content: str = ""
    has_forward: bool = False

    @cached_property
    def reply_type(self):
        if self.rtype == 2:
            return 11
        if self.rtype == 16:
            return 5
        if self.rtype == 64:
            return 12
        if self.rtype == 256:
            return 14
        if self.rtype in [8, 512, *range(4000, 4200)]:
            return 1
        if self.rtype in [1, 4, *range(4200, 4300), *range(2048, 2100)]:
            return 17

    @cached_property
    def rtype(self):
        return int(self.detailcontent["item"]["basic"]["rtype"])

    @cached_property
    def rid(self):
        return int(self.detailcontent["item"]["basic"]["rid_str"])

    @property
    @lru_cache(maxsize=1)
    def content(self):
        content = self.__content
        if self.has_forward:
            if self.forward_user:
                content += f"//@{self.forward_user}:\n"
            content += self.forward_content
        return self.shrink_line(content)

    @content.setter
    def content(self, content):
        self.__content = content

    @cached_property
    def content_markdown(self):
        content_markdown = escape_markdown(self.__content)
        if self.has_forward:
            if self.uid:
                content_markdown += f"//{self.make_user_markdown(self.forward_user, self.forward_uid)}:\n"
            elif self.user:
                content_markdown += f"//@{escape_markdown(self.forward_user)}:\n"
            content_markdown += escape_markdown(self.forward_content)
        if not content_markdown.endswith("\n"):
            content_markdown += "\n"
        return self.shrink_line(content_markdown)

    @cached_property
    def url(self):
        return f"https://t.bilibili.com/{self.dynamic_id}"


class audio(feed):
    infocontent: dict = {}
    mediacontent: str = ""
    audio_id: int = 0
    reply_type: int = 14

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/audio/au{self.audio_id}"


class live(feed):
    rawcontent: dict = {}
    room_id: int = 0

    @cached_property
    def url(self):
        return f"https://live.bilibili.com/{self.room_id}"


class video(feed):
    aid: int = 0
    cid: int = 0
    sid: int = 0
    cidcontent: dict = {}
    infocontent: dict = {}
    mediacontent: dict = {}
    reply_type: int = 1

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/video/av{self.aid}"


class read(feed):
    rawcontent: str = ""
    read_id: int = 0
    reply_type: int = 12

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/read/cv{self.read_id}"


def safe_parser(func):
    async def inner_function(*args, **kwargs):
        try:
            try:
                return await func(*args, **kwargs)
            except IntegrityError as err:
                logger.error(err)
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
async def reply_parser(client: httpx.AsyncClient, oid, reply_type):
    query = Q(Q(oid=oid), Q(reply_type=reply_type))
    cache = await reply_cache.get_or_none(
        query,
        Q(created__gte=timezone.now() - reply_cache.timeout),
    )
    if cache:
        logger.info(f"拉取评论缓存: {cache.created}")
        reply = cache.content
    else:
        r = await client.get(
            BILI_API + "/x/v2/reply/main",
            params={"oid": oid, "type": reply_type},
            headers={"Referer": "https://www.bilibili.com/client"},
        )
        response = r.json()
        if not response.get("data"):
            logger.warning(f"评论ID: {oid}, 评论类型: {reply_type}, 获取错误: {response}")
            return {}
        reply = response.get("data")
        if not reply:
            logger.warning(f"评论ID: {oid}, 评论类型: {reply_type}, 解析错误: {response}")
            return {}
            # raise ParserException("评论解析错误", reply, r)
    logger.info(f"评论ID: {oid}, 评论类型: {reply_type}")
    if not cache:
        logger.info(f"评论缓存: {oid}")
        cache = await reply_cache.get_or_none(query)
        try:
            if cache:
                cache.content = reply
                await cache.save(update_fields=["content", "created"])
            else:
                await reply_cache(oid=oid, reply_type=reply_type, content=reply).save()
        except Exception as e:
            logger.exception(f"评论缓存错误: {e}")
    return reply


def __list_dicts_to_dict(lists: list[dict]):
    return reduce(lambda old, new: old.update(new) or old, lists, {})


def __opus_handle_major(f: opus, major: dict):
    datapath_map = {
        "MDL_DYN_TYPE_ARCHIVE": "dyn_archive",
        "MDL_DYN_TYPE_PGC": "dyn_pgc",
        "MDL_DYN_TYPE_ARTICLE": "dyn_article",
        "MDL_DYN_TYPE_MUSIC": "dyn_music",
        "MDL_DYN_TYPE_COMMON": "dyn_common",
        "MDL_DYN_TYPE_LIVE": "dyn_live",
        "MDL_DYN_TYPE_UGC_SEASON": "dyn_ugc_season",
        "MDL_DYN_TYPE_DRAW": "dyn_draw",
        "MDL_DYN_TYPE_OPUS": "dyn_opus",
        "MDL_DYN_TYPE_FORWARD": "dyn_forward",
    }
    if not major:
        return
    target = datapath_map.get(major["type"])
    if major["type"] == "MDL_DYN_TYPE_FORWARD":
        f.has_forward = True
        majorcontent = __list_dicts_to_dict(major[target]["item"]["modules"])
        f.forward_user = majorcontent["module_author"]["user"]["name"]
        f.forward_uid = majorcontent["module_author"]["user"]["mid"]
        if majorcontent.get("module_desc"):
            f.forward_content = __opus_handle_desc_text(majorcontent["module_desc"])
        if not f.mediatype and majorcontent.get("module_dynamic"):
            __opus_handle_major(f, majorcontent["module_dynamic"])
    elif major["type"] == "MDL_DYN_TYPE_DRAW":
        f.mediaurls = [item["src"] for item in major[target]["items"]]
        f.mediatype = "image"
    elif datapath_map.get(major["type"]):
        if major[target].get("cover"):
            f.mediaurls = major[target]["cover"]
            f.mediatype = "image"
        if major[target].get("aid") and major[target].get("title"):
            f.extra_markdown = f"[{escape_markdown(major[target]['title'])}](https://www.bilibili.com/video/av{major[target]['aid']})"


def __opus_handle_desc_text(desc: dict):
    if not desc:
        return ""
    return desc["text"]


@safe_parser
async def opus_parser(client: httpx.AsyncClient, url: str):
    match = re.search(r"bilibili\.com[\/\w]*\/(\d+)", url)
    if not match:
        raise ParserException("动态链接错误", url)
    f = opus(url)
    f.dynamic_id = int(match.group(1))
    query = (
        Q(rid=match.group(1))
        if "type=2" in match.group(0)
        else Q(dynamic_id=match.group(1))
    )
    cache = await dynamic_cache.get_or_none(
        query,
        Q(created__gte=timezone.now() - dynamic_cache.timeout),
    )
    if cache:
        logger.info(f"拉取opus动态缓存: {cache.created}")
        f.detailcontent = cache.content
    else:
        r = await client.get(
            BILI_API + "/x/polymer/web-dynamic/desktop/v1/detail",
            params={"id": f.dynamic_id},
        )
        response = r.json()
        if not response.get("data"):
            raise ParserException("opus动态获取错误", url, response)
        f.detailcontent = response["data"]
        if not f.detailcontent.get("item"):
            raise ParserException("opus动态解析错误", url, f.detailcontent)
    logger.info(f"动态ID: {f.dynamic_id}")
    if not cache:
        logger.info(f"动态缓存: {f.dynamic_id}")
        cache = await dynamic_cache.get_or_none(query)
        try:
            if cache:
                cache.content = f.detailcontent
                await cache.save(update_fields=["content", "created"])
            else:
                await dynamic_cache(
                    dynamic_id=f.dynamic_id, rid=f.rid, content=f.detailcontent
                ).save()
        except Exception as e:
            logger.exception(f"动态缓存错误: {e}")
    detailcontent = __list_dicts_to_dict(f.detailcontent["item"]["modules"])
    f.user = detailcontent["module_author"]["user"]["name"]
    f.uid = detailcontent["module_author"]["user"]["mid"]
    if detailcontent.get("module_desc"):
        f.content = __opus_handle_desc_text(detailcontent["module_desc"])
    if detailcontent.get("module_dynamic"):
        __opus_handle_major(f, detailcontent["module_dynamic"])
    f.replycontent = await reply_parser(client, f.rid, f.reply_type)
    return f


@safe_parser
async def audio_parser(client: httpx.AsyncClient, url: str):
    match = re.search(r"bilibili\.com\/audio\/au(\d+)", url)
    if not match:
        raise ParserException("音频链接错误", url)
    f = audio(url)
    f.audio_id = int(match.group(1))
    query = Q(audio_id=f.audio_id)
    cache = await audio_cache.get_or_none(
        query,
        Q(created__gte=timezone.now() - audio_cache.timeout),
    )
    if cache:
        logger.info(f"拉取音频缓存: {cache.created}")
        f.infocontent = cache.content
        detail = f.infocontent["data"]
    else:
        r = await client.get(
            BILI_API + "/audio/music-service-c/songs/playing",
            params={"song_id": f.audio_id},
        )
        f.infocontent = r.json()
        detail = f.infocontent.get("data")
        if not detail:
            raise ParserException("音频解析错误", r.url, f.infocontent)
    logger.info(f"音频ID: {f.audio_id}")
    if not cache:
        logger.info(f"音频缓存: {f.audio_id}")
        cache = await audio_cache.get_or_none(query)
        try:
            if cache:
                cache.content = f.infocontent
                await cache.save(update_fields=["content", "created"])
            else:
                await audio_cache(audio_id=f.audio_id, content=f.infocontent).save()
        except Exception as e:
            logger.exception(f"音频缓存错误: {e}")
    f.uid = detail.get("mid")
    r = await client.get(
        BILI_API + "/audio/music-service-c/url",
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
async def live_parser(client: httpx.AsyncClient, url: str):
    match = re.search(r"live\.bilibili\.com[\/\w]*\/(\d+)", url)
    if not match:
        raise ParserException("直播链接错误", url)
    f = live(url)
    f.room_id = int(match.group(1))
    query = Q(room_id=f.room_id)
    cache = await live_cache.get_or_none(
        query,
        Q(created__gte=timezone.now() - live_cache.timeout),
    )
    if cache:
        logger.info(f"拉取直播缓存: {cache.created}")
        f.rawcontent = cache.content
        detail = f.rawcontent.get("data")
    else:
        r = await client.get(
            "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
            params={"room_id": f.room_id},
        )
        f.rawcontent = r.json()
        detail = f.rawcontent.get("data")
        if not detail:
            raise ParserException("直播解析错误", r.url, f.rawcontent)
    logger.info(f"直播ID: {f.room_id}")
    if not cache:
        logger.info(f"直播缓存: {f.room_id}")
        cache = await live_cache.get_or_none(query)
        try:
            if cache:
                cache.content = f.rawcontent
                await cache.save(update_fields=["content", "created"])
            else:
                await live_cache(room_id=f.room_id, content=f.rawcontent).save()
        except Exception as e:
            logger.exception(f"直播缓存错误: {e}")
    if not detail:
        raise ParserException("直播内容获取错误", f.url)
    f.user = detail["anchor_info"]["base_info"]["uname"]
    roominfo = detail.get("room_info")
    f.uid = roominfo.get("uid")
    f.content = f"{roominfo.get('title')} - {roominfo.get('area_name')} - {roominfo.get('parent_area_name')}"
    f.extra_markdown = f"[{escape_markdown(f.user)}的直播间]({f.url})"
    f.mediaurls = roominfo.get("keyframe")
    f.mediatype = "image"
    return f


@safe_parser
async def video_parser(client: httpx.AsyncClient, url: str):
    match = re.search(
        r"(?i)(?:bilibili\.com/(?:video|bangumi/play|festival)|b23\.tv|acg\.tv)/(?:(?P<bvid>bv\w+)|av(?P<aid>\d+)|ep(?P<epid>\d+)|ss(?P<ssid>\d+)|(?P<festivalid>\w+))",
        url,
    )
    if not match:
        raise ParserException("视频链接错误", url)
    f = video(url)
    epid = match.group("epid")
    bvid = match.group("bvid")
    aid = match.group("aid")
    ssid = match.group("ssid")
    festivalid = match.group("festivalid")
    if epid:
        params = {"ep_id": epid}
    elif bvid:
        params = {"bvid": bvid}
    elif aid:
        params = {"aid": aid}
    elif ssid:
        params = {"season_id": ssid}
    else:
        params = {}
    if "ep_id" in params or "season_id" in params:
        query = Q(
            Q(epid=params.get("ep_id")),
            Q(ssid=params.get("season_id")),
            join_type="OR",
        )
        cache = await bangumi_cache.get_or_none(
            query,
            Q(created__gte=timezone.now() - video_cache.timeout),
        )
        if cache:
            logger.info(f"拉取番剧缓存: {cache.created}")
            f.infocontent = cache.content
        else:
            r = await client.get(
                BILI_API + "/pgc/view/web/season",
                params=params,
            )
            f.infocontent = r.json()
        detail = f.infocontent.get("result")
        if not detail:
            # Anime detects non-China IP
            raise ParserException("番剧解析错误", url, f.infocontent)
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
            cache = await bangumi_cache.get_or_none(query)
            try:
                if cache:
                    cache.content = f.infocontent
                    await cache.save(update_fields=["content", "created"])
                else:
                    await bangumi_cache(
                        epid=epid, ssid=f.sid, content=f.infocontent
                    ).save()
            except Exception as e:
                logger.exception(f"番剧缓存错误: {e}")
        params = {"aid": f.aid}
    # elif "aid" in params or "bvid" in params:
    query = Q(Q(aid=params.get("aid")), Q(bvid=params.get("bvid")), join_type="OR")
    cache = await video_cache.get_or_none(
        query,
        Q(created__gte=timezone.now() - video_cache.timeout),
    )
    if cache:
        logger.info(f"拉取视频缓存: {cache.created}")
        f.infocontent = cache.content
        detail = f.infocontent.get("data")
    else:
        r = await client.get(
            BILI_API + "/x/web-interface/view",
            params=params,
        )
        # Video detects non-China IP
        f.infocontent = r.json()
        detail = f.infocontent.get("data")
        if not detail:
            raise ParserException("视频解析错误", r.url, f.infocontent)
    if not detail:
        raise ParserException("视频内容获取错误", f.url)
    bvid = detail.get("bvid")
    f.aid = detail.get("aid")
    f.cid = detail.get("cid")
    logger.info(f"视频ID: {f.aid}")
    if not cache:
        logger.info(f"视频缓存: {f.aid}")
        cache = await video_cache.get_or_none(query)
        try:
            if cache:
                cache.content = f.infocontent
                await cache.save(update_fields=["content", "created"])
            else:
                await video_cache(aid=f.aid, bvid=bvid, content=f.infocontent).save()
        except Exception as e:
            logger.exception(f"视频缓存错误: {e}")
    f.user = detail.get("owner").get("name")
    f.uid = detail.get("owner").get("mid")
    f.content = detail.get("dynamic", detail.get("desc"))
    f.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({f.url})"
    f.mediatitle = detail.get("title")
    f.mediaurls = detail.get("pic")
    f.mediatype = "image"
    f.replycontent = await reply_parser(client, f.aid, f.reply_type)

    async def get_video_result(client: httpx.AsyncClient, f: video, detail, qn: int):
        params = {"avid": f.aid, "cid": f.cid}
        if qn:
            params["qn"] = qn
        r = await client.get(
            BILI_API + "/x/player/playurl",
            params=params,
        )
        video_result = r.json()
        logger.debug(f"视频内容: {video_result}")
        if (
            video_result.get("code") == 0
            and video_result.get("data")
            and video_result.get("data").get("durl")
            and video_result.get("data").get("durl")[0].get("size")
            < (
                int(
                    os.environ.get(
                        "VIDEO_SIZE_LIMIT", FileSizeLimit.FILESIZE_UPLOAD_LOCAL_MODE
                    )
                )
                if LOCAL_MODE
                else FileSizeLimit.FILESIZE_UPLOAD
            )
        ):

            async def test_url_status_code(url):
                header = headers.copy()
                header["Referer"] = f.url
                async with client.stream("GET", url, headers=header) as response:
                    if response.status_code != 200:
                        return False
                    return True

            url = video_result["data"]["durl"][0]["url"]
            result = await test_url_status_code(url)
            if not result and video_result["data"]["durl"][0].get("backup_url", None):
                url = video_result["data"]["durl"][0]["backup_url"]
                result = await test_url_status_code(url)
            if result:
                f.mediacontent = video_result
                f.mediathumb = detail.get("pic")
                f.mediaduration = round(
                    video_result["data"]["durl"][0]["length"] / 1000
                )
                f.mediadimention = detail.get("pages")[0].get("dimension")
                f.mediaurls = url
                f.mediatype = "video"
                f.mediaraws = (
                    False
                    if video_result.get("data").get("durl")[0].get("size")
                    < (
                        FileSizeLimit.FILESIZE_DOWNLOAD_LOCAL_MODE
                        if LOCAL_MODE
                        else FileSizeLimit.FILESIZE_DOWNLOAD
                    )
                    else True
                )
                return True

    for item in [64, 32, 16]:
        if await get_video_result(client, f, detail, item):
            break
    return f


@safe_parser
async def read_parser(client: httpx.AsyncClient, url: str):
    async def relink(img):
        src = img.attrs.pop("data-src")
        img.attrs = {"src": src}
        logger.info(f"下载图片: {src}")
        async with httpx.AsyncClient(
            headers=headers, http2=True, timeout=90, follow_redirects=True
        ) as client:
            r = await client.get(f"https:{src}")
            media = BytesIO(r.read())
            content_length = int(r.headers.get("content-length"))
            if content_length > 1024 * 1024 * 5:
                mediatype = r.headers.get("content-type")
                if mediatype in ["image/jpeg", "image/png"]:
                    logger.info(f"图片大小: {content_length} 压缩: {src} {mediatype}")
                    media = compress(media)
            try:
                resp = await telegraph.upload_file(media)
                logger.info(f"图片上传: {resp}")
                img.attrs["src"] = f"https://telegra.ph{resp[0].get('src')}"
            except Exception as e:
                logger.exception(f"图片上传错误: {e}")

    match = re.search(r"bilibili\.com\/read\/(?:cv|mobile\/|mobile\?id=)(\d+)", url)
    if not match:
        raise ParserException("文章链接错误", url)
    f = read(url)
    f.read_id = int(match.group(1))
    r = await client.get(f"https://www.bilibili.com/read/cv{f.read_id}")
    cv_init = re.search(r"window\.__INITIAL_STATE__=(.*?);\(function\(\)", r.text)
    if not cv_init:
        raise ParserException("文章内容获取错误", url, cv_init)
    cv_content = json.loads(cv_init.group(1))
    f.uid = cv_content.get("readInfo").get("author").get("mid")
    f.user = cv_content.get("readInfo").get("author").get("name")
    f.content = cv_content.get("readInfo").get("summary")
    mediaurls = (
        cv_content.get("readInfo").get("banner_url")
        if cv_content.get("readInfo").get("banner_url")
        else cv_content.get("readInfo").get("image_urls")
    )
    if mediaurls:
        logger.info(f"文章mediaurls: {mediaurls}")
        f.mediaurls = mediaurls
        f.mediatype = "image"
    title = cv_content.get("readInfo").get("title")
    logger.info(f"文章ID: {f.read_id}")
    query = Q(read_id=f.read_id)
    cache = await read_cache.get_or_none(
        query,
        Q(created__gte=timezone.now() - audio_cache.timeout),
    )
    if cache:
        logger.info(f"拉取文章缓存: {cache.created}")
        graphurl = cache.graphurl
    else:
        article_content = cv_content.get("readInfo").get("content")
        telegraph = Telegraph()
        if not telegraph.get_access_token():
            await telegraph.create_account(
                "bilifeedbot", "bilifeedbot", "https://t.me/bilifeedbot"
            )
        try:
            article = json.loads(article_content)
            result = article.get("ops")[0].get("insert").split("\n")
            logger.info(result)
            graphurl = (
                await telegraph.create_page(
                    title=title,
                    content=result,
                    author_name=f.user,
                    author_url=f"https://space.bilibili.com/{f.uid}",
                )
            ).get("url")
        except json.decoder.JSONDecodeError:
            article = BeautifulSoup(article_content, "lxml")
            if not isinstance(article, Tag):
                ParserException("文章内容解析错误", url, cv_init)
            imgs = article.find_all("img")
            task = list(relink(img) for img in imgs)  ## data-src -> src
            for _ in article.find_all("h1"):  ## h1 -> h3
                _.name = "h3"
            for item in ["span", "div"]:  ## remove tags
                for _ in article.find_all(item):
                    _.unwrap()
            for item in ["p", "figure", "figcaption"]:  ## clean tags
                for _ in article.find_all(item):
                    _.attrs = {}
            await asyncio.gather(*task)
            result = "".join(
                [str(i) for i in article.body.contents]
            )  ## convert tags to string
            graphurl = (
                await telegraph.create_page(
                    title=title,
                    html_content=result,
                    author_name=f.user,
                    author_url=f"https://space.bilibili.com/{f.uid}",
                )
            ).get("url")
        logger.info(f"生成页面: {graphurl}")
        logger.info(f"文章缓存: {f.read_id}")
        cache = await read_cache.get_or_none(query)
        try:
            if cache:
                cache.graphurl = graphurl
                await cache.save(update_fields=["graphurl", "created"])
            else:
                await read_cache(read_id=f.read_id, graphurl=graphurl).save()
        except Exception as e:
            logger.exception(f"文章缓存失败: {e}")
    f.extra_markdown = f"[{escape_markdown(title)}]({graphurl})"
    f.replycontent = await reply_parser(client, f.read_id, f.reply_type)
    return f


@safe_parser
async def feed_parser(client: httpx.AsyncClient, url: str):
    r = await client.get(url)
    url = str(r.url)
    logger.debug(f"URL: {url}")
    # main video
    if re.search(r"video|bangumi/play|festival", url):
        return await video_parser(client, url)
    # au audio
    elif "read" in url:
        return await read_parser(client, url)
    # au audio
    elif "audio" in url:
        return await audio_parser(client, url)
    # live image
    elif "live" in url:
        return await live_parser(client, url)
    # dynamic
    elif re.search(r"[th]\.|dynamic|opus", url):
        return await opus_parser(client, url)
    # API link blackboard link user space link
    if re.search(r"api\..*\.bilibili|blackboard|space\.bilibili", url):
        pass
    raise ParserException("URL错误", url)


async def biliparser(urls) -> list[feed]:
    logger.debug(BILI_API)
    if isinstance(urls, str):
        urls = [urls]
    elif isinstance(urls, tuple):
        urls = list(urls)
    async with httpx.AsyncClient(
        headers=headers, http2=True, timeout=90, follow_redirects=True
    ) as client:
        tasks = list(
            feed_parser(
                client,
                f"http://{url}" if not url.startswith(("http:", "https:")) else url,
            )
            for url in list(set(urls))
        )
        callbacks = await asyncio.gather(*tasks)
    for num, f in enumerate(callbacks):
        if isinstance(f, Exception):
            logger.warning(f"排序: {num}\n异常: {f}\n")
        else:
            logger.debug(
                f"排序: {num}\n"
                f"类型: {type(f)}\n"
                f"链接: {f.url}\n"
                f"用户: {f.user_markdown}\n"
                f"内容: {f.content_markdown}\n"
                f"附加内容: {f.extra_markdown}\n"
                f"评论: {f.comment_markdown}\n"
                f"媒体: {f.mediaurls}\n"
                f"媒体种类: {f.mediatype}\n"
                f"媒体预览: {f.mediathumb}\n"
                f"媒体标题: {f.mediatitle}\n"
                f"媒体文件名: {f.mediafilename}"
            )
    return callbacks
