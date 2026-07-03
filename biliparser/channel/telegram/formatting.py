import re

from ...model import Comment, MediaConstraints, ParsedContent
from ...utils import escape_markdown


def _clean_cn_tag_style(content: str) -> str:
    """Refine cn tag style display: #abc# -> #abc"""
    if not content:
        return ""
    return re.sub(r"\\#((?:(?!\\#).)+)\\#", r"\\#\1 ", content)


def _make_user_markdown(name: str, uid: str) -> str:
    if name and uid:
        return f"[@{escape_markdown(name)}](https://space.bilibili.com/{uid})"
    return ""


def _format_comment_markdown(comments: list[Comment]) -> str:
    result = ""
    for c in comments:
        user_md = _make_user_markdown(c.author.name, c.author.uid)
        if c.is_target:
            result += f"💬\\> {user_md}:\n{escape_markdown(c.text)}\n"
        elif c.is_top:
            result += f"🔝\\> {user_md}:\n{escape_markdown(c.text)}\n"
    return result


def _try_append_within_limit(components: list[str], text: str, max_len: int) -> bool:
    if not text:
        return True
    test_content = "".join([*components, text])
    if len(test_content) < max_len:
        components.append(text)
        return True
    return False


def format_caption_for_telegram(content: ParsedContent, constraints: MediaConstraints) -> str:
    """Format ParsedContent into a Telegram MarkdownV2 caption string."""
    max_len = constraints.caption_max_length

    components = [f"{content.extra_markdown or escape_markdown(content.url)}\n"]

    if content.author.name:
        user_md = _make_user_markdown(content.author.name, content.author.uid)
        if not _try_append_within_limit(components, f"{user_md}:", max_len):
            return "".join(components)

    content_md = content.content_markdown or escape_markdown(content.content)
    if content_md and not content_md.endswith("\n"):
        content_md += "\n"

    comment_md = _format_comment_markdown(content.comments)

    for text in [content_md, comment_md]:
        if text:
            formatted = f"\n**>{_clean_cn_tag_style(text).replace(chr(10), chr(10) + '>')}||"
            if not _try_append_within_limit(components, formatted, max_len):
                return "".join(components)

    return "".join(components)
