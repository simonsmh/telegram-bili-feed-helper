from functools import cached_property

from .feed import Feed


class Video(Feed):
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
