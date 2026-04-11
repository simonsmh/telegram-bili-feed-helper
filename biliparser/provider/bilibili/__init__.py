# BilibiliProvider will be fully implemented in Task 5
# For now, just export the api and credential modules
from .api import (
    BILIBILI_DESKTOP_HEADER,
    BILIBILI_DESKTOP_BUILD,
    CACHES_TIMER,
    ParserException,
    retry_catcher,
    referer_url,
    bili_api_request,
)
from .credential import CredentialFactory, credentialFactory
from .feed import Feed
from .audio import Audio
from .live import Live
from .opus import Opus
from .read import Read
from .video import Video

__all__ = [
    "BILIBILI_DESKTOP_HEADER",
    "BILIBILI_DESKTOP_BUILD",
    "CACHES_TIMER",
    "ParserException",
    "retry_catcher",
    "referer_url",
    "bili_api_request",
    "CredentialFactory",
    "credentialFactory",
    "Feed",
    "Audio",
    "Live",
    "Opus",
    "Read",
    "Video",
]
