from functools import cached_property

from .feed import Feed


class Audio(Feed):
    infocontent: dict = {}
    mediacontent: str = ""
    audio_id: int = 0
    reply_type: int = 14

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/audio/au{self.audio_id}"
