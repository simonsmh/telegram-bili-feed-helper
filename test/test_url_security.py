import httpx
import pytest

from biliparser.model import MediaConstraints
from biliparser.provider.bilibili import BilibiliProvider, _route
from biliparser.provider.bilibili.api import ParserException
from biliparser.provider.bilibili.urls import IMAGE_HOSTS, LINK_HOSTS, checked_request
from biliparser.uploader.download import get_media


@pytest.mark.parametrize(
    "url",
    [
        "https://qqbilibili.com/read/cv123",
        "https://bilibili.com.evil.example/read/cv123",
        "https://bilibili.com@evil.example/x",
        "https://evil.example/?url=bilibili.com",
        "https://evil.example/BV1bW411n7fY",
        "https://www.bilibili.com:8080/read/cv123",
        "https://www.bilibili.com\\@evil.example/x",
        "https://www.bilibili.com\n.evil.example/x",
        "file://bilibili.com/read/cv123",
    ],
)
async def test_untrusted_input_never_reaches_network(url):
    provider = BilibiliProvider()
    assert not provider.can_handle(url)
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r))) as client:
        assert isinstance(await _route(client, url), ParserException)
    assert requests == []
    # parse is public and must enforce the boundary even without can_handle.
    result = await provider.parse([url], MediaConstraints(1, 1, 1))
    assert isinstance(result[0], ParserException)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/read/cv123",
        "http://t.bilibili.com/123",
        "b23.tv/abc",
        "https://acg.tv/abc",
        "https://bili2233.cn/abc",
        "https://WWW.BILIBILI.COM/video/BV1bW411n7fY",
        "BV1bW411n7fY",
        "av123",
    ],
)
def test_legitimate_links(url):
    assert BilibiliProvider().can_handle(url)


@pytest.mark.parametrize("hosts,start", [(LINK_HOSTS, "https://b23.tv/x"), (IMAGE_HOSTS, "https://i0.hdslb.com/x")])
@pytest.mark.parametrize(
    "destination", ["http://127.0.0.1/secret", "http://169.254.169.254/secret", "https://bilibili.com@evil.example/x"]
)
async def test_redirect_checked_before_request(hosts, start, destination):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": destination})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(ParserException):
            await checked_request(client, "GET", start, hosts)
    assert requests == [start]


async def test_legitimate_relative_redirect():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return (
            httpx.Response(302, headers={"location": "/read/cv123"})
            if len(requests) == 1
            else httpx.Response(200, text="ok")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        response = await checked_request(client, "GET", "https://www.bilibili.com/x", LINK_HOSTS)
    assert response.text == "ok"
    assert len(requests) == 2


async def test_article_media_blocks_cached_metadata_and_redirects(monkeypatch, tmp_path):
    monkeypatch.setattr("biliparser.uploader.download.LOCAL_MEDIA_FILE_PATH", tmp_path)
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        for url in ["http://127.0.0.1/secret", "https://i0.hdslb.com/redirect"]:
            assert (
                await get_media(client, "https://www.bilibili.com/read/cv123", url, "x", media_check_ignore=True)
                is None
            )
        result = await get_media(
            client, "https://www.bilibili.com/read/cv123", "https://i0.hdslb.com/image", "x", compression=False
        )
    assert result.read_bytes() == b"image"
    assert requests == ["https://i0.hdslb.com/redirect", "https://i0.hdslb.com/image"]


async def test_article_fetch_uses_canonical_host_and_checks_cached_images(monkeypatch):
    from unittest.mock import AsyncMock

    import orjson

    from biliparser.provider.bilibili.read import Read

    article = {
        "readInfo": {
            "author": {"mid": 1, "name": "author"},
            "summary": "text",
            "title": "title",
            "image_urls": ["https://i0.hdslb.com/image"],
        }
    }
    cache = AsyncMock()
    cache.get.side_effect = [None, "https://telegra.ph/article"]
    monkeypatch.setattr("biliparser.provider.bilibili.read.RedisCache", lambda: cache)
    monkeypatch.setattr(Read, "parse_reply", AsyncMock(return_value={}))
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text="window.__INITIAL_STATE__=" + orjson.dumps(article).decode() + ";(function()")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Read("https://www.bilibili.com/read/mobile?id=123", client).handle()
        assert result.mediaurls == ["https://i0.hdslb.com/image"]
        assert requests == ["https://www.bilibili.com/read/cv123"]
        article["readInfo"]["image_urls"] = ["http://127.0.0.1/secret"]
        cache.get.side_effect = [orjson.dumps(article)]
        with pytest.raises(ParserException):
            await Read("https://www.bilibili.com/read/cv123", client).handle()
        with pytest.raises(ParserException):
            await Read("https://qqbilibili.com/read/cv123", client).handle()
    assert len(requests) == 1
