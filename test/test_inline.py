"""测试 Telegram inline 缓存候选名、URL 回退与空 filenames。"""

from unittest.mock import AsyncMock

import pytest
from telegram import (
    InlineQueryResultCachedGif,
    InlineQueryResultCachedVideo,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
)
from telegram.error import BadRequest

from biliparser.channel.telegram.inline import (
    INLINE_QUERY_TIMEOUT_MESSAGE,
    answer_inline_query,
    build_media_inline_results,
    pad_cache_ids,
    video_cache_candidate_names,
)
from biliparser.model import Author, MediaInfo, ParsedContent


def test_video_cache_candidate_names_with_merge_streams():
    assert video_cache_candidate_names(["CID-1-30080.m4s"], True) == [
        "CID-1-30080.m4s",
        "CID-1-30080_merged.mp4",
    ]


def test_video_cache_candidate_names_without_merge_streams():
    assert video_cache_candidate_names(["video.mp4"], False) == ["video.mp4"]


def test_video_cache_candidate_names_empty():
    assert video_cache_candidate_names([], True) == []


def test_pad_cache_ids_empty_filenames():
    urls = ["https://i.example.com/1.jpg", "https://i.example.com/2.jpg"]
    assert pad_cache_ids(urls, []) == [None, None]


@pytest.mark.asyncio
async def test_answer_inline_retries_fallback_on_bad_cached_file_id():
    inline_query = AsyncMock()
    inline_query.answer = AsyncMock(side_effect=[BadRequest("Wrong file identifier/HTTP URL specified"), None])
    primary = [object()]
    fallback = [object()]

    await answer_inline_query(inline_query, primary, fallback=fallback)

    assert inline_query.answer.await_count == 2
    assert inline_query.answer.await_args_list[0].args[0] is primary
    assert inline_query.answer.await_args_list[1].args[0] is fallback


@pytest.mark.asyncio
async def test_answer_inline_timeout_does_not_retry():
    inline_query = AsyncMock()
    inline_query.answer = AsyncMock(side_effect=BadRequest(INLINE_QUERY_TIMEOUT_MESSAGE))
    fallback = [object()]

    await answer_inline_query(inline_query, [object()], fallback=fallback)

    assert inline_query.answer.await_count == 1


@pytest.mark.asyncio
async def test_answer_inline_fallback_failure_reraises():
    inline_query = AsyncMock()
    inline_query.answer = AsyncMock(
        side_effect=[
            BadRequest("Wrong file identifier/HTTP URL specified"),
            BadRequest("another bad request"),
        ]
    )

    with pytest.raises(BadRequest, match="another bad request"):
        await answer_inline_query(inline_query, [object()], fallback=[object()])


@pytest.mark.asyncio
async def test_empty_filenames_still_produce_url_photo_results():
    content = ParsedContent(
        url="https://example.com/opus",
        author=Author(name="author"),
        content="hi",
        media=MediaInfo(
            urls=["https://i.example.com/1.jpg", "https://i.example.com/2.jpg"],
            type="image",
            filenames=[],
        ),
    )

    primary, fallback = await build_media_inline_results(content, "caption")

    assert len(primary) == 2
    assert len(fallback) == 2
    assert all(isinstance(item, InlineQueryResultPhoto) for item in primary)
    assert all(isinstance(item, InlineQueryResultPhoto) for item in fallback)


@pytest.mark.asyncio
async def test_gif_cache_hit_prefers_cached_gif_with_url_fallback(monkeypatch):
    monkeypatch.setattr(
        "biliparser.channel.telegram.inline.get_cached_media_file_id",
        AsyncMock(return_value="gif-file-id"),
    )
    content = ParsedContent(
        url="https://example.com/opus",
        author=Author(name="author"),
        content="hi",
        media=MediaInfo(
            urls=["https://i.example.com/a.gif"],
            type="image",
            filenames=["a.gif"],
        ),
    )

    primary, fallback = await build_media_inline_results(content, "caption")

    assert len(primary) == 1
    assert isinstance(primary[0], InlineQueryResultCachedGif)
    assert isinstance(fallback[0], InlineQueryResultGif)


@pytest.mark.asyncio
async def test_video_inline_looks_up_merged_cache_name(monkeypatch):
    looked_up: list[str] = []

    async def fake_get(name: str):
        looked_up.append(name)
        if name.endswith("_merged.mp4"):
            return "file-id-merged"
        return None

    monkeypatch.setattr("biliparser.channel.telegram.inline.get_cached_media_file_id", fake_get)
    content = ParsedContent(
        url="https://www.bilibili.com/video/BVxxx",
        author=Author(name="author"),
        content="hi",
        media=MediaInfo(
            urls=["https://cdn.invalid/v.m4s", "https://cdn.invalid/a.m4s"],
            type="video",
            filenames=["CID-1-30080.m4s", "audio.m4s"],
            merge_streams=True,
            title="title",
            fallback_url="https://cdn.invalid/v.mp4",
            thumbnail="https://cdn.invalid/t.jpg",
        ),
    )

    primary, fallback = await build_media_inline_results(content, "caption")

    assert looked_up == ["CID-1-30080.m4s", "CID-1-30080_merged.mp4"]
    assert isinstance(primary[0], InlineQueryResultCachedVideo)
    assert isinstance(fallback[0], InlineQueryResultVideo)
