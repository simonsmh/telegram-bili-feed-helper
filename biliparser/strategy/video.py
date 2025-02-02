import os
import re
from functools import cached_property
from urllib.parse import parse_qs, urlparse

import orjson
from bilibili_api import video
from telegram.constants import FileSizeLimit

from ..cache import CACHES_TIMER, RedisCache
from ..utils import (
    BILI_API,
    LOCAL_MODE,
    ParserException,
    credentialFactory,
    escape_markdown,
    get_filename,
    headers,
    logger,
)
from .feed import Feed

QN = [64, 32, 16]


class Video(Feed):
    cidcontent: dict = {}
    epcontent: dict = {}
    infocontent: dict = {}
    page = 1
    reply_type: int = 1
    dashurls: list[str] = []
    dashtype: str = ""

    @cached_property
    def dashfilename(self):
        return [get_filename(i) for i in self.dashurls] if self.dashurls else list()

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
            for subsection in self.epcontent["result"].get("section"):
                for episode in subsection.get("episodes"):
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
                return 0
            return int(response.headers.get("Content-Length", 0))

    async def __get_video_result(self, detail, qn: int):
        params = {"avid": self.aid, "cid": self.cid}
        if qn:
            params["qn"] = qn
        r = await self.client.get(
            BILI_API + "/x/player/playurl",
            params=params,
            cookies=(await credentialFactory.get()).get_cookies(),
        )
        video_result = r.json()
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
                backup_urls = video_result["data"]["durl"][0]["backup_url"]
                for item in backup_urls:
                    url = item
                    result = await self.__test_url_status_code(item, self.url)
                    if result:
                        break
            if result:
                self.mediacontent = video_result
                self.mediathumb = detail.get("pic")
                self.mediaduration = round(
                    video_result["data"]["durl"][0]["length"] / 1000
                )
                self.mediadimention = detail.get("pages")[0].get("dimension")
                self.mediaurls = url
                self.mediatype = "video"
                self.mediaraws = False
                return True

    async def __get_dash_video(self):
        params = {
            "avid": self.aid,
            "cid": self.cid,
            "qn": 125,
            "fnver": 0,
            "fnval": 4048,
            "fourk": 1,
            "voice_balance": 1,
        }
        r = await self.client.get(
            BILI_API + "/x/player/playurl",
            params=params,
            cookies=(await credentialFactory.get()).get_cookies(),
        )
        video_result = r.json()
        if not video_result.get("code") == 0 or not video_result.get("data"):
            logger.error(f"获取Dash视频流错误: {video_result}")
            return False
        ## TODO: rewrite self VideoDownloadURLDataDetecter with built-in __test_url_status_code
        detecter = video.VideoDownloadURLDataDetecter(data=video_result.get("data"))
        streams = detecter.detect(
            video_min_quality=video.VideoQuality._360P,
            codecs=[video.VideoCodecs(os.environ.get("VIDEO_CODEC", "avc"))],
        )  # 可以设置成hev/av01减少文件体积，但是tg不二压会造成部分老设备直接解码指定codec时不展示，需要指定成avc
        video_streams = [
            video_stream
            for video_stream in streams
            if type(video_stream) is video.VideoStreamDownloadURL
        ]
        audio_streams = [
            audio_stream
            for audio_stream in streams
            if type(audio_stream) is video.AudioStreamDownloadURL
        ]
        if not video_streams or not audio_streams:
            logger.error(f"获取Dash视频流错误: {streams}")
            return False
        self.dashtype = ""
        self.dashurls = []
        video_streams.sort(key=lambda x: x.video_quality.value, reverse=True)
        audio_streams.sort(key=lambda x: x.audio_quality.value, reverse=True)
        audio_size = 0
        for audio_stream in audio_streams:
            audio_size = await self.__test_url_status_code(audio_stream.url, self.url)
            if audio_size:
                self.dashurls = [audio_stream.url]
                break
        if len(self.dashurls) < 1:
            logger.error(f"无可用Dash视频音频流清晰度: {streams}")
            return False
        for video_stream in video_streams:
            video_size = await self.__test_url_status_code(video_stream.url, self.url)
            if (
                audio_size
                and video_size
                and (
                    audio_size + video_size
                    < (
                        int(
                            os.environ.get(
                                "VIDEO_SIZE_LIMIT",
                                FileSizeLimit.FILESIZE_UPLOAD_LOCAL_MODE,
                            )
                        )
                        if LOCAL_MODE
                        else FileSizeLimit.FILESIZE_UPLOAD
                    )
                )
            ):
                logger.info(
                    f"选择Dash视频清晰度: {video_stream.video_quality.name} 大小：{video_size}"
                )
                self.dashurls.insert(0, video_stream.url)
                self.dashtype = "dash"
                self.mediaraws = True
                return True
        if len(self.dashurls) < 2:
            logger.error(f"无可用Dash视频流清晰度: {streams}")
            return False

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
                    await RedisCache().get(f"bangumi:ep:{epid}")
                    if epid
                    else await RedisCache().get(f"bangumi:ss:{ssid}")
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
                        await RedisCache().set(
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
                await RedisCache().get(f"video:aid:{aid}")
                if aid
                else await RedisCache().get(f"video:bvid:{bvid}")
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
                    await RedisCache().set(
                        key,
                        orjson.dumps(self.infocontent),
                        ex=CACHES_TIMER.get("video"),
                        nx=True,
                    )
            except Exception as e:
                logger.exception(f"缓存视频错误: {e}")
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
        try:
            for qn in QN:
                if await self.__get_video_result(detail, qn):
                    break
            await self.__get_dash_video()
        except Exception as e:
            logger.exception(f"视频下载解析错误: {e}")
        return self
