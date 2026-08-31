"""Telegram inline query helpers: cache lookup, Cached*/URL results, answer-with-fallback."""

import asyncio
from uuid import uuid4

from telegram import (
    InlineQueryResultAudio,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
)
from telegram.error import BadRequest

from ...model import ParsedContent
from ...provider.bilibili.api import referer_url
from ...uploader.download import dash_merged_filename
from ...utils import logger
from .uploader import get_cached_media_file_id

INLINE_QUERY_TIMEOUT_MESSAGE = "Query is too old and response timeout expired or query id is invalid"


def video_cache_candidate_names(filenames: list[str], merge_streams: bool) -> list[str]:
    """Inline 视频缓存查找名，按优先级：原始文件名，DASH 合并名。"""
    if not filenames:
        return []
    names = [filenames[0]]
    if merge_streams:
        merged = dash_merged_filename(filenames[0])
        if merged != names[0]:
            names.append(merged)
    return names


def pad_cache_ids(urls: list[str], cache_ids: list[str | None]) -> list[str | None]:
    """保证每个 media url 都有一个 cache id 槽位（缺失时为 None）。"""
    padded: list[str | None] = list(cache_ids)
    if len(padded) < len(urls):
        padded.extend([None] * (len(urls) - len(padded)))
    return padded[: len(urls)]


async def resolve_cached_file_id(candidate_names: list[str]) -> str | None:
    """按候选名顺序查找，返回第一个命中的 file_id。"""
    for name in candidate_names:
        file_id = await get_cached_media_file_id(name)
        if file_id:
            return file_id
    return None


async def answer_inline_query(inline_query, results, fallback=None) -> None:
    """回答 inline query。缓存 file_id 不可用时用 URL 结果重试一次；超时不重试。"""
    try:
        await inline_query.answer(results, cache_time=0, is_personal=True)
        return
    except BadRequest as err:
        if INLINE_QUERY_TIMEOUT_MESSAGE in err.message:
            logger.error(f"{err} -> Inline请求超时")
            return
        if fallback is None:
            logger.exception(err)
            raise err
        logger.warning(f"Inline 缓存 file_id 无效，改用 URL 结果: {err}")
    except Exception as err:
        logger.exception(err)
        raise err

    try:
        await inline_query.answer(fallback, cache_time=0, is_personal=True)
    except BadRequest as err:
        if INLINE_QUERY_TIMEOUT_MESSAGE in err.message:
            logger.error(f"{err} -> Inline请求超时")
            return
        logger.exception(err)
        raise err
    except Exception as err:
        logger.exception(err)
        raise err


def _url_video_result(f: ParsedContent, caption: str) -> InlineQueryResultVideo:
    inline_video_url = f.media.fallback_url or f.media.urls[0]
    return InlineQueryResultVideo(
        id=uuid4().hex,
        caption=caption,
        title=f.media.title,
        description=f"{f.author.name}: {f.content}",
        mime_type="video/mp4",
        thumbnail_url=f.media.thumbnail,
        video_url=referer_url(inline_video_url, f.url),
        video_duration=f.media.duration,
        video_width=f.media.dimension.get("width", 0),
        video_height=f.media.dimension.get("height", 0),
    )


def _cached_video_result(cache_file_id: str, f: ParsedContent, caption: str) -> InlineQueryResultCachedVideo:
    return InlineQueryResultCachedVideo(
        id=uuid4().hex,
        video_file_id=cache_file_id,
        caption=caption,
        title=f.media.title,
        description=f"{f.author.name}: {f.content}",
    )


def _url_audio_result(f: ParsedContent, caption: str) -> InlineQueryResultAudio:
    return InlineQueryResultAudio(
        id=uuid4().hex,
        caption=caption,
        title=f.media.title,
        audio_duration=f.media.duration,
        audio_url=referer_url(f.media.urls[0], f.url),
        performer=f.author.name,
    )


def _cached_audio_result(cache_file_id: str, f: ParsedContent, caption: str) -> InlineQueryResultCachedAudio:
    return InlineQueryResultCachedAudio(
        id=uuid4().hex,
        audio_file_id=cache_file_id,
        caption=caption,
    )


def _url_image_result(mediaurl: str, f: ParsedContent, caption: str):
    if ".gif" in mediaurl:
        return InlineQueryResultGif(
            id=uuid4().hex,
            caption=caption,
            title=f"{f.author.name}: {f.content}",
            gif_url=mediaurl,
            thumbnail_url=mediaurl,
        )
    return InlineQueryResultPhoto(
        id=uuid4().hex,
        caption=caption,
        title=f.author.name,
        description=f.content,
        photo_url=mediaurl + "@1280w.jpg",
        thumbnail_url=mediaurl + "@512w_512h.jpg",
    )


def _cached_image_result(mediaurl: str, cache_file_id: str, f: ParsedContent, caption: str):
    if ".gif" in mediaurl:
        return InlineQueryResultCachedGif(
            id=uuid4().hex,
            gif_file_id=cache_file_id,
            caption=caption,
            title=f"{f.author.name}: {f.content}",
        )
    return InlineQueryResultCachedPhoto(
        id=uuid4().hex,
        photo_file_id=cache_file_id,
        caption=caption,
        title=f.author.name,
        description=f.content,
    )


async def build_media_inline_results(f: ParsedContent, caption: str) -> tuple[list, list]:
    """构建 (优先缓存的结果, 纯 URL 回退结果)。"""
    media = f.media
    if media is None:
        return [], []
    if media.type == "video":
        cache_file_id = await resolve_cached_file_id(video_cache_candidate_names(media.filenames, media.merge_streams))
        fallback = [_url_video_result(f, caption)]
        if cache_file_id:
            return [_cached_video_result(cache_file_id, f, caption)], fallback
        return fallback, fallback

    if media.type == "audio":
        cache_file_id = await resolve_cached_file_id(media.filenames[:1])
        fallback = [_url_audio_result(f, caption)]
        if cache_file_id:
            return [_cached_audio_result(cache_file_id, f, caption)], fallback
        return fallback, fallback

    cache_ids: list[str | None] = []
    if media.filenames:
        cache_ids = list(await asyncio.gather(*[get_cached_media_file_id(fn) for fn in media.filenames]))
    cache_ids = pad_cache_ids(media.urls, cache_ids)
    fallback = [_url_image_result(url, f, caption) for url in media.urls]
    primary = [
        _cached_image_result(url, cache_id, f, caption) if cache_id else _url_image_result(url, f, caption)
        for url, cache_id in zip(media.urls, cache_ids, strict=True)
    ]
    return primary, fallback
