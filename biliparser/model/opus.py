from functools import cached_property, lru_cache

from ..utils import escape_markdown
from .feed import Feed


class Opus(Feed):
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
