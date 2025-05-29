import datetime
import os
import re
from difflib import SequenceMatcher
from functools import cached_property
from urllib.parse import parse_qs, urlparse

import orjson
from bilibili_api import video
from telegram.constants import FileSizeLimit, MessageLimit

from ..cache import CACHES_TIMER, RedisCache
from ..utils import (
    BILI_API,
    LOCAL_MODE,
    ParserException,
    credentialFactory,
    escape_markdown,
    get_filename,
    logger,
)
from .feed import Feed


class Video(Feed):
    cidcontent: dict = {}
    epcontent: dict = {}
    infocontent: dict = {}
    page = 1
    quality = video.VideoQuality._8K
    reply_type: int = 1
    __dashurls: list[str] = []
    dashtype: str = ""

    @property
    def dashurls(self):
        return self.__dashurls

    @dashurls.setter
    def dashurls(self, content):
        self.__dashurls = content
        if hasattr(self, "dashfilename"):
            delattr(self, "dashfilename")

    @cached_property
    def dashfilename(self):
        return [get_filename(i) for i in self.dashurls] if self.dashurls else list()

    def extract_episode_info(self, target: str):
        if not self.epid or not self.epcontent or not self.epcontent.get("result"):
            return
        for episode in self.epcontent["result"].get("episodes"):
            if str(episode.get("id")) == str(self.epid):
                return episode.get(target)
        for subsection in self.epcontent["result"].get("section"):
            for episode in subsection.get("episodes"):
                if str(episode.get("id")) == str(self.epid):
                    return episode.get(target)

    @cached_property
    def cid(self):
        if self.infocontent and self.infocontent.get("data"):
            if self.page != 1 and self.infocontent["data"].get("pages"):
                for item in self.infocontent["data"]["pages"]:
                    if item.get("page") == self.page:
                        return item.get("cid")
            self.page = 1
            return self.infocontent["data"].get("cid")
        return self.extract_episode_info("cid")

    @cached_property
    def bvid(self):
        if self.infocontent and self.infocontent.get("data"):
            return self.infocontent["data"].get("bvid")
        return self.extract_episode_info("bvid")

    @cached_property
    def aid(self):
        if self.infocontent and self.infocontent.get("data"):
            return self.infocontent["data"].get("aid")
        return self.extract_episode_info("aid")

    @cached_property
    def epid(self):
        if (
            self.epcontent
            and self.epcontent.get("result")
            and self.epcontent["result"].get("episodes")
        ):
            return self.epcontent["result"]["episodes"][-1].get("id")

    @cached_property
    def ssid(self):
        if self.epcontent and self.epcontent.get("result"):
            return self.epcontent["result"].get("season_id")

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/video/av{self.aid}?p={self.page}"

    @property
    def cache_key(self):
        return {
            "bangumi:ep": f"bangumi:ep:{self.epid}",
            "bangumi:ss": f"bangumi:ss:{self.ssid}",
            "video:aid": f"video:aid:{self.aid}",
            "video:bvid": f"video:bvid:{self.bvid}",
        }

    @staticmethod
    def wan(num):
        return f"{num / 10000:.2f}万" if num >= 10000 else num

    def set_quality(self, in_str: str | None):
        if in_str is None:
            return
        in_str = in_str.strip().upper().replace("+", "PLUS")
        similarities = [
            (opt, SequenceMatcher(lambda x: x == "_", in_str, opt).ratio())
            for opt in [q.name for q in video.VideoQuality]
        ]
        best_match = max(similarities, key=lambda x: x[1])
        self.quality = video.VideoQuality[best_match[0]]

    def clear_cached_properties(self):
        for key in ["epid", "ssid", "aid", "bvid", "cid"]:
            if hasattr(self, key) and getattr(self, key) is None:
                delattr(self, key)

    async def __get_video_result(self, qn: video.VideoQuality):
        params = {"avid": self.aid, "cid": self.cid}
        if qn:
            params["qn"] = qn.value
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
            result, url = await self.test_url_status_code(url, self.url)
            if not result and video_result["data"]["durl"][0].get("backup_url", None):
                backup_urls = video_result["data"]["durl"][0]["backup_url"]
                for item in backup_urls:
                    url = item
                    result, item = await self.test_url_status_code(item, self.url)
                    if result:
                        break
            if result:
                self.mediacontent = video_result
                self.mediaduration = round(
                    video_result["data"]["durl"][0]["length"] / 1000
                )
                self.mediaurls = url
                self.mediatype = "video"
                self.mediaraws = False
                self.mediafilesize = video_result.get("data").get("durl")[0].get("size")
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
        ## TODO: rewrite self VideoDownloadURLDataDetecter with built-in test_url_status_code
        detecter = video.VideoDownloadURLDataDetecter(data=video_result.get("data"))
        streams = detecter.detect(
            video_min_quality=video.VideoQuality._360P,
            video_max_quality=self.quality,
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
            audio_size, audio_stream.url = await self.test_url_status_code(
                audio_stream.url, self.url
            )
            if audio_size:
                self.dashurls = [audio_stream.url]
                break
        if len(self.dashurls) < 1:
            logger.error(f"无可用Dash视频音频流清晰度: {streams}")
            return False
        for video_stream in video_streams:
            video_size, video_stream.url = await self.test_url_status_code(
                video_stream.url, self.url
            )
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
                    f"选择Dash视频清晰度:{video_stream.video_quality.name} 大小:{video_size}"
                )
                self.dashurls.insert(0, video_stream.url)
                self.dashtype = "dash"
                self.mediaraws = True
                self.mediafilesize = audio_size + video_size
                return True
        if len(self.dashurls) < 2:
            logger.error(f"无可用Dash视频流清晰度: {streams}")
            return False

    async def handle(self, extra: dict | None = None) -> "Video":
        if extra:
            self.set_quality(extra.get("quality"))
        logger.info(f"处理视频信息: 链接: {self.rawurl}")
        match = re.search(
            r"(?:bilibili\.com(?:/video|/bangumi/play)?|b23\.tv|acg\.tv)/(?:(?P<bvid>BV\w{10})|av(?P<aid>\d+)|ep(?P<epid>\d+)|ss(?P<ssid>\d+)|)/?\??(?:p=(?P<page>\d+))?",
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
            __bvid = match_fes.group("bvid")
            if __bvid:
                params = {"bvid": __bvid}
                self.bvid = __bvid
            else:
                raise ParserException("视频链接解析错误", self.rawurl)
        elif match:
            __bvid = match.group("bvid")
            __epid = match.group("epid")
            __aid = match.group("aid")
            __ssid = match.group("ssid")
            __page = match.group("page")
            if __page and __page.isdigit():
                self.page = max(1, int(__page))
            if __epid:
                params = {"ep_id": __epid}
                self.epid = __epid
            elif __bvid:
                params = {"bvid": __bvid}
                self.bvid = __bvid
            elif __aid:
                params = {"aid": __aid}
                self.aid = __aid
            elif __ssid:
                params = {"season_id": __ssid}
                self.ssid = __ssid
            else:
                raise ParserException("视频链接解析错误", self.rawurl)
            if self.epid is not None or self.ssid is not None:
                # 1.获取缓存
                try:
                    cache = (
                        await RedisCache().get(self.cache_key["bangumi:ep"])
                        if self.epid
                        else await RedisCache().get(self.cache_key["bangumi:ss"])
                    )
                except Exception as e:
                    logger.exception(f"拉取番剧缓存错误: {e}")
                    cache = None
                # 2.拉取番剧
                self.clear_cached_properties()
                if cache:
                    self.epcontent = orjson.loads(cache)  # type: ignore
                    logger.info(
                        f"拉取番剧缓存:epid {self.epid}"
                        if self.epid
                        else f"拉取番剧缓存:ssid {self.ssid}"
                    )
                else:
                    try:
                        r = await self.client.get(
                            BILI_API + "/pgc/view/web/season",
                            params=params,
                        )
                        self.epcontent = r.json()
                    except Exception as e:
                        raise ParserException(
                            f"番剧获取错误:{self.epid if self.epid else self.ssid}",
                            self.rawurl,
                            e,
                        )
                    # 3.番剧解析
                    if not self.epcontent or not self.epcontent.get("result"):
                        # Anime detects non-China IP
                        raise ParserException(
                            f"番剧解析错误:{self.epid if self.epid else self.ssid} {self.epcontent}",
                            self.rawurl,
                            self.epcontent,
                        )
                    self.clear_cached_properties()
                    if not self.epid or not self.ssid or not self.aid:
                        raise ParserException(
                            f"番剧解析错误:{self.epid} {self.ssid} {self.aid}",
                            self.rawurl,
                            self.epcontent,
                        )
                    # 4.缓存评论
                    try:
                        for key in [
                            self.cache_key["bangumi:ep"],
                            self.cache_key["bangumi:ss"],
                        ]:
                            await RedisCache().set(
                                key,
                                orjson.dumps(self.epcontent),
                                ex=CACHES_TIMER["BANGUMI"],
                                nx=True,
                            )
                    except Exception as e:
                        logger.exception(f"缓存番剧错误: {e}")
                params = {"aid": self.aid}
        else:
            raise ParserException("视频链接解析错误", self.rawurl)
        # 1.获取缓存
        try:
            cache = (
                await RedisCache().get(self.cache_key["video:aid"])
                if self.aid
                else await RedisCache().get(self.cache_key["video:bvid"])
            )
        except Exception as e:
            logger.exception(f"拉取视频缓存错误: {e}")
            cache = None
        # 2.拉取视频
        self.clear_cached_properties()
        if cache:
            self.infocontent = orjson.loads(cache)  # type: ignore
            logger.info(f"拉取视频缓存:{self.aid if self.aid else self.bvid}")
        else:
            try:
                r = await self.client.get(
                    BILI_API + "/x/web-interface/view",
                    params=params,
                )
                self.infocontent = r.json()
            except Exception as e:
                raise ParserException(
                    f"视频获取错误:{self.aid if self.aid else self.bvid}",
                    self.rawurl,
                    e,
                )
            # 3.视频解析
            if not self.infocontent and not self.infocontent.get("data"):
                # Video detects non-China IP
                raise ParserException(
                    f"视频解析错误{self.aid if self.aid else self.bvid}",
                    r.url,
                    self.infocontent,
                )
            if not self.aid or not self.bvid or not self.cid:
                raise ParserException(
                    f"视频解析错误:{self.aid} {self.bvid} {self.cid}",
                    self.rawurl,
                    self.epcontent,
                )
            # 4.缓存视频
            try:
                for key in [self.cache_key["video:aid"], self.cache_key["video:bvid"]]:
                    await RedisCache().set(
                        key,
                        orjson.dumps(self.infocontent),
                        ex=CACHES_TIMER["VIDEO"],
                        nx=True,
                    )
            except Exception as e:
                logger.exception(f"缓存视频错误: {e}")
        detail = self.infocontent["data"]
        self.user = detail.get("owner").get("name")
        self.uid = detail.get("owner").get("mid")
        content = "发布视频"
        if detail.get("tname"):
            content += f"-{detail.get('tname')}"
        if detail.get("tname_v2"):
            content += f"-{detail.get('tname_v2')}"
        content += "\n"
        if detail.get("pages") and len(detail["pages"]) > 1:
            content += f"第{self.page}P/共{len(detail['pages'])}P\n"
        if detail.get("stat"):
            content += f"播放量:{self.wan(detail.get('stat').get('view', 0))}\t\t弹幕:{self.wan(detail.get('stat').get('danmaku', 0))}\t\t评论:{self.wan(detail.get('stat').get('reply', 0))}\n"
            content += f"点赞:{self.wan(detail.get('stat').get('like', 0))}\t\t投币:{self.wan(detail.get('stat').get('coin', 0))}\t\t收藏:{self.wan(detail.get('stat').get('favorite', 0))}\n"
        if detail.get("pubdate"):
            content += f"发布日期:{datetime.datetime.fromtimestamp(detail.get('pubdate')).strftime('%Y-%m-%d %H:%M:%S')}\n"
        if detail.get("ctime") and detail.get("ctime") != detail.get("pubdate"):
            content += f"上传日期:{datetime.datetime.fromtimestamp(detail.get('ctime')).strftime('%Y-%m-%d %H:%M:%S')}\n"
        if detail.get("duration"):
            content += f"时长:{datetime.timedelta(seconds=detail.get('duration', 0))}\n"
        self.content = content
        self.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({self.url})"
        extra_desc = f"\n**{
            escape_markdown(detail.get('desc') or detail.get('dynamic')).replace(
                '\n', '\n>'
            )
        }||"
        if (
            extra_desc
            and len(self.extra_markdown + extra_desc) < MessageLimit.CAPTION_LENGTH
        ):
            self.extra_markdown += extra_desc
        self.mediatitle = detail.get("title")
        self.mediaurls = detail.get("pic")
        self.mediathumb = detail.get("pic")
        self.mediadimention = detail.get("pages")[self.page - 1].get("dimension")
        self.mediatype = "image"
        self.replycontent = await self.parse_reply(self.aid, self.reply_type, seek_id)
        try:
            for mp4_qn in [
                video.VideoQuality._720P,
                video.VideoQuality._480P,
                video.VideoQuality._360P,
            ]:
                if await self.__get_video_result(mp4_qn):
                    break
            await self.__get_dash_video()
        except Exception as e:
            logger.exception(f"视频下载解析错误: {e}")
        return self
