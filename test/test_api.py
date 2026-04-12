"""测试 provider/bilibili/api.py — ParserException、referer_url、CACHES_TIMER"""
import pytest

from biliparser.provider.bilibili.api import (
    BILIBILI_DESKTOP_HEADER,
    BILIBILI_DESKTOP_BUILD,
    CACHES_TIMER,
    CACHE_TIMER_DEFAULTS,
    ParserException,
    referer_url,
    retry_catcher,
)


def test_bilibili_desktop_header():
    assert "User-Agent" in BILIBILI_DESKTOP_HEADER
    assert "bilibili_pc" in BILIBILI_DESKTOP_HEADER["User-Agent"]


def test_bilibili_desktop_build():
    assert BILIBILI_DESKTOP_BUILD == "11605"


def test_caches_timer_keys():
    expected_keys = {"CREDENTIAL", "LOCK", "AUDIO", "BANGUMI", "OPUS", "LIVE", "READ", "REPLY", "VIDEO"}
    assert set(CACHES_TIMER.keys()) == expected_keys


def test_caches_timer_values_are_ints():
    for k, v in CACHES_TIMER.items():
        assert isinstance(v, int), f"{k} should be int, got {type(v)}"


def test_caches_timer_defaults():
    assert CACHE_TIMER_DEFAULTS["LIVE"] == 300  # 5 minutes
    assert CACHE_TIMER_DEFAULTS["VIDEO"] == 3600  # 1 hour


def test_parser_exception_str():
    e = ParserException("测试错误", "https://example.com", "response body")
    s = str(e)
    assert "测试错误" in s
    assert "https://example.com" in s
    assert "response body" in s


def test_parser_exception_no_res():
    e = ParserException("错误", "https://example.com")
    s = str(e)
    assert "错误" in s
    assert e.res is None


def test_parser_exception_is_exception():
    e = ParserException("msg", "url")
    assert isinstance(e, Exception)


def test_referer_url_with_referer():
    result = referer_url("https://cdn.bilibili.com/video.mp4", "https://www.bilibili.com/video/BV123")
    assert "referer.simonsmh.workers.dev" in result
    assert "video.mp4" in result
    assert "referer=" in result


def test_referer_url_no_referer():
    url = "https://cdn.bilibili.com/video.mp4"
    assert referer_url(url, "") == url


@pytest.mark.asyncio
async def test_retry_catcher_catches_parser_exception():
    @retry_catcher
    async def failing_func():
        raise ParserException("test", "url")

    result = await failing_func()
    assert isinstance(result, ParserException)


@pytest.mark.asyncio
async def test_retry_catcher_catches_base_exception():
    @retry_catcher
    async def failing_func():
        raise ValueError("test error")

    result = await failing_func()
    assert isinstance(result, ValueError)


@pytest.mark.asyncio
async def test_retry_catcher_passes_through():
    @retry_catcher
    async def ok_func():
        return "success"

    result = await ok_func()
    assert result == "success"
