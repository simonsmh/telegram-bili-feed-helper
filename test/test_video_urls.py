from unittest.mock import AsyncMock

import httpx
import pytest
from bilibili_api import Credential

from biliparser.provider.bilibili import _route
from biliparser.provider.bilibili.api import ParserException
from biliparser.provider.bilibili.video import Video


@pytest.mark.parametrize("short_link", [False, True])
@pytest.mark.parametrize("prefix", ["video", "en/video", "ja/video", "zh-CN/video", "zh-Hant/video", "pt-BR/video"])
@pytest.mark.parametrize("page", [1, 2])
@pytest.mark.parametrize("video_id,params", [("BV1KehG64E5s", {"bvid": "BV1KehG64E5s"}), ("av12345", {"aid": "12345"})])
async def test_video_url_reaches_metadata_request(monkeypatch, short_link, prefix, page, video_id, params):
    destination = f"https://www.bilibili.com/{prefix}/{video_id}/?p={page}&spm_id_from=333.1007#reply123"

    def redirect(request):
        assert request.method == "HEAD"
        if request.url.host == "b23.tv":
            return httpx.Response(302, headers={"location": destination})
        assert str(request.url).split("#")[0] == destination.split("#")[0]
        return httpx.Response(200)

    monkeypatch.setattr(
        "biliparser.provider.bilibili.video.RedisCache", lambda: AsyncMock(get=AsyncMock(return_value=None))
    )
    monkeypatch.setattr(
        "biliparser.provider.bilibili.video.credentialFactory.get", AsyncMock(return_value=Credential())
    )
    # Stop at the metadata response: no download or upload is needed to verify URL parsing.
    api = AsyncMock(return_value=httpx.Response(200, json={}, request=httpx.Request("GET", "https://api.bilibili.com")))
    monkeypatch.setattr("biliparser.provider.bilibili.video.bili_api_request", api)
    pages = []
    original_handle = Video.handle

    async def handle(self, **kwargs):
        try:
            return await original_handle(self, **kwargs)
        finally:
            pages.append(self.page)

    monkeypatch.setattr(Video, "handle", handle)
    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        result = await _route(client, "b23.tv/uoRg71d" if short_link else destination)
        assert isinstance(result, ParserException)
        assert "视频解析错误" in str(result)
        api.assert_awaited_once_with(client, "/x/web-interface/view", params=params)
    assert pages == [page]


@pytest.mark.parametrize(
    "path",
    [
        "/en/video/",
        "/en/video/BV1KehG64E5s_extra",
        "/en/video/BV1KehG64E5s/extra",
        "/unknown/video/BV1KehG64E5s",
        "/en/?next=/video/BV1KehG64E5s",
    ],
)
async def test_video_rejects_invalid_paths(monkeypatch, path):
    api = AsyncMock()
    monkeypatch.setattr("biliparser.provider.bilibili.video.bili_api_request", api)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ParserException, match="视频链接解析错误"):
            await Video(f"https://www.bilibili.com{path}", client).handle()
    api.assert_not_awaited()
