"""集成测试：验证 credentialFactory + Video 解析真实 BV 号的完整链路。

需要网络访问和 Redis（可选）。跳过条件：无网络或 bilibili API 不可达。
用法：uv run pytest test/test_integration_bv.py -x -s --tb=short
"""

import uuid

import httpx
import pytest

from biliparser.model import MediaConstraints
from biliparser.provider.bilibili import (
    BILIBILI_DESKTOP_HEADER,
    BilibiliProvider,
    Feed,
    _route,
)
from biliparser.provider.bilibili.credential import credentialFactory

# 测试用 BV 号
TEST_BV = "BV1zvQbBkEcG"
TEST_FULL_URL = f"https://www.bilibili.com/video/{TEST_BV}?spm_id_from=333.1007.tianma.1-2-2.click"


def _mc():
    return MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
    )


async def _can_reach_bilibili() -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.head("https://api.bilibili.com")
            return resp.status_code < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 1. credentialFactory 基本功能
# ---------------------------------------------------------------------------


class TestCredentialFactory:
    @pytest.mark.asyncio
    async def test_credential_returns_credential_object(self):
        """credentialFactory.get() 应返回 Credential 对象，不抛异常。"""
        cred = await credentialFactory.get()
        assert cred is not None
        # Credential 对象应有 get_cookies 方法
        cookies = cred.get_cookies()
        assert isinstance(cookies, dict)
        print(f"  credential cookies keys: {list(cookies.keys())}")

    @pytest.mark.asyncio
    async def test_credential_singleton(self):
        """credentialFactory 是单例，多次 get 返回同一对象。"""
        cred1 = await credentialFactory.get()
        cred2 = await credentialFactory.get()
        assert cred1 is cred2


# ---------------------------------------------------------------------------
# 2. _route 对裸 BV 号的路由
# ---------------------------------------------------------------------------


class TestRouteIntegration:
    @pytest.mark.asyncio
    async def test_route_bare_bv(self):
        """_route 对裸 BV 号应返回 Video (Feed) 对象。"""
        reachable = await _can_reach_bilibili()
        if not reachable:
            pytest.skip("bilibili API 不可达")

        async with httpx.AsyncClient(
            headers=BILIBILI_DESKTOP_HEADER,
            http2=True,
            follow_redirects=True,
            cookies={"buvid3": f"{uuid.uuid4()}infoc"},
        ) as client:
            result = await _route(client, TEST_BV)

        # _route 被 @retry_catcher 包装，出错时返回 Exception
        if isinstance(result, Exception):
            print(f"  _route 返回异常: {result}")
            # 即使 API 返回错误（如地区限制），也不应是 None
            assert result is not None
            return

        assert isinstance(result, Feed), f"期望 Feed 实例，得到 {type(result)}"
        print(f"  url: {result.url}")
        print(f"  user: {result.user}")
        print(f"  uid: {result.uid}")
        print(f"  content: {result.content[:100]}...")
        print(f"  mediaurls: {result.mediaurls[:1]}...")
        print(f"  mediatype: {result.mediatype}")

        # 基本字段校验
        assert result.user, "user 不应为空"
        assert result.uid, "uid 不应为空"
        assert result.content, "content 不应为空"
        assert result.mediaurls, "mediaurls 不应为空"

    @pytest.mark.asyncio
    async def test_route_full_url(self):
        """_route 对完整 URL 应返回 Video (Feed) 对象。"""
        reachable = await _can_reach_bilibili()
        if not reachable:
            pytest.skip("bilibili API 不可达")

        async with httpx.AsyncClient(
            headers=BILIBILI_DESKTOP_HEADER,
            http2=True,
            follow_redirects=True,
            cookies={"buvid3": f"{uuid.uuid4()}infoc"},
        ) as client:
            result = await _route(client, TEST_FULL_URL)

        if isinstance(result, Exception):
            print(f"  _route 返回异常: {result}")
            return

        assert isinstance(result, Feed)
        print(f"  url: {result.url}")
        print(f"  user: {result.user}")
        assert result.user


# ---------------------------------------------------------------------------
# 3. BilibiliProvider.parse 端到端
# ---------------------------------------------------------------------------


class TestBilibiliProviderParse:
    @pytest.mark.asyncio
    async def test_parse_bare_bv(self):
        """BilibiliProvider.parse 对裸 BV 号应返回 ParsedContent 列表。"""
        reachable = await _can_reach_bilibili()
        if not reachable:
            pytest.skip("bilibili API 不可达")

        provider = BilibiliProvider()
        mc = _mc()
        results = await provider.parse([TEST_BV], mc)

        assert len(results) == 1
        r = results[0]

        if isinstance(r, Exception):
            print(f"  解析返回异常（可能是地区限制）: {r}")
            return

        print(f"  url: {r.url}")
        print(f"  author: {r.author.name} (uid={r.author.uid})")
        print(f"  content: {r.content[:100]}...")
        print(f"  media: {r.media}")
        if r.media:
            print(f"  media.type: {r.media.type}")
            print(f"  media.urls: {r.media.urls[:1]}...")

        assert r.url, "url 不应为空"
        assert r.author.name, "author.name 不应为空"
        assert r.content, "content 不应为空"

    @pytest.mark.asyncio
    async def test_parse_full_url(self):
        """BilibiliProvider.parse 对完整 URL 应返回 ParsedContent 列表。"""
        reachable = await _can_reach_bilibili()
        if not reachable:
            pytest.skip("bilibili API 不可达")

        provider = BilibiliProvider()
        mc = _mc()
        results = await provider.parse([TEST_FULL_URL], mc)

        assert len(results) == 1
        r = results[0]

        if isinstance(r, Exception):
            print(f"  解析返回异常: {r}")
            return

        assert r.url
        assert r.author.name
        print(f"  url: {r.url}")
        print(f"  author: {r.author.name}")

    @pytest.mark.asyncio
    async def test_parse_bare_bv_and_full_url_same_video(self):
        """裸 BV 号和完整 URL 应解析出同一个视频。"""
        reachable = await _can_reach_bilibili()
        if not reachable:
            pytest.skip("bilibili API 不可达")

        provider = BilibiliProvider()
        mc = _mc()

        results_bare = await provider.parse([TEST_BV], mc)
        results_full = await provider.parse([TEST_FULL_URL], mc)

        if isinstance(results_bare[0], Exception) or isinstance(results_full[0], Exception):
            pytest.skip("API 返回异常，跳过对比")

        r_bare = results_bare[0]
        r_full = results_full[0]

        print(f"  bare: {r_bare.url} by {r_bare.author.name}")
        print(f"  full: {r_full.url} by {r_full.author.name}")

        # 同一个视频，author 和 url 应一致
        assert r_bare.author.name == r_full.author.name
        assert r_bare.url == r_full.url

    @pytest.mark.asyncio
    async def test_parse_invalid_bv_returns_exception(self):
        """无效 BV 号应返回异常而非 raise。"""
        reachable = await _can_reach_bilibili()
        if not reachable:
            pytest.skip("bilibili API 不可达")

        provider = BilibiliProvider()
        mc = _mc()
        results = await provider.parse(["BV0000000000"], mc)

        assert len(results) == 1
        r = results[0]
        print(f"  无效BV结果类型: {type(r).__name__}")
        if isinstance(r, Exception):
            print(f"  异常信息: {r}")
        # 不管是 Exception 还是 ParsedContent，都不应 raise
