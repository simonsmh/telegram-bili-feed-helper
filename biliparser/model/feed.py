import re
from functools import cached_property, lru_cache

from ..utils import escape_markdown


class Feed:
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
