import os
import re
from functools import cached_property

import httpx
import orjson
from telegram.constants import FileSizeLimit

from ..cache import CACHES_TIMER, RedisCache
from ..utils import (
    BILI_API,
    LOCAL_MODE,
    ParserException,
    escape_markdown,
    headers,
    logger,
)
from .feed import Feed

from urllib.parse import urlparse, parse_qs

QN = [64, 32, 16]


class Video(Feed):
    cidcontent: dict = {}
    epcontent: dict = {}
    infocontent: dict = {}
    page = 1
    reply_type: int = 1

    @cached_property
    def cid(self):
        if self.infocontent and self.infocontent.get("data"):
            if self.page != 1 and self.infocontent["data"].get("pages"):
                for item in self.infocontent["data"]["pages"]:
                    if item.get("page") == self.page:
                        return item.get("cid")
            self.page = 1
            return self.infocontent["data"].get("cid")

    @cached_property
    def bvid(self):
        if self.infocontent and self.infocontent.get("data"):
            return self.infocontent["data"].get("bvid")

    @cached_property
    def aid(self):
        if self.infocontent and self.infocontent.get("data"):
            return self.infocontent["data"].get("aid")
        elif self.epid and self.epcontent and self.epcontent.get("result"):
            for episode in self.epcontent["result"].get("episodes"):
                if str(episode.get("id")) == self.epid:
                    return episode.get("aid")

    @cached_property
    def epid(self):
        if (
            self.epcontent
            and self.epcontent.get("result")
            and self.epcontent["result"].get("episodes")
        ):
            if not self.aid:
                self.aid = self.epcontent["result"]["episodes"][-1].get("aid")
            return self.epcontent["result"]["episodes"][-1].get("id")

    @cached_property
    def ssid(self):
        if self.epcontent and self.epcontent.get("result"):
            return self.epcontent["result"].get("season_id")

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/video/av{self.aid}?p={self.page}"

    async def __test_url_status_code(self, url, referer):
        header = headers.copy()
        header["Referer"] = referer
        async with self.client.stream("GET", url, headers=header) as response:
            if response.status_code != 200:
                return False
            return True

    async def __get_video_result(self, detail, qn: int):
        params = {"avid": self.aid, "cid": self.cid}
        if qn:
            params["qn"] = qn
        r = await self.client.get(
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
            url = video_result["data"]["durl"][0]["url"]
            result = await self.__test_url_status_code(url, self.url)
            if not result and video_result["data"]["durl"][0].get("backup_url", None):
                url = video_result["data"]["durl"][0]["backup_url"]
                result = await self.__test_url_status_code(url, self.url)
            if result:
                self.mediacontent = video_result
                self.mediathumb = detail.get("pic")
                self.mediaduration = round(
                    video_result["data"]["durl"][0]["length"] / 1000
                )
                self.mediadimention = detail.get("pages")[0].get("dimension")
                self.mediaurls = url
                self.mediatype = "video"
                self.mediaraws = (
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

    async def handle(self):
        logger.info(f"处理视频信息: 链接: {self.rawurl}")
        match = re.search(
            r"(?:bilibili\.com/(?:video|bangumi/play)|b23\.tv|acg\.tv)/(?:(?P<bvid>BV\w{10})|av(?P<aid>\d+)|ep(?P<epid>\d+)|ss(?P<ssid>\d+)|)/?\??(?:p=(?P<page>\d+))?",
            self.rawurl,
        )
        match_fes = re.search(
            r"bilibili\.com/festival/(?P<festivalid>\w+)\?(?:bvid=(?P<bvid>BV\w{10}))",
            self.rawurl,
        )
        pr = urlparse(self.rawurl)
        qs = parse_qs(pr.query)
        seek_id = None
        if "comment_secondary_id" in qs:
            seek_id = qs["comment_secondary_id"][0]
        elif "comment_root_id" in qs:
            seek_id = qs["comment_root_id"][0]
        elif pr.fragment.startswith("reply"):
            seek_id = pr.fragment.removeprefix("reply")
        if match_fes:
            bvid = match_fes.group("bvid")
            epid = None
            aid = None
            ssid = None
            page = 1
        elif match:
            bvid = match.group("bvid")
            epid = match.group("epid")
            aid = match.group("aid")
            ssid = match.group("ssid")
            page = match.group("page")
            if page and page.isdigit():
                page = max(1, int(page))
            else:
                page = 1
        else:
            raise ParserException("视频链接错误", self.rawurl)
        if epid:
            params = {"ep_id": epid}
        elif bvid:
            params = {"bvid": bvid}
        elif aid:
            params = {"aid": aid}
        elif ssid:
            params = {"season_id": ssid}
        else:
            raise ParserException("视频链接解析错误", self.rawurl)
        self.page = page
        if epid:
            self.epid = epid
        if epid is not None or ssid is not None:
            # 1.获取缓存
            try:
                cache = (
                    RedisCache().get(f"bangumi:ep:{epid}")
                    if epid
                    else RedisCache().get(f"bangumi:ss:{ssid}")
                )
            except Exception as e:
                logger.exception(f"拉取番剧缓存错误: {e}")
                cache = None
            # 2.拉取番剧
            if cache:
                logger.info(
                    f"拉取番剧缓存:epid {epid}" if epid else f"拉取番剧缓存:ssid {ssid}"
                )
                self.epcontent = orjson.loads(cache)  # type: ignore
            else:
                try:
                    r = await self.client.get(
                        BILI_API + "/pgc/view/web/season",
                        params=params,
                    )
                    self.epcontent = r.json()
                except Exception as e:
                    raise ParserException(
                        f"番剧获取错误:{epid if epid else ssid}", self.rawurl, e
                    )
                # 3.番剧解析
                if not self.epcontent or not self.epcontent.get("result"):
                    # Anime detects non-China IP
                    raise ParserException(
                        f"番剧解析错误:{epid if epid else ssid} {self.epcontent}",
                        self.rawurl,
                        self.epcontent,
                    )
                if not self.epid or not self.ssid or not self.aid:
                    raise ParserException(
                        f"番剧解析错误:{self.aid} {self.ssid} {self.aid}",
                        self.rawurl,
                        self.epcontent,
                    )
                # 4.缓存评论
                try:
                    for key in [f"bangumi:ep:{self.epid}", f"bangumi:ss:{self.ssid}"]:
                        RedisCache().set(
                            key,
                            orjson.dumps(self.epcontent),
                            ex=CACHES_TIMER.get("bangumi"),
                            nx=True,
                        )
                except Exception as e:
                    logger.exception(f"缓存番剧错误: {e}")
            params = {"aid": self.aid}
            aid = self.aid
        # 1.获取缓存
        try:
            cache = (
                RedisCache().get(f"video:aid:{aid}")
                if aid
                else RedisCache().get(f"video:bvid:{bvid}")
            )
        except Exception as e:
            logger.exception(f"拉取视频缓存错误: {e}")
            cache = None
        # 2.拉取视频
        if cache:
            logger.info(f"拉取视频缓存:{aid if aid else bvid}")
            self.infocontent = orjson.loads(cache)  # type: ignore
        else:
            try:
                r = await self.client.get(
                    BILI_API + "/x/web-interface/view",
                    params=params,
                )
                self.infocontent = r.json()
            except Exception as e:
                raise ParserException(
                    f"视频获取错误:{aid if aid else bvid}", self.rawurl, e
                )
            # 3.视频解析
            if not self.infocontent and not self.infocontent.get("data"):
                # Video detects non-China IP
                raise ParserException(
                    f"视频解析错误{aid if aid else bvid}", r.url, self.infocontent
                )
            if not self.aid or not self.bvid or not self.cid:
                raise ParserException(
                    f"视频解析错误:{self.aid} {self.bvid} {self.cid}",
                    self.rawurl,
                    self.epcontent,
                )
            # 4.缓存视频
            try:
                for key in [f"video:aid:{self.aid}", f"video:bvid:{self.bvid}"]:
                    RedisCache().set(
                        key,
                        orjson.dumps(self.infocontent),
                        ex=CACHES_TIMER.get("video"),
                        nx=True,
                    )
            except Exception as e:
                logger.exception(f"缓存番剧错误: {e}")
        detail = self.infocontent["data"]
        self.user = detail.get("owner").get("name")
        self.uid = detail.get("owner").get("mid")
        self.content = detail.get("tname", "发布视频")
        if detail.get("pages") and len(detail["pages"]) > 1:
            self.content += f" - 第{page}P/共{len(detail['pages'])}P"
        if detail.get("dynamic") or detail.get("desc"):
            self.content += f" - {detail.get('dynamic') or detail.get('desc')}"
        self.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({self.url})"
        self.mediatitle = detail.get("title")
        self.mediaurls = detail.get("pic")
        self.mediatype = "image"
        self.replycontent = await self.parse_reply(self.aid, self.reply_type, seek_id)

        for qn in QN:
            if await self.__get_video_result(detail, qn):
                break
        return self
