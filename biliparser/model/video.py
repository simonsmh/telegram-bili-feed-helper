from functools import cached_property

from .feed import Feed


class Video(Feed):
    cidcontent: dict = {}
    epcontent: dict = {}
    infocontent: dict = {}
    mediacontent: dict = {}
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
