from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Author:
    name: str = ""
    uid: str = ""


@dataclass
class Comment:
    author: Author
    text: str
    is_top: bool = False
    is_target: bool = False


@dataclass
class MediaInfo:
    urls: list[str]
    type: str  # "video" | "audio" | "image"
    thumbnail: str = ""
    duration: int = 0
    dimension: dict = field(default_factory=lambda: {"width": 0, "height": 0, "rotate": 0})
    title: str = ""
    filenames: list[str] = field(default_factory=list)
    thumbnail_filename: str = ""
    need_download: bool = False


@dataclass
class MediaConstraints:
    """Channel 声明自己的媒体能力，传给 Provider"""

    max_upload_size: int  # bytes
    max_download_size: int  # bytes
    caption_max_length: int
    local_mode: bool = False


@dataclass
class ParsedContent:
    """Provider 产出，Channel 消费"""

    url: str
    author: Author
    title: str = ""
    content: str = ""
    content_markdown: str = ""
    extra_markdown: str = ""
    media: "MediaInfo | None" = None
    comments: list[Comment] = field(default_factory=list)
    source_url: str = ""
    cache_keys: dict = field(default_factory=dict)


@dataclass
class PreparedMedia:
    """Provider 准备好的媒体文件"""

    files: list[Path | str]
    thumbnail: "Path | str | None"
    cleanup_paths: list[Path] = field(default_factory=list)
