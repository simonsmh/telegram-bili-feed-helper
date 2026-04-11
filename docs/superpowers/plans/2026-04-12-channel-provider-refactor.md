# biliparser Channel/Provider/Storage 三层架构重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 biliparser 重构为 Channel（消息通道）+ Provider（内容源）+ Storage（统一数据层）三层架构，使其可扩展支持 Discord 等其他通道和 YouTube 等其他内容源。

**Architecture:** Provider 层（Bilibili）负责解析内容并按 Channel 声明的 MediaConstraints 准备媒体；Channel 层（Telegram）负责格式化文本和发送消息；Storage 层提供统一的 Redis/KV 缓存和 Tortoise ORM 连接，各层定义自己的 model 和 key。三层通过 model.py 中的 ParsedContent、MediaConstraints、PreparedMedia 数据类通信，互不直接依赖。

**Tech Stack:** Python 3.11+, httpx, tortoise-orm, redis.asyncio, python-telegram-bot, bilibili-api-python, orjson, loguru, Pillow

---

## 文件结构

### 新建文件
- `biliparser/model.py` — 中间数据模型（ParsedContent、MediaConstraints、PreparedMedia、Author、Comment、MediaInfo）
- `biliparser/storage/__init__.py` — db_init / db_close（从 database.py 迁移）
- `biliparser/storage/cache.py` — RedisCache / FakeRedis（从 cache.py 迁移，去掉 CACHES_TIMER）
- `biliparser/storage/models.py` — TelegramFileCache ORM model（从 database.py 迁移）
- `biliparser/provider/__init__.py` — Provider ABC + ProviderRegistry
- `biliparser/provider/bilibili/__init__.py` — BilibiliProvider（URL 路由 + parse + prepare_media）
- `biliparser/provider/bilibili/api.py` — BILIBILI_DESKTOP_HEADER、BILIBILI_DESKTOP_BUILD、bili_api_request、referer_url、CACHES_TIMER、ParserException
- `biliparser/provider/bilibili/credential.py` — CredentialFactory（从 credentialFactory.py 迁移）
- `biliparser/provider/bilibili/feed.py` — Feed 基类（去掉 caption/escape_markdown/MessageLimit）
- `biliparser/provider/bilibili/video.py` — Video（从 strategy/video.py 迁移，去掉 FileSizeLimit/MessageLimit）
- `biliparser/provider/bilibili/audio.py` — Audio（从 strategy/audio.py 迁移）
- `biliparser/provider/bilibili/live.py` — Live（从 strategy/live.py 迁移）
- `biliparser/provider/bilibili/opus.py` — Opus（从 strategy/opus.py 迁移）
- `biliparser/provider/bilibili/read.py` — Read（从 strategy/read.py 迁移）
- `biliparser/channel/__init__.py` — Channel ABC
- `biliparser/channel/telegram/__init__.py` — TelegramChannel
- `biliparser/channel/telegram/bot.py` — handlers + format_caption（从 __main__.py 迁移）
- `biliparser/channel/telegram/uploader.py` — UploadQueueManager + UploadTask + get_media（从 __main__.py 迁移）

### 修改文件
- `biliparser/utils.py` — 只保留 logger、compress、get_filename（删除 B站和 Telegram 专属内容）
- `biliparser/__init__.py` — 保持兼容入口，内部改用 BilibiliProvider
- `biliparser/__main__.py` — 精简为多通道启动逻辑

### 删除文件（迁移完成后）
- `biliparser/cache.py`
- `biliparser/credentialFactory.py`
- `biliparser/database.py`
- `biliparser/strategy/` 目录

---
## Task 1: 建立 model.py — 中间数据模型

**Files:**
- Create: `biliparser/model.py`
- Test: `test/test_model.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_model.py
from biliparser.model import Author, Comment, MediaInfo, MediaConstraints, ParsedContent, PreparedMedia
from pathlib import Path

def test_author_defaults():
    a = Author()
    assert a.name == ""
    assert a.uid == ""

def test_media_constraints():
    mc = MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
    )
    assert mc.local_mode is False

def test_parsed_content_minimal():
    pc = ParsedContent(url="https://example.com", author=Author())
    assert pc.title == ""
    assert pc.content == ""
    assert pc.media is None
    assert pc.comments == []
    assert pc.cache_keys == {}

def test_prepared_media_cleanup():
    pm = PreparedMedia(files=[], thumbnail=None, cleanup_paths=[])
    assert pm.files == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest test/test_model.py -v
```

Expected: `ModuleNotFoundError: No module named 'biliparser.model'`

- [ ] **Step 3: 实现 model.py**

```python
# biliparser/model.py
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
    max_upload_size: int        # bytes
    max_download_size: int      # bytes
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /root/telegram-bili-feed-helper && python -m pytest test/test_model.py -v
```

Expected: 4 tests PASSED

- [ ] **Step 5: 提交**

```bash
cd /root/telegram-bili-feed-helper && git add biliparser/model.py test/test_model.py
git commit -m "feat: add model.py with ParsedContent/MediaConstraints/PreparedMedia data classes"
```

---

## Task 2: 建立 storage/ 层 — 统一数据基础设施

**Files:**
- Create: `biliparser/storage/__init__.py`
- Create: `biliparser/storage/cache.py`
- Create: `biliparser/storage/models.py`
- Test: `test/test_storage.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_storage.py
import pytest
from biliparser.storage.cache import RedisCache, FakeRedis
from biliparser.storage import db_init, db_close
from biliparser.storage.models import TelegramFileCache

@pytest.mark.asyncio
async def test_fake_redis_set_get():
    cache = FakeRedis()
    await cache.set("test_key", "test_value", ex=60)
    result = await cache.get("test_key")
    assert result == "test_value"

@pytest.mark.asyncio
async def test_fake_redis_nx():
    cache = FakeRedis()
    await cache.set("nx_key", "first", nx=True)
    await cache.set("nx_key", "second", nx=True)
    result = await cache.get("nx_key")
    assert result == "first"

@pytest.mark.asyncio
async def test_redis_cache_singleton():
    a = RedisCache()
    b = RedisCache()
    assert a is b

def test_telegram_file_cache_model():
    assert TelegramFileCache._meta.table is not None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest test/test_storage.py -v
```

Expected: `ModuleNotFoundError: No module named 'biliparser.storage'`

- [ ] **Step 3: 实现 storage/cache.py（从 cache.py 迁移，去掉 CACHES_TIMER）**

```python
# biliparser/storage/cache.py
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import redis.asyncio as redis

LOCAL_FILE_PATH = Path(os.environ.get("LOCAL_TEMP_FILE_PATH", os.getcwd()))


class FakeLock:
    def __init__(self, store, lock_key, timeout=10):
        self.store = store
        self.lock_key = lock_key
        self.timeout = timeout
        self._acquired = False

    async def acquire(self):
        current_time = int(time.time())
        lock_value = await self.store.get(self.lock_key)
        if lock_value:
            if current_time - float(lock_value) > self.timeout:
                await self.store.set(self.lock_key, str(current_time))
                self._acquired = True
                return True
            return False
        await self.store.set(self.lock_key, str(current_time))
        self._acquired = True
        return True

    async def release(self):
        if self._acquired:
            await self.store.delete(self.lock_key)
            self._acquired = False

    async def __aenter__(self):
        while not await self.acquire():
            await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()


class FakeRedis:
    def __init__(self):
        self.cache_file = LOCAL_FILE_PATH / "cache.json"
        self.cache = self._load_cache()

    def _load_cache(self) -> dict[Any, Any]:
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                result = json.load(f)
                if isinstance(result, dict) and result.get("__version") == 2:
                    return result
        except (IOError, json.JSONDecodeError):
            pass
        return {"__version": 2}

    def _save_cache(self):
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False)

    async def get(self, key: str):
        if key == "__version":
            return None
        target = self.cache.get(key)
        if target and isinstance(target, dict):
            if target.get("timeout") and target["timeout"] < int(time.time()):
                del self.cache[key]
                self._save_cache()
                return None
            return target.get("value")
        return None

    async def set(self, key: str, value: str | bytes, ex: int | None = None,
                  nx: bool | None = None, *args, **kwargs) -> None:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if nx and key in self.cache:
            return
        self.cache[key] = {"value": value}
        if isinstance(ex, int):
            self.cache[key]["timeout"] = int(time.time()) + ex
        self._save_cache()

    async def delete(self, key: str) -> None:
        if key in self.cache:
            del self.cache[key]
            self._save_cache()

    def lock(self, key: str, timeout: int = 3600):
        return FakeLock(self, key, timeout)


class RedisCache:
    def __new__(cls):
        if not hasattr(cls, "instance"):
            if os.environ.get("REDIS_URL"):
                cls.instance = redis.Redis.from_url(os.environ["REDIS_URL"])
            else:
                cls.instance = FakeRedis()
        return cls.instance
```

- [ ] **Step 4: 实现 storage/models.py（从 database.py 迁移 file_cache model）**

```python
# biliparser/storage/models.py
import os

from tortoise import fields
from tortoise.models import Model


class TelegramFileCache(Model):
    """Telegram Channel 专用：mediafilename → file_id 映射"""
    mediafilename = fields.CharField(64, pk=True, unique=True)
    file_id = fields.CharField(128, unique=True)
    created = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = os.environ.get("FILE_TABLE", "file")
```

- [ ] **Step 5: 实现 storage/__init__.py（从 database.py 迁移 db_init/db_close）**

```python
# biliparser/storage/__init__.py
import os

from tortoise import Tortoise

from .cache import LOCAL_FILE_PATH


async def db_init() -> None:
    from ..utils import logger
    db_url = os.environ.get(
        "DATABASE_URL", "sqlite://" + str(LOCAL_FILE_PATH / "cache.db")
    )
    logger.info(f"db_url: {db_url}")
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        logger.info(f"redis_url: {redis_url}")
    await Tortoise.init(
        db_url=db_url,
        modules={"models": ["biliparser.storage.models"]},
        use_tz=True,
    )
    await Tortoise.generate_schemas()


async def db_close() -> None:
    await Tortoise.close_connections()
```

- [ ] **Step 6: 运行测试确认通过**

```bash
cd /root/telegram-bili-feed-helper && python -m pytest test/test_storage.py -v
```

Expected: 4 tests PASSED

- [ ] **Step 7: 提交**

```bash
cd /root/telegram-bili-feed-helper && git add biliparser/storage/ test/test_storage.py
git commit -m "feat: add storage layer with RedisCache, FakeRedis, TelegramFileCache, db_init/db_close"
```

---

## Task 3: 建立 provider/ 层 — Provider ABC + BilibiliProvider 骨架

**Files:**
- Create: `biliparser/provider/__init__.py`
- Create: `biliparser/provider/bilibili/__init__.py`
- Create: `biliparser/provider/bilibili/api.py`
- Create: `biliparser/provider/bilibili/credential.py`
- Test: `test/test_provider_abc.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_provider_abc.py
import pytest
from biliparser.provider import Provider, ProviderRegistry
from biliparser.model import MediaConstraints, ParsedContent

class DummyProvider(Provider):
    def can_handle(self, url: str) -> bool:
        return "dummy" in url

    async def parse(self, urls, constraints, extra=None):
        return [ParsedContent(url=u, author=__import__('biliparser.model', fromlist=['Author']).Author()) for u in urls]

    async def prepare_media(self, content, constraints):
        from biliparser.model import PreparedMedia
        return PreparedMedia(files=[], thumbnail=None)

def test_provider_registry_find():
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    assert registry.find_provider("https://dummy.com/test") is not None
    assert registry.find_provider("https://other.com/test") is None

@pytest.mark.asyncio
async def test_provider_registry_parse():
    registry = ProviderRegistry()
    registry.register(DummyProvider())
    mc = MediaConstraints(max_upload_size=50*1024*1024, max_download_size=2*1024*1024*1024, caption_max_length=1024)
    results = await registry.parse(["https://dummy.com/test"], mc)
    assert len(results) == 1
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest test/test_provider_abc.py -v
```

Expected: `ModuleNotFoundError: No module named 'biliparser.provider'`

- [ ] **Step 3: 实现 provider/__init__.py**

```python
# biliparser/provider/__init__.py
from abc import ABC, abstractmethod
from typing import Any

from ..model import MediaConstraints, ParsedContent, PreparedMedia


class Provider(ABC):
    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """URL 是否属于本 Provider"""

    @abstractmethod
    async def parse(self, urls: list[str], constraints: MediaConstraints,
                    extra: dict | None = None) -> list[ParsedContent]:
        """解析 URL 列表，返回 ParsedContent 列表"""

    @abstractmethod
    async def prepare_media(self, content: ParsedContent,
                            constraints: MediaConstraints) -> PreparedMedia:
        """按 Channel 的 constraints 下载/准备媒体"""


class ProviderRegistry:
    def __init__(self):
        self._providers: list[Provider] = []

    def register(self, provider: Provider) -> None:
        self._providers.append(provider)

    def find_provider(self, url: str) -> Provider | None:
        for provider in self._providers:
            if provider.can_handle(url):
                return provider
        return None

    async def parse(self, urls: list[str], constraints: MediaConstraints,
                    extra: dict | None = None) -> list[ParsedContent]:
        """按 URL 分发到对应 Provider 并聚合结果"""
        import asyncio
        from itertools import groupby

        # 按 provider 分组
        provider_urls: dict[int, tuple[Provider, list[str]]] = {}
        unhandled = []
        for url in urls:
            provider = self.find_provider(url)
            if provider is None:
                unhandled.append(url)
                continue
            pid = id(provider)
            if pid not in provider_urls:
                provider_urls[pid] = (provider, [])
            provider_urls[pid][1].append(url)

        tasks = [
            provider.parse(purl_list, constraints, extra)
            for provider, purl_list in provider_urls.values()
        ]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[ParsedContent] = []
        for r in results_nested:
            if isinstance(r, Exception):
                raise r
            results.extend(r)
        return results
```

- [ ] **Step 4: 实现 provider/bilibili/api.py（从 utils.py 提取 B站专属内容）**

```python
# biliparser/provider/bilibili/api.py
import os
from urllib.parse import urlencode, urljoin

from httpx import AsyncClient, HTTPStatusError, Response

from ...utils import logger

BILIBILI_DESKTOP_HEADER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) bilibili_pc/1.16.5 Chrome/108.0.5359.215 Electron/22.3.27 Safari/537.36 build/1001016005"
}
BILIBILI_DESKTOP_BUILD = "11605"

CACHE_TIMER_DEFAULTS = {
    "CREDENTIAL": 60 * 60 * 24 * 7 * 4,
    "LOCK": 60 * 60,
    "AUDIO": 60 * 60,
    "BANGUMI": 60 * 60,
    "OPUS": 60 * 60,
    "LIVE": 60 * 5,
    "READ": 60 * 60,
    "REPLY": 60 * 60,
    "VIDEO": 60 * 60,
}

CACHES_TIMER = {
    k: int(os.environ.get(f"{k}_CACHE_TIME", v))
    for k, v in CACHE_TIMER_DEFAULTS.items()
}


class ParserException(Exception):
    def __init__(self, msg, url, res=None):
        self.msg = msg
        self.url = url
        self.res = str(res) if res else None

    def __str__(self):
        return f"{self.msg}: {self.url} ->\n{self.res}"


def retry_catcher(func):
    async def inner_function(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ParserException as err:
            logger.error(err)
            return err
        except BaseException as err:
            logger.exception(err)
            return err
    return inner_function


def referer_url(url: str, referer: str) -> str:
    if not referer:
        return url
    from urllib.parse import urlencode
    params = {"url": url, "referer": referer}
    from ...utils import get_filename
    return f"https://referer.simonsmh.workers.dev/?{urlencode(params)}#{get_filename(url)}"


async def bili_api_request(client: AsyncClient, path: str, **kwargs) -> Response:
    url_prefixes = ["https://api.bilibili.com"]
    bili_apis = os.environ.get("BILI_API")
    if bili_apis:
        url_prefixes = [*bili_apis.split(","), *url_prefixes]
    for url_prefix in url_prefixes:
        try:
            url = urljoin(url_prefix.rstrip("/") + "/", path.lstrip("/"))
            resp = await client.get(url, **kwargs)
            resp.raise_for_status()
            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == 0:
                    logger.debug(f"biliAPI请求成功 [{resp.status_code}]: {url}")
                    return resp
        except HTTPStatusError as e:
            logger.warning(f"biliAPI请求失败 [{e.response.status_code}]: {e.request.url}")
        except Exception as e:
            logger.error(f"biliAPI请求异常 [{url_prefix}]: {str(e)}")
            continue
    raise ParserException("biliAPI请求错误", path)
```

- [ ] **Step 5: 实现 provider/bilibili/credential.py（从 credentialFactory.py 迁移）**

```python
# biliparser/provider/bilibili/credential.py
import asyncio
import os

import orjson
from bilibili_api import Credential
from loguru import logger

from ...storage.cache import RedisCache
from .api import CACHES_TIMER


class CredentialFactory:
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._credential = None
        return cls._instance

    async def get(self):
        async with self._lock:
            if self._credential is None:
                self._credential = Credential(
                    sessdata=os.environ.get("SESSDATA"),
                    bili_jct=os.environ.get("BILI_JCT"),
                    buvid3=os.environ.get("BUVID3"),
                    buvid4=os.environ.get("BUVID4"),
                    dedeuserid=os.environ.get("DEDEUSERID"),
                    ac_time_value=os.environ.get("AC_TIME_VALUE"),
                )
                if not os.environ.get("FORCE_REFRESH_COOKIE"):
                    try:
                        result = await RedisCache().get("credential")
                        if result:
                            self._credential = Credential().from_cookies(orjson.loads(result))
                    except Exception:
                        logger.exception("Failed to load credential.")
            try:
                if self._credential.ac_time_value and await self._credential.check_refresh():
                    await self._credential.refresh()
                    try:
                        await RedisCache().set(
                            "credential",
                            orjson.dumps(self._credential.get_cookies()),
                            ex=CACHES_TIMER["CREDENTIAL"],
                        )
                    except Exception:
                        logger.exception("Failed to save credential.")
            except Exception as e:
                logger.exception(e)
            return self._credential


credentialFactory = CredentialFactory()
```

- [ ] **Step 6: 创建 provider/bilibili/__init__.py 骨架**

```python
# biliparser/provider/bilibili/__init__.py
# BilibiliProvider 将在 Task 5 中完整实现
# 此处仅导出供其他模块使用
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
```

- [ ] **Step 7: 运行测试确认通过**

```bash
cd /root/telegram-bili-feed-helper && python -m pytest test/test_provider_abc.py -v
```

Expected: 2 tests PASSED

- [ ] **Step 8: 提交**

```bash
cd /root/telegram-bili-feed-helper && git add biliparser/provider/ test/test_provider_abc.py
git commit -m "feat: add Provider ABC, ProviderRegistry, bilibili api/credential modules"
```

---

## Task 4: 迁移 Feed 基类和 strategy/ 到 provider/bilibili/

**Files:**
- Create: `biliparser/provider/bilibili/feed.py`
- Create: `biliparser/provider/bilibili/video.py`
- Create: `biliparser/provider/bilibili/audio.py`
- Create: `biliparser/provider/bilibili/live.py`
- Create: `biliparser/provider/bilibili/opus.py`
- Create: `biliparser/provider/bilibili/read.py`

- [ ] **Step 1: 实现 provider/bilibili/feed.py（去掉 telegram 依赖）**

从 `biliparser/strategy/feed.py` 迁移，做以下修改：
- 删除 `from telegram.constants import MessageLimit`
- 删除 `caption`、`content_markdown`（Opus 子类保留自己的）、`comment_markdown`、`user_markdown`、`_try_append_within_limit` 属性
- 保留所有纯数据属性和 B站业务逻辑（`parse_reply`、`test_url_status_code`、`shrink_line`、`clean_cn_tag_style`、`wan`）
- 将 `from ..cache import CACHES_TIMER, RedisCache` 改为 `from ...storage.cache import RedisCache` 和 `from .api import CACHES_TIMER`
- 将 `from ..utils import ...` 改为 `from ...utils import ...` 和 `from .api import ...`

```python
# biliparser/provider/bilibili/feed.py
import os
import random
import re
from abc import ABC, abstractmethod
from functools import cached_property

import httpx
import orjson

from ...storage.cache import RedisCache
from ...utils import get_filename, logger
from .api import BILIBILI_DESKTOP_HEADER, CACHES_TIMER, ParserException, bili_api_request


class Feed(ABC):
    user: str = ""
    uid: str = ""
    __content: str = ""
    __mediaurls: list = []
    mediacontent: dict = {}
    mediaraws: bool = False
    mediatype: str = ""
    __mediathumb: str = ""
    mediaduration: int = 0
    mediadimention: dict = {"width": 0, "height": 0, "rotate": 0}
    mediatitle: str = ""
    mediafilesize: int = 0
    extra_markdown: str = ""
    replycontent: dict = {}

    def __init__(self, rawurl: str, client: httpx.AsyncClient):
        self.rawurl = rawurl
        self.client = client

    async def test_url_status_code(self, url, referer):
        header = BILIBILI_DESKTOP_HEADER.copy()
        header["Referer"] = referer
        select_urls = [url]
        upos_domain = os.environ.get("UPOS_DOMAIN")
        if upos_domain:
            domains = upos_domain.split(",")
            if domains:
                random.shuffle(domains)
                domain = domains.pop()
                if domain:
                    test_url = re.sub(r"https?://[^/]+/", f"https://{domain}/", url)
                    select_urls.insert(0, test_url)
        for select_url in select_urls:
            try:
                select_url = re.sub(r"&buvid=[^&]+", "&buvid=", select_url)
                async with self.client.stream("GET", select_url, headers=header) as response:
                    if response.status_code != 200:
                        continue
                    return int(response.headers.get("Content-Length", 0)), select_url
            except Exception as e:
                logger.error(f"下载链接测试错误: {url}->{referer}")
                logger.exception(e)
        return 0, url

    @staticmethod
    def make_user_markdown(user, uid):
        from ...utils import escape_markdown
        return (
            f"[@{escape_markdown(user)}](https://space.bilibili.com/{uid})"
            if user and uid
            else str()
        )

    @staticmethod
    def shrink_line(text: str):
        return (
            text.strip()
            .replace(r"\r\n", r"\n")
            .replace(r"\n*\n", r"\n")
            if text
            else str()
        )

    @staticmethod
    def clean_cn_tag_style(content: str) -> str:
        if not content:
            return ""
        return re.sub(r"\\#((?:(?!\\#).)+)\\#", r"\\#\1 ", content)

    @staticmethod
    def wan(num):
        return f"{num / 10000:.2f}万" if num >= 10000 else num

    @property
    def content(self):
        return self.shrink_line(self.__content)

    @content.setter
    def content(self, content):
        self.__content = content

    @property
    def mediaurls(self):
        return self.__mediaurls

    @mediaurls.setter
    def mediaurls(self, content):
        if isinstance(content, list):
            self.__mediaurls = content
        else:
            self.__mediaurls = [content]
        if hasattr(self, "mediafilename"):
            delattr(self, "mediafilename")

    @cached_property
    def mediafilename(self):
        return (
            [get_filename(i) for i in self.__mediaurls] if self.__mediaurls else list()
        )

    @property
    def mediathumb(self):
        return self.__mediathumb

    @mediathumb.setter
    def mediathumb(self, content):
        self.__mediathumb = content
        if hasattr(self, "mediathumbfilename"):
            delattr(self, "mediathumbfilename")

    @cached_property
    def mediathumbfilename(self):
        return get_filename(self.mediathumb) if self.mediathumb else str()

    @cached_property
    def url(self):
        return self.rawurl

    @property
    def cache_key(self):
        return {}

    async def parse_reply(self, oid, reply_type, seek_comment_id=None):
        logger.info(f"处理评论信息: 媒体ID: {oid} 评论类型: {reply_type} 评论ID {seek_comment_id}")
        cache_key = "new_reply:" + ":".join(
            str(x) for x in [oid, reply_type, seek_comment_id] if x is not None
        )
        try:
            cache = await RedisCache().get(cache_key)
        except Exception as e:
            logger.exception(f"拉取评论缓存错误: {e}")
            cache = None
        if cache:
            reply = orjson.loads(cache)
            logger.info(f"拉取评论缓存: {oid}")
        else:
            try:
                params = {"oid": oid, "type": reply_type}
                if seek_comment_id is not None:
                    params["seek_rpid"] = seek_comment_id
                r = await bili_api_request(
                    self.client,
                    "/x/v2/reply/main",
                    params=params,
                    headers={"Referer": "https://www.bilibili.com/client"},
                )
                response = r.json()
            except Exception as e:
                logger.exception(f"评论获取错误: {cache_key} {e}")
                return {}
            if not response or not response.get("data"):
                logger.warning(f"评论解析错误: {cache_key} {response}")
                return {}
            data = response["data"]
            target = None
            if seek_comment_id is not None and "replies" in data:
                for r in data["replies"]:
                    if str(r["rpid"]) == str(seek_comment_id):
                        target = r
                        break
                    else:
                        for sr in r["replies"]:
                            if str(sr["rpid"]) == str(seek_comment_id):
                                target = sr
                                break
            reply = {"top": data.get("top_replies"), "target": target}
            try:
                await RedisCache().set(
                    cache_key,
                    orjson.dumps(reply),
                    ex=CACHES_TIMER["REPLY"],
                    nx=True,
                )
            except Exception as e:
                logger.exception(f"缓存评论错误: {e}")
        return reply

    @abstractmethod
    async def handle(self):
        return self
```

- [ ] **Step 2: 迁移 video.py、audio.py、live.py、opus.py、read.py**

将 `biliparser/strategy/` 下的各文件复制到 `biliparser/provider/bilibili/`，修改 import 路径：

对每个文件做以下替换：
- `from ..cache import CACHES_TIMER, RedisCache` → `from ...storage.cache import RedisCache` + `from .api import CACHES_TIMER`
- `from ..utils import LOCAL_MODE, ParserException, bili_api_request, escape_markdown, ...` → 拆分为：
  - `from ...utils import escape_markdown, get_filename, logger`
  - `from .api import ParserException, bili_api_request, BILIBILI_DESKTOP_BUILD, referer_url`
  - `from .credential import credentialFactory`
- `from telegram.constants import FileSizeLimit, MessageLimit` → 删除，改用 `constraints.max_upload_size`（在 handle 方法签名中接收 `constraints: MediaConstraints`）
- `from .feed import Feed` → 保持不变（同目录）

video.py 中 FileSizeLimit 替换示例：
```python
# 原来：
# FileSizeLimit.FILESIZE_UPLOAD_LOCAL_MODE if LOCAL_MODE else FileSizeLimit.FILESIZE_UPLOAD
# 改为：
# constraints.max_upload_size
```

audio.py 中 FileSizeLimit 替换示例：
```python
# 原来：
# FileSizeLimit.FILESIZE_DOWNLOAD_LOCAL_MODE if LOCAL_MODE else FileSizeLimit.FILESIZE_DOWNLOAD
# 改为：
# constraints.max_upload_size
```

所有 `handle()` 方法签名改为 `async def handle(self, constraints: "MediaConstraints | None" = None, extra: dict | None = None)`，并在方法内部用 `constraints.max_upload_size if constraints else 50 * 1024 * 1024` 作为默认值。

- [ ] **Step 3: 运行现有测试确认基本功能不破坏**

```bash
python -m pytest test/test_biliparser.py::test_video_parser -v
```

Expected: PASSED（需要网络访问）

- [ ] **Step 4: 提交**

```bash
cd /root/telegram-bili-feed-helper && git add biliparser/provider/bilibili/
git commit -m "feat: migrate strategy/ to provider/bilibili/ with MediaConstraints support, remove telegram deps"
```

---

## Task 5: 实现 BilibiliProvider（完整 parse + prepare_media）

**Files:**
- Modify: `biliparser/provider/bilibili/__init__.py`
- Test: `test/test_bilibili_provider.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_bilibili_provider.py
import pytest
from biliparser.provider.bilibili import BilibiliProvider
from biliparser.model import MediaConstraints

def test_can_handle_video():
    p = BilibiliProvider()
    assert p.can_handle("https://www.bilibili.com/video/BV1bW411n7fY")

def test_can_handle_live():
    p = BilibiliProvider()
    assert p.can_handle("https://live.bilibili.com/115")

def test_can_handle_audio():
    p = BilibiliProvider()
    assert p.can_handle("https://www.bilibili.com/audio/au1360511")

def test_can_handle_dynamic():
    p = BilibiliProvider()
    assert p.can_handle("https://t.bilibili.com/379593676394065939")

def test_cannot_handle_other():
    p = BilibiliProvider()
    assert not p.can_handle("https://youtube.com/watch?v=abc")

@pytest.mark.asyncio
async def test_parse_video():
    p = BilibiliProvider()
    mc = MediaConstraints(
        max_upload_size=50 * 1024 * 1024,
        max_download_size=2 * 1024 * 1024 * 1024,
        caption_max_length=1024,
    )
    results = await p.parse(["BV1bW411n7fY"], mc)
    assert len(results) == 1
    assert results[0].url == "https://www.bilibili.com/video/av19390801?p=1"
    assert results[0].author.name != ""
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest test/test_bilibili_provider.py::test_can_handle_video -v
```

Expected: `ImportError: cannot import name 'BilibiliProvider'`

- [ ] **Step 3: 实现完整 BilibiliProvider**

```python
# biliparser/provider/bilibili/__init__.py
import asyncio
import re
import uuid
from typing import Any

from httpx import AsyncClient, HTTPStatusError

from ...model import (
    Author, Comment, MediaConstraints, MediaInfo, ParsedContent, PreparedMedia
)
from ...utils import logger
from .. import Provider
from .api import (
    BILIBILI_DESKTOP_HEADER, CACHES_TIMER, ParserException,
    bili_api_request, referer_url, retry_catcher
)
from .credential import CredentialFactory, credentialFactory
from .feed import Feed
from .audio import Audio
from .live import Live
from .opus import Opus
from .read import Read
from .video import Video


def _feed_to_parsed_content(f: Feed) -> ParsedContent:
    """将 Feed 对象转换为 ParsedContent"""
    author = Author(name=f.user or "", uid=str(f.uid) if f.uid else "")

    media = None
    if f.mediaurls:
        media = MediaInfo(
            urls=f.mediaurls,
            type=f.mediatype,
            thumbnail=f.mediathumb or "",
            duration=f.mediaduration,
            dimension=f.mediadimention,
            title=f.mediatitle or "",
            filenames=f.mediafilename,
            thumbnail_filename=f.mediathumbfilename,
            need_download=getattr(f, "mediaraws", False),
        )

    comments = []
    if isinstance(f.replycontent, dict):
        target = f.replycontent.get("target")
        if target:
            comments.append(Comment(
                author=Author(
                    name=target["member"]["uname"],
                    uid=str(target["member"]["mid"]),
                ),
                text=target["content"]["message"],
                is_target=True,
            ))
        top = f.replycontent.get("top")
        if top:
            for item in top:
                if item:
                    comments.append(Comment(
                        author=Author(
                            name=item["member"]["uname"],
                            uid=str(item["member"]["mid"]),
                        ),
                        text=item["content"]["message"],
                        is_top=True,
                    ))

    return ParsedContent(
        url=f.url,
        author=author,
        content=f.content or "",
        extra_markdown=f.extra_markdown or "",
        media=media,
        comments=comments,
        cache_keys=f.cache_key,
    )


class BilibiliProvider(Provider):
    BILIBILI_URL_PATTERN = re.compile(
        r"(?i)(?:https?://)?[\w\.]*?(?:bilibili(?:bb)?\.com|(?:b23(?:bb)?|acg)\.tv|bili2?2?3?3?\.cn)\S+|BV\w{10}|av\d+"
    )

    def can_handle(self, url: str) -> bool:
        return bool(self.BILIBILI_URL_PATTERN.search(url))

    async def parse(self, urls: list[str], constraints: MediaConstraints,
                    extra: dict | None = None) -> list[ParsedContent]:
        async with AsyncClient(
            headers=BILIBILI_DESKTOP_HEADER,
            http2=True,
            follow_redirects=True,
            cookies={"buvid3": f"{uuid.uuid4()}infoc"},
        ) as client:
            tasks = [
                self._parse_single(client, url, constraints, extra)
                for url in list(set(urls))
            ]
            callbacks = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for f in callbacks:
            if isinstance(f, Exception):
                logger.warning(f"解析异常: {f}")
                raise f
            results.append(_feed_to_parsed_content(f))
        return results

    @retry_catcher
    async def _parse_single(self, client: AsyncClient, url: str,
                             constraints: MediaConstraints,
                             extra: dict | None = None) -> Feed:
        normalized = (
            f"http://{url}"
            if not url.startswith(("http:", "https:", "av", "BV"))
            else url
        )
        if re.search(r"(?:^|/)(?:BV\w{10}|av\d+|ep\d+|ss\d+)", normalized):
            return await Video(normalized if "/" in normalized else f"b23.tv/{normalized}", client).handle(constraints, extra)
        elif re.search(r"(?:www|t|h|m)\.bilibili\.com\/(?:[^\/?]+\/)*?(?:\d+)(?:[\/?].*)?", normalized):
            return await Opus(normalized, client).handle(constraints)
        elif re.search(r"live\.bilibili\.com[\/\w]*\/(\d+)", normalized):
            return await Live(normalized, client).handle(constraints)
        elif re.search(r"bilibili\.com\/audio\/au(\d+)", normalized):
            return await Audio(normalized, client).handle(constraints)
        elif re.search(r"bilibili\.com\/read\/(?:cv|mobile\/|mobile\?id=)(\d+)", normalized):
            return await Read(normalized, client).handle(constraints)
        try:
            resp = await client.head(normalized)
        except HTTPStatusError as e:
            raise ParserException("URL请求失败", normalized)
        except Exception as e:
            raise ParserException("URL请求异常", normalized)
        resolved = str(resp.url)
        if re.search(r"video|bangumi/play|festival", resolved):
            return await Video(resolved, client).handle(constraints, extra)
        elif re.search(r"(?:www|t|h|m)\.bilibili\.com\/(?:[^\/?]+\/)*?(?:\d+)(?:[\/?].*)?", resolved):
            return await Opus(resolved, client).handle(constraints)
        elif "live" in resolved:
            return await Live(resolved, client).handle(constraints)
        elif "audio" in resolved:
            return await Audio(resolved, client).handle(constraints)
        elif "read" in resolved:
            return await Read(resolved, client).handle(constraints)
        raise ParserException("URL无可用策略", normalized)

    async def prepare_media(self, content: ParsedContent,
                            constraints: MediaConstraints) -> PreparedMedia:
        """媒体下载逻辑将在 Task 7 中从 __main__.py 迁移"""
        return PreparedMedia(files=[], thumbnail=None)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest test/test_bilibili_provider.py -v -k "not parse_video"
```

Expected: 5 tests PASSED（跳过需要网络的测试）

- [ ] **Step 5: 提交**

```bash
cd /root/telegram-bili-feed-helper && git add biliparser/provider/bilibili/__init__.py test/test_bilibili_provider.py
git commit -m "feat: implement BilibiliProvider with can_handle/parse, Feed-to-ParsedContent conversion"
```

---

## Task 6: 建立 channel/ 层 — Channel ABC + TelegramChannel 骨架

**Files:**
- Create: `biliparser/channel/__init__.py`
- Create: `biliparser/channel/telegram/__init__.py`
- Test: `test/test_channel_abc.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_channel_abc.py
from biliparser.channel import Channel
from biliparser.model import MediaConstraints

class DummyChannel(Channel):
    @property
    def media_constraints(self) -> MediaConstraints:
        return MediaConstraints(
            max_upload_size=50 * 1024 * 1024,
            max_download_size=2 * 1024 * 1024 * 1024,
            caption_max_length=1024,
        )

    def format_caption(self, content):
        return content.url

    async def send_content(self, content, media, context):
        pass

    async def send_text(self, text, context):
        pass

    async def cache_sent_media(self, content, result):
        pass

    async def get_cached_media(self, filename):
        return None

    async def start(self, provider_registry):
        pass

    async def stop(self):
        pass

def test_channel_media_constraints():
    ch = DummyChannel()
    mc = ch.media_constraints
    assert mc.max_upload_size == 50 * 1024 * 1024

def test_channel_format_caption():
    from biliparser.model import Author, ParsedContent
    ch = DummyChannel()
    pc = ParsedContent(url="https://example.com", author=Author())
    assert ch.format_caption(pc) == "https://example.com"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest test/test_channel_abc.py -v
```

Expected: `ModuleNotFoundError: No module named 'biliparser.channel'`

- [ ] **Step 3: 实现 channel/__init__.py**

```python
# biliparser/channel/__init__.py
from abc import ABC, abstractmethod
from typing import Any

from ..model import MediaConstraints, ParsedContent, PreparedMedia
from ..provider import ProviderRegistry


class Channel(ABC):
    @property
    @abstractmethod
    def media_constraints(self) -> MediaConstraints:
        """声明本通道的媒体能力"""

    @abstractmethod
    def format_caption(self, content: ParsedContent) -> str:
        """将 ParsedContent 格式化为通道特定的文本
        Telegram: MarkdownV2
        Discord: 标准 Markdown + embed
        """

    @abstractmethod
    async def send_content(self, content: ParsedContent,
                           media: PreparedMedia | None, context: Any) -> Any:
        """发送内容到通道
        通道自己决定：
        - Telegram: 根据 media.type 选 reply_video/reply_photo 等
        - Discord: 统一用 channel.send(embed=..., files=...)
        """

    @abstractmethod
    async def send_text(self, text: str, context: Any) -> None:
        """发送纯文本"""

    @abstractmethod
    async def cache_sent_media(self, content: ParsedContent, result: Any) -> None:
        """缓存已发送的媒体标识
        Telegram: 存 file_id 到 TelegramFileCache
        Discord: 可能不需要，或存 attachment URL
        """

    @abstractmethod
    async def get_cached_media(self, filename: str) -> str | None:
        """查询已缓存的媒体标识
        Telegram: 查 file_id
        Discord: 返回 None（无此机制）
        """

    @abstractmethod
    async def start(self, provider_registry: ProviderRegistry) -> None:
        """启动通道
        Telegram: 启动 polling/webhook
        Discord: 连接 gateway
        """

    @abstractmethod
    async def stop(self) -> None:
        """停止通道"""
```

- [ ] **Step 4: 实现 channel/telegram/__init__.py（TelegramChannel 骨架）**

```python
# biliparser/channel/telegram/__init__.py
import os

from ...model import MediaConstraints, ParsedContent, PreparedMedia
from ...provider import ProviderRegistry
from ...storage.models import TelegramFileCache
from ...utils import logger
from .. import Channel

# Telegram 媒体限制常量（不依赖 telegram.constants，避免循环依赖）
TELEGRAM_UPLOAD_SIZE = 50 * 1024 * 1024          # 50MB
TELEGRAM_UPLOAD_SIZE_LOCAL = 2 * 1024 * 1024 * 1024  # 2GB
TELEGRAM_CAPTION_LENGTH = 1024


class TelegramChannel(Channel):
    def __init__(self):
        self._local_mode = bool(os.environ.get("LOCAL_MODE", False))
        self._registry: ProviderRegistry | None = None

    @property
    def media_constraints(self) -> MediaConstraints:
        return MediaConstraints(
            max_upload_size=(
                TELEGRAM_UPLOAD_SIZE_LOCAL if self._local_mode else TELEGRAM_UPLOAD_SIZE
            ),
            max_download_size=TELEGRAM_UPLOAD_SIZE_LOCAL,
            caption_max_length=TELEGRAM_CAPTION_LENGTH,
            local_mode=self._local_mode,
        )

    def format_caption(self, content: ParsedContent) -> str:
        """格式化为 Telegram MarkdownV2 格式"""
        from .bot import format_caption_for_telegram
        return format_caption_for_telegram(content, self.media_constraints)

    async def send_content(self, content, media, context):
        """由 bot.py 中的 handler 调用"""
        pass

    async def send_text(self, text, context):
        pass

    async def cache_sent_media(self, content: ParsedContent, result) -> None:
        from .uploader import cache_media
        if content.media and content.media.filenames:
            await cache_media(content.media.filenames[0], result)

    async def get_cached_media(self, filename: str) -> str | None:
        file = await TelegramFileCache.get_or_none(mediafilename=filename)
        if file:
            return file.file_id
        return None

    async def start(self, provider_registry: ProviderRegistry) -> None:
        self._registry = provider_registry
        from .bot import run_bot
        await run_bot(self, provider_registry)

    async def stop(self) -> None:
        logger.info("TelegramChannel stopped")
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest test/test_channel_abc.py -v
```

Expected: 2 tests PASSED

- [ ] **Step 6: 提交**

```bash
git add biliparser/channel/ test/test_channel_abc.py
git commit -m "feat: add Channel ABC and TelegramChannel skeleton with MediaConstraints"
```

---

## Task 7: 实现 channel/telegram/bot.py — format_caption + handlers

**Files:**
- Create: `biliparser/channel/telegram/bot.py`

- [ ] **Step 1: 实现 format_caption_for_telegram（从 Feed.caption 迁移）**

```python
# biliparser/channel/telegram/bot.py
import re
from uuid import uuid4

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultAudio,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
    InputTextMessageContent,
    Message,
    MessageEntity,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
    Update,
)
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    AIORateLimiter,
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from ...model import Comment, MediaConstraints, ParsedContent
from ...provider import ProviderRegistry
from ...utils import escape_markdown, logger

BILIBILI_URL_REGEX = r"(?i)(?:https?://)?[\w\.]*?(?:bilibili(?:bb)?\.com|(?:b23(?:bb)?|acg)\.tv|bili2?2?3?3?\.cn)\S+|BV\w{10}"
BILIBILI_SHARE_URL_REGEX = (
    r"(?i)【.*】 https://[\w\.]*?(?:bilibili\.com|b23\.tv|bili2?2?3?3?\.cn)\S+"
)

SOURCE_CODE_MARKUP = InlineKeyboardMarkup(
    [[InlineKeyboardButton(text="源代码", url="https://github.com/simonsmh/telegram-bili-feed-helper")]]
)


def _make_user_markdown(name: str, uid: str) -> str:
    if name and uid:
        return f"[@{escape_markdown(name)}](https://space.bilibili.com/{uid})"
    return ""


def _clean_cn_tag_style(content: str) -> str:
    if not content:
        return ""
    return re.sub(r"\\#((?:(?!\\#).)+)\\#", r"\\#\1 ", content)


def format_caption_for_telegram(content: ParsedContent, constraints: MediaConstraints) -> str:
    """将 ParsedContent 格式化为 Telegram MarkdownV2 caption"""
    max_len = constraints.caption_max_length

    def try_append(components: list[str], text: str) -> bool:
        if not text:
            return True
        if len("".join(components + [text])) < max_len:
            components.append(text)
            return True
        return False

    components = [f"{content.extra_markdown or escape_markdown(content.url)}\n"]

    if content.author.name:
        user_md = _make_user_markdown(content.author.name, content.author.uid)
        if not try_append(components, f"{user_md}:"):
            return "".join(components)

    # content
    content_md = escape_markdown(content.content)
    if content_md and not content_md.endswith("\n"):
        content_md += "\n"

    for text in [content_md, _format_comments_markdown(content.comments)]:
        if text:
            formatted = f"\n**>{_clean_cn_tag_style(text).replace(chr(10), chr(10) + '>')}||"
            if not try_append(components, formatted):
                return "".join(components)

    return "".join(components)


def _format_comments_markdown(comments: list[Comment]) -> str:
    result = ""
    for c in comments:
        user_md = _make_user_markdown(c.author.name, c.author.uid)
        prefix = "💬\\> " if c.is_target else "🔝\\> "
        result += f"{prefix}{user_md}:\n{escape_markdown(c.text)}\n"
    return result


async def message_to_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if message is None:
        return message, []
    if isinstance(message.forward_origin, MessageOriginUser):
        if (message.forward_origin.sender_user.is_bot
                and message.forward_origin.sender_user.username == context.bot.username):
            return message, []
    elif isinstance(message.forward_origin, MessageOriginHiddenUser):
        if message.forward_origin.sender_user_name == context.bot.first_name:
            return message, []
    elif isinstance(message.forward_origin, (MessageOriginChat, MessageOriginChannel)):
        if message.forward_origin.author_signature == context.bot.first_name:
            return message, []
        if isinstance(message.forward_origin, MessageOriginChannel):
            try:
                self_user = await message.forward_origin.chat.get_member(context.bot.id)
                if self_user.status == "administrator":
                    return message, []
            except Exception:
                pass
    urls = re.findall(BILIBILI_URL_REGEX, message.text or message.caption or "")
    if message.entities:
        for entity in message.entities:
            if entity.url:
                urls.extend(re.findall(BILIBILI_URL_REGEX, entity.url))
    return message, urls


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, urls = await message_to_urls(update, context)
    if message is None:
        return
    isParse = bool(message.text and message.text.startswith("/parse"))
    isVideo = bool(message.text and message.text.startswith("/video"))
    extra = None
    if isVideo:
        if not message.text or message.text == "/video" or len(texts := message.text.split(" ")) < 2:
            await message.reply_text("参数不正确，例如：/video 720P BV1Y25Nz4EZ3")
            return
        extra = {"quality": texts[1]}
    if not urls:
        if isParse or isVideo or message.chat.type == ChatType.PRIVATE:
            await message.reply_text("链接不正确")
        return
    try:
        await message.reply_chat_action(ChatAction.TYPING)
    except Exception:
        pass
    registry: ProviderRegistry = context.bot_data["provider_registry"]
    channel = context.bot_data["telegram_channel"]
    mc = channel.media_constraints
    parsed_results = await registry.parse(urls, mc, extra=extra)
    from .uploader import UploadTask, UploadQueueManager
    for f in parsed_results:
        if isinstance(f, Exception):
            logger.warning(f"解析错误: {f}")
            if isParse or isVideo:
                await message.reply_text(str(f))
            continue
        if not f.media or not f.media.urls:
            await message.reply_text(format_caption_for_telegram(f, mc))
            continue
        user_id = message.from_user.id if message.from_user else message.chat.id
        task = UploadTask(
            user_id=user_id,
            message=message,
            parsed_content=f,
            media=[],
            mediathumb=None,
            is_parse_cmd=isParse,
            is_video_cmd=isVideo,
            urls=urls,
        )
        upload_queue_manager: UploadQueueManager = context.bot_data["upload_queue_manager"]
        await upload_queue_manager.submit(task)


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, urls = await message_to_urls(update, context)
    if message is None or not message.text:
        return
    if not urls:
        await message.reply_text("链接不正确")
        return
    fetch_mode = "cover" if message.text.startswith("/cover") else "file"
    registry: ProviderRegistry = context.bot_data["provider_registry"]
    channel = context.bot_data["telegram_channel"]
    mc = channel.media_constraints
    parsed_results = await registry.parse(urls, mc)
    from .uploader import UploadTask, UploadQueueManager
    for f in parsed_results:
        if isinstance(f, Exception):
            await message.reply_text(str(f))
            continue
        if not f.media or not f.media.urls:
            continue
        user_id = message.from_user.id if message.from_user else message.chat.id
        task = UploadTask(
            user_id=user_id,
            message=message,
            parsed_content=f,
            media=[],
            mediathumb=None,
            is_parse_cmd=False,
            is_video_cmd=False,
            urls=urls,
            task_type="fetch",
            fetch_mode=fetch_mode,
        )
        upload_queue_manager: UploadQueueManager = context.bot_data["upload_queue_manager"]
        await upload_queue_manager.submit(task)


async def inlineparse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def answer(inline_query: InlineQuery, msg):
        try:
            await inline_query.answer(msg, cache_time=0, is_personal=True)
        except BadRequest as err:
            if "Query is too old" not in err.message:
                raise

    inline_query = update.inline_query
    if inline_query is None:
        return
    query = inline_query.query
    helpmsg = [InlineQueryResultArticle(
        id=uuid4().hex, title="帮助",
        description="将 Bot 添加到群组或频道可以自动匹配消息",
        reply_markup=SOURCE_CODE_MARKUP,
        input_message_content=InputTextMessageContent("欢迎使用 Bilibili Feed Helper"),
    )]
    if not query:
        return await answer(inline_query, helpmsg)
    url_re = re.search(BILIBILI_URL_REGEX, query)
    if url_re is None:
        return await answer(inline_query, helpmsg)
    url = url_re.group(0)
    registry: ProviderRegistry = context.bot_data["provider_registry"]
    channel = context.bot_data["telegram_channel"]
    mc = channel.media_constraints
    results_list = await registry.parse([url], mc)
    if not results_list or isinstance(results_list[0], Exception):
        err = results_list[0] if results_list else Exception("解析失败")
        return await answer(inline_query, [InlineQueryResultArticle(
            id=uuid4().hex, title="解析错误！",
            description=str(err),
            input_message_content=InputTextMessageContent(str(err)),
        )])
    f = results_list[0]
    caption = format_caption_for_telegram(f, mc)
    if not f.media or not f.media.urls:
        return await answer(inline_query, [InlineQueryResultArticle(
            id=uuid4().hex, title=f.author.name, description=f.content,
            input_message_content=InputTextMessageContent(caption),
        )])
    from ...provider.bilibili.api import referer_url
    from .uploader import get_cached_media_file_id
    if f.media.type == "video":
        cache_id = await get_cached_media_file_id(f.media.filenames[0]) if f.media.filenames else None
        results = [InlineQueryResultCachedVideo(
            id=uuid4().hex, video_file_id=cache_id, caption=caption,
            title=f.media.title, description=f"{f.author.name}: {f.content}",
        ) if cache_id else InlineQueryResultVideo(
            id=uuid4().hex, caption=caption, title=f.media.title,
            description=f"{f.author.name}: {f.content}",
            mime_type="video/mp4", thumbnail_url=f.media.thumbnail,
            video_url=referer_url(f.media.urls[0], f.url),
            video_duration=f.media.duration,
            video_width=f.media.dimension.get("width", 0),
            video_height=f.media.dimension.get("height", 0),
        )]
    elif f.media.type == "audio":
        cache_id = await get_cached_media_file_id(f.media.filenames[0]) if f.media.filenames else None
        results = [InlineQueryResultCachedAudio(
            id=uuid4().hex, audio_file_id=cache_id, caption=caption,
        ) if cache_id else InlineQueryResultAudio(
            id=uuid4().hex, caption=caption, title=f.media.title,
            audio_duration=f.media.duration,
            audio_url=referer_url(f.media.urls[0], f.url),
            performer=f.author.name,
        )]
    else:
        cache_ids = [await get_cached_media_file_id(fn) for fn in f.media.filenames]
        results = [
            (InlineQueryResultCachedGif(id=uuid4().hex, gif_file_id=cid, caption=caption,
                title=f"{f.author.name}: {f.content}") if ".gif" in mu
             else InlineQueryResultCachedPhoto(id=uuid4().hex, photo_file_id=cid, caption=caption,
                title=f.author.name, description=f.content))
            if cid else
            (InlineQueryResultGif(id=uuid4().hex, caption=caption,
                title=f"{f.author.name}: {f.content}", gif_url=mu, thumbnail_url=mu)
             if ".gif" in mu else
             InlineQueryResultPhoto(id=uuid4().hex, caption=caption, title=f.author.name,
                description=f.content, photo_url=mu + "@1280w.jpg",
                thumbnail_url=mu + "@512w_512h.jpg"))
            for mu, cid in zip(f.media.urls, cache_ids)
        ]
    return await answer(inline_query, results)


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, urls = await message_to_urls(update, context)
    if message is None:
        return
    if not urls:
        await message.reply_text("链接不正确")
        return
    registry: ProviderRegistry = context.bot_data["provider_registry"]
    channel = context.bot_data["telegram_channel"]
    mc = channel.media_constraints
    from ...storage.cache import RedisCache
    for f in await registry.parse(urls, mc):
        for key, value in f.cache_keys.items():
            if value:
                await RedisCache().delete(value)
        await message.reply_text(f"清除缓存成功：{escape_markdown(f.url)}\n请重新获取")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    user_id = message.from_user.id if message.from_user else message.chat.id
    from .uploader import UploadQueueManager
    mgr: UploadQueueManager = context.bot_data["upload_queue_manager"]
    count = await mgr.cancel_user_tasks(user_id)
    await message.reply_text(f"已取消 {count} 个任务" if count else "当前没有正在排队的任务")


async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    user_id = message.from_user.id if message.from_user else message.chat.id
    from .uploader import UploadQueueManager
    mgr: UploadQueueManager = context.bot_data["upload_queue_manager"]
    user_tasks = await mgr.get_user_tasks(user_id)
    if user_tasks:
        await message.reply_text("当前正在进行的任务:\n" + "\n".join(escape_markdown(t) for t in user_tasks))
    else:
        await message.reply_text("当前没有正在进行的任务")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    bot_me = await context.bot.get_me()
    await message.reply_text(
        f"欢迎使用 @{bot_me.username} 的 Inline 模式来转发动态。",
        reply_markup=SOURCE_CODE_MARKUP,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception(context.error)


def add_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start, block=False))
    application.add_handler(CommandHandler("file", fetch, block=False))
    application.add_handler(CommandHandler("cover", fetch, block=False))
    application.add_handler(CommandHandler("cancel", cancel, block=False))
    application.add_handler(CommandHandler("tasks", tasks_cmd, block=False))
    application.add_handler(CommandHandler("clear", clear, block=False))
    application.add_handler(CommandHandler("video", parse, block=False))
    application.add_handler(CommandHandler("parse", parse, block=False))
    application.add_handler(MessageHandler(
        filters.Entity(MessageEntity.URL)
        | filters.Entity(MessageEntity.TEXT_LINK)
        | filters.Regex(BILIBILI_URL_REGEX)
        | filters.CaptionRegex(BILIBILI_URL_REGEX),
        parse, block=False,
    ))
    application.add_handler(InlineQueryHandler(inlineparse, block=False))
    application.add_error_handler(error_handler)


async def run_bot(channel, provider_registry: ProviderRegistry) -> None:
    import os
    from ...utils import logger as _logger
    from .uploader import UploadQueueManager

    TOKEN = os.environ.get("TOKEN")
    if not TOKEN:
        raise RuntimeError("TOKEN environment variable not set")

    LOCAL_MODE = bool(os.environ.get("LOCAL_MODE", False))

    application = (
        Application.builder()
        .defaults(Defaults(
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_notification=True,
            allow_sending_without_reply=True,
            block=False,
        ))
        .token(TOKEN)
        .media_write_timeout(300)
        .read_timeout(60)
        .write_timeout(60)
        .base_url(os.environ.get("API_BASE_URL", "https://api.telegram.org/bot"))
        .base_file_url(os.environ.get("API_BASE_FILE_URL", "https://api.telegram.org/file/bot"))
        .local_mode(LOCAL_MODE)
        .concurrent_updates(
            int(os.environ.get("SEMAPHORE_SIZE", 256)) if os.environ.get("SEMAPHORE_SIZE") else True
        )
        .rate_limiter(AIORateLimiter(max_retries=int(os.environ.get("API_MAX_RETRIES", 5))))
        .build()
    )

    add_handlers(application)

    async def post_init(app: Application) -> None:
        from ...storage import db_init
        await db_init()
        max_workers = int(os.environ.get("UPLOAD_WORKERS", 4))
        max_user_tasks = int(os.environ.get("MAX_USER_TASKS", 5))
        max_queue_size = int(os.environ.get("MAX_QUEUE_SIZE", 200))
        mgr = UploadQueueManager(max_workers, max_user_tasks, max_queue_size)
        await mgr.start_workers()
        app.bot_data["upload_queue_manager"] = mgr
        app.bot_data["provider_registry"] = provider_registry
        app.bot_data["telegram_channel"] = channel
        await app.bot.set_my_commands([
            ["start", "关于本 Bot"],
            ["parse", "获取匹配内容"],
            ["file", "获取匹配内容原始文件"],
            ["cover", "获取匹配内容原始文件预览"],
            ["video", "获取匹配清晰度视频，需参数：/video 720P BV号"],
            ["clear", "清除匹配内容缓存"],
            ["tasks", "查看当前任务"],
            ["cancel", "取消正在排队的任务"],
        ])
        bot_me = await app.bot.get_me()
        _logger.info(f"Bot @{bot_me.username} started.")

    async def post_shutdown(app: Application) -> None:
        mgr = app.bot_data.get("upload_queue_manager")
        if mgr:
            await mgr.stop_workers()
        from ...storage import db_close
        await db_close()

    application.post_init = post_init
    application.post_shutdown = post_shutdown

    if os.environ.get("DOMAIN"):
        application.run_webhook(
            listen=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", 9000)),
            url_path=TOKEN,
            webhook_url=f"{os.environ.get('DOMAIN')}{TOKEN}",
            max_connections=100,
        )
    else:
        application.run_polling()
```

- [ ] **Step 2: 提交**

```bash
git add biliparser/channel/telegram/bot.py
git commit -m "feat: implement TelegramChannel bot.py with format_caption and all handlers"
```

---

## Task 8: 实现 channel/telegram/uploader.py — 媒体下载和上传队列

**Files:**
- Create: `biliparser/channel/telegram/uploader.py`

- [ ] **Step 1: 实现 uploader.py（从 __main__.py 迁移 UploadTask、UploadQueueManager、get_media、handle_dash_media）**

```python
# biliparser/channel/telegram/uploader.py
import asyncio
import os
import re
import subprocess
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from async_timeout import timeout
from bilibili_api.video import VideoQuality
from telegram import InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message
from telegram.constants import ChatType
from telegram.error import BadRequest, NetworkError, RetryAfter
from tqdm import tqdm

from ...model import ParsedContent
from ...provider.bilibili.api import BILIBILI_DESKTOP_HEADER, CACHES_TIMER, referer_url
from ...storage.cache import RedisCache
from ...storage.models import TelegramFileCache
from ...utils import compress, logger

LOCAL_MEDIA_FILE_PATH = Path(os.environ.get("LOCAL_TEMP_FILE_PATH", os.getcwd())) / ".tmp"
LOCAL_MODE = bool(os.environ.get("LOCAL_MODE", False))

BILIBILI_SHARE_URL_REGEX = (
    r"(?i)【.*】 https://[\w\.]*?(?:bilibili\.com|b23\.tv|bili2?2?3?3?\.cn)\S+"
)


@dataclass
class UploadTask:
    user_id: int
    message: Message
    parsed_content: ParsedContent
    media: list[Path | str]
    mediathumb: Path | str | None
    is_parse_cmd: bool
    is_video_cmd: bool
    urls: list[str]
    task_type: str = "parse"
    fetch_mode: str | None = None
    task_id: str = field(default_factory=lambda: uuid4().hex)
    cancelled: bool = field(default=False)


async def get_cached_media_file_id(filename: str) -> str | None:
    file = await TelegramFileCache.get_or_none(mediafilename=filename)
    if file:
        return file.file_id
    return None


async def cache_media(mediafilename: str, file) -> None:
    if not file:
        return
    try:
        await TelegramFileCache.update_or_create(
            mediafilename=mediafilename, defaults=dict(file_id=file.file_id)
        )
    except Exception as e:
        logger.exception(e)


def cleanup_medias(medias):
    for item in medias:
        if isinstance(item, Path):
            item.unlink(missing_ok=True)


async def get_media(
    client: httpx.AsyncClient,
    referer: str,
    url: Path | str,
    filename: str,
    compression: bool = True,
    media_check_ignore: bool = False,
    no_cache: bool = False,
    is_thumbnail: bool = False,
) -> Path | str | None:
    if isinstance(url, Path):
        return url
    if not no_cache:
        file_id = await get_cached_media_file_id(filename)
        if file_id:
            return file_id
    LOCAL_MEDIA_FILE_PATH.mkdir(parents=True, exist_ok=True)
    media = LOCAL_MEDIA_FILE_PATH / filename
    temp_media = LOCAL_MEDIA_FILE_PATH / uuid4().hex
    try:
        header = BILIBILI_DESKTOP_HEADER.copy()
        header["Referer"] = referer
        async with timeout(CACHES_TIMER["LOCK"]):
            async with client.stream("GET", url, headers=header) as response:
                logger.info(f"下载开始: {url}")
                if response.status_code != 200:
                    raise NetworkError(f"媒体文件获取错误: {response.status_code} {url}->{referer}")
                content_type = response.headers.get("content-type")
                if content_type is None:
                    raise NetworkError(f"媒体文件获取错误: 无法获取 content-type {url}->{referer}")
                mediatype = content_type.split("/")
                total = int(response.headers.get("content-length", 0))
                if mediatype[0] in ["video", "audio", "application"]:
                    with open(temp_media, "wb") as file:
                        with tqdm(total=total, unit_scale=True, unit_divisor=1024, unit="B",
                                  desc=response.request.url.host + "->" + filename) as pbar:
                            async for chunk in response.aiter_bytes():
                                file.write(chunk)
                                pbar.update(len(chunk))
                elif media_check_ignore or mediatype[0] == "image":
                    img = await response.aread()
                    if compression and mediatype[1] in ["jpeg", "png"]:
                        logger.info(f"压缩: {url} {mediatype[1]}")
                        if is_thumbnail:
                            img = compress(BytesIO(img), size=320, format="JPEG").getvalue()
                        else:
                            img = compress(BytesIO(img)).getvalue()
                    with open(temp_media, "wb") as file:
                        file.write(img)
                else:
                    raise NetworkError(f"媒体文件类型错误: {mediatype} {url}->{referer}")
                media.unlink(missing_ok=True)
                temp_media.rename(media)
                logger.info(f"完成下载: {media}")
                return media
    except asyncio.TimeoutError:
        logger.error(f"下载超时: {url}->{referer}")
        raise NetworkError(f"下载超时: {url}")
    except Exception as e:
        logger.error(f"下载错误: {url}->{referer}")
        logger.exception(e)
    finally:
        temp_media.unlink(missing_ok=True)


async def handle_dash_media(f: ParsedContent, client: httpx.AsyncClient):
    """处理 DASH 视频合并（仅 Bilibili 视频使用）"""
    # f.media 中存储了 dash 相关信息（通过 extra 字段传递）
    res = []
    try:
        dash_info = getattr(f, "_dash_info", None)
        if not dash_info:
            return []
        dashurls = dash_info.get("dashurls", [])
        dashfilenames = dash_info.get("dashfilenames", [])
        bvid = dash_info.get("bvid", "")
        quality_name = dash_info.get("quality_name", "")

        cache_dash_file = LOCAL_MEDIA_FILE_PATH / f"{bvid}{quality_name}.mp4"
        cache_dash = await get_cached_media_file_id(cache_dash_file.name)
        if cache_dash:
            return [cache_dash]

        tasks = [get_media(client, f.url, m, fn) for m, fn in zip(dashurls, dashfilenames)]
        res = [m for m in await asyncio.gather(*tasks) if m]
        if len(res) < 2:
            logger.error(f"DASH媒体下载失败: {f.url}")
            return []
        cmd = [os.environ.get("FFMPEG_PATH", "ffmpeg"), "-y"]
        for item in res:
            cmd.extend(["-i", str(item)])
        cmd.extend(["-vcodec", "copy", "-acodec", "copy", str(cache_dash_file.absolute())])
        logger.info(f"开始合并，执行命令：{' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        logger.debug(f"合并完成: {f.url}")
        return [cache_dash_file]
    except subprocess.CalledProcessError as e:
        logger.error(f"DASH媒体处理失败: {f.url} - {str(e)}")
        return []
    finally:
        for item in res:
            if isinstance(item, Path):
                item.unlink(missing_ok=True)


async def get_media_for_content(
    f: ParsedContent, compression=True, media_check_ignore=False, no_media: bool = False
) -> tuple[list, Path | str | None]:
    """下载并准备媒体文件"""
    if not f.media:
        return [], None

    async with httpx.AsyncClient(
        http2=True,
        follow_redirects=True,
        proxy=os.environ.get("FILE_PROXY", os.environ.get("HTTP_PROXY")),
    ) as client:
        mediathumb = None
        if f.media.thumbnail:
            if f.media.need_download or LOCAL_MODE:
                mediathumb = await get_media(
                    client, f.url, f.media.thumbnail, f.media.thumbnail_filename,
                    compression=compression, media_check_ignore=False,
                    no_cache=True, is_thumbnail=True,
                )
            else:
                mediathumb = referer_url(f.media.thumbnail, f.url)

        media = []
        if no_media:
            return media, mediathumb

        if f.media.need_download or LOCAL_MODE:
            dash_info = getattr(f, "_dash_info", None)
            if dash_info and dash_info.get("dashtype") == "dash":
                media = await handle_dash_media(f, client)
                if media:
                    return media, mediathumb
            tasks = [
                get_media(client, f.url, m, fn, compression=compression,
                          media_check_ignore=media_check_ignore)
                for m, fn in zip(f.media.urls, f.media.filenames)
            ]
            media = [m for m in await asyncio.gather(*tasks) if m]
        else:
            dash_info = getattr(f, "_dash_info", None)
            if dash_info and dash_info.get("dashtype") == "dash":
                cache_dash = await get_cached_media_file_id(f.media.filenames[0])
                if cache_dash:
                    return [cache_dash], mediathumb
            if f.media.type in ["video", "audio"]:
                media = [referer_url(f.media.urls[0], f.url)]
            else:
                media = f.media.urls

        return media, mediathumb


class UploadQueueManager:
    def __init__(self, max_workers: int = 4, max_user_tasks: int = 5, max_queue_size: int = 200):
        self.queue: asyncio.Queue[UploadTask] = asyncio.Queue(maxsize=max_queue_size)
        self.max_workers = max_workers
        self.max_user_tasks = max_user_tasks
        self.active_tasks: dict[int, dict[str, UploadTask]] = {}
        self.processing_tasks: dict[int, dict[str, asyncio.Task]] = {}
        self.workers: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    async def submit(self, task: UploadTask) -> None:
        async with self._lock:
            if task.user_id not in self.active_tasks:
                self.active_tasks[task.user_id] = {}
            user_tasks = self.active_tasks[task.user_id]
            if len(user_tasks) >= self.max_user_tasks:
                logger.warning(f"用户 {task.user_id} 任务数已达上限，丢弃新任务")
                return
            for existing in user_tasks.values():
                if existing.urls == task.urls:
                    logger.info(f"用户 {task.user_id} 提交了重复任务，忽略")
                    return
            self.active_tasks[task.user_id][task.task_id] = task
        await self.queue.put(task)

    async def cancel_user_tasks(self, user_id: int) -> int:
        async with self._lock:
            count = 0
            if user_id in self.processing_tasks:
                for t in self.processing_tasks[user_id].values():
                    t.cancel()
                    count += 1
                del self.processing_tasks[user_id]
            if user_id in self.active_tasks:
                count += len(self.active_tasks[user_id])
                del self.active_tasks[user_id]
            return count

    async def get_user_tasks(self, user_id: int) -> list[str]:
        async with self._lock:
            tasks = self.active_tasks.get(user_id, {})
            return [f"{t.parsed_content.url} (ID: {t.task_id[:8]})" for t in tasks.values()]

    async def _worker(self, worker_id: int) -> None:
        logger.info(f"上传 Worker {worker_id} 启动")
        while True:
            try:
                task = await self.queue.get()
                async with self._lock:
                    if task.task_id not in self.active_tasks.get(task.user_id, {}):
                        self.queue.task_done()
                        continue
                process_task = asyncio.create_task(self._process_upload(task))
                async with self._lock:
                    if task.user_id not in self.processing_tasks:
                        self.processing_tasks[task.user_id] = {}
                    self.processing_tasks[task.user_id][task.task_id] = process_task
                try:
                    await process_task
                except asyncio.CancelledError:
                    if not process_task.cancelled():
                        raise
                finally:
                    async with self._lock:
                        if (task.user_id in self.processing_tasks
                                and task.task_id in self.processing_tasks[task.user_id]):
                            del self.processing_tasks[task.user_id][task.task_id]
                            if not self.processing_tasks[task.user_id]:
                                del self.processing_tasks[task.user_id]
                async with self._lock:
                    if (task.user_id in self.active_tasks
                            and task.task_id in self.active_tasks[task.user_id]):
                        del self.active_tasks[task.user_id][task.task_id]
                        if not self.active_tasks[task.user_id]:
                            del self.active_tasks[task.user_id]
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Worker {worker_id} 异常: {e}")
                self.queue.task_done()

    async def _process_upload(self, task: UploadTask) -> None:
        if task.task_type == "fetch":
            await self._process_fetch_task(task)
            return
        MAX_RETRIES = 4
        for attempt in range(1, MAX_RETRIES + 1):
            async with self._lock:
                if task.task_id not in self.active_tasks.get(task.user_id, {}):
                    return
            success = await self._try_upload_once(task, attempt, MAX_RETRIES)
            if success:
                return
            if attempt < MAX_RETRIES:
                from ...provider import ProviderRegistry
                # Re-parse for retry
                try:
                    from ...provider.bilibili import BilibiliProvider
                    from ...model import MediaConstraints
                    mc = MediaConstraints(
                        max_upload_size=2 * 1024 * 1024 * 1024 if LOCAL_MODE else 50 * 1024 * 1024,
                        max_download_size=2 * 1024 * 1024 * 1024,
                        caption_max_length=1024,
                        local_mode=LOCAL_MODE,
                    )
                    provider = BilibiliProvider()
                    results = await provider.parse([task.parsed_content.url], mc)
                    if results:
                        task.parsed_content = results[0]
                except Exception as e:
                    logger.exception(f"重新解析失败: {e}")
                    break

    async def _try_upload_once(self, task: UploadTask, attempt: int, max_retries: int) -> bool:
        f = task.parsed_content
        message = task.message
        medias = []
        try:
            async with RedisCache().lock(f.url, timeout=2 * CACHES_TIMER["LOCK"]):
                if not f.media or not f.media.urls:
                    from ..bot import format_caption_for_telegram
                    from ...model import MediaConstraints
                    mc = MediaConstraints(
                        max_upload_size=2 * 1024 * 1024 * 1024 if LOCAL_MODE else 50 * 1024 * 1024,
                        max_download_size=2 * 1024 * 1024 * 1024,
                        caption_max_length=1024,
                        local_mode=LOCAL_MODE,
                    )
                    await message.reply_text(format_caption_for_telegram(f, mc))
                    return True

                media, mediathumb = await get_media_for_content(f)
                if media is None:
                    return True
                if not media:
                    if mediathumb:
                        media = [mediathumb]
                    else:
                        from ..bot import format_caption_for_telegram
                        from ...model import MediaConstraints
                        mc = MediaConstraints(
                            max_upload_size=2 * 1024 * 1024 * 1024 if LOCAL_MODE else 50 * 1024 * 1024,
                            max_download_size=2 * 1024 * 1024 * 1024,
                            caption_max_length=1024,
                            local_mode=LOCAL_MODE,
                        )
                        await message.reply_text(format_caption_for_telegram(f, mc))
                        return True

                if media:
                    medias.extend(media)
                if mediathumb:
                    medias.append(mediathumb)
                task.media = media
                task.mediathumb = mediathumb

                await self._upload_media(task)
                await self._try_delete_share_message(task)
                return True

        except (BadRequest, RetryAfter, NetworkError, httpx.HTTPError) as err:
            return not await self._handle_upload_error(err, task, attempt, max_retries, medias)
        except Exception as err:
            logger.exception(f"任务 {task.task_id[:8]} 未预期异常: {err}")
            cleanup_medias(medias)
            return False
        finally:
            cleanup_medias(medias)

    async def _handle_upload_error(self, err, task, attempt, max_retries, medias) -> bool:
        f = task.parsed_content
        message = task.message
        if isinstance(err, BadRequest):
            if ("Not enough rights" in err.message or "Need administrator rights" in err.message):
                await message.chat.leave()
                cleanup_medias(medias)
                return False
            elif any(x in err.message for x in ["Topic_deleted", "Topic_closed", "Message thread not found"]):
                cleanup_medias(medias)
                return False
            else:
                f.media.need_download = True
                cleanup_medias(medias)
                return True
        elif isinstance(err, RetryAfter):
            cleanup_medias(medias)
            await asyncio.sleep(err.retry_after)
            return True
        elif isinstance(err, (NetworkError, httpx.HTTPError)):
            cleanup_medias(medias)
            return True
        return False

    async def _upload_media(self, task: UploadTask) -> Any:
        f = task.parsed_content
        message = task.message
        media = task.media
        mediathumb = task.mediathumb
        from ..bot import format_caption_for_telegram
        from ...model import MediaConstraints
        mc = MediaConstraints(
            max_upload_size=2 * 1024 * 1024 * 1024 if LOCAL_MODE else 50 * 1024 * 1024,
            max_download_size=2 * 1024 * 1024 * 1024,
            caption_max_length=1024,
            local_mode=LOCAL_MODE,
        )
        caption = format_caption_for_telegram(f, mc)

        if f.media.type == "video":
            result = await message.reply_video(
                media[0], caption=caption, supports_streaming=True,
                thumbnail=mediathumb, duration=f.media.duration,
                filename=f.media.filenames[0] if f.media.filenames else None,
                width=f.media.dimension.get("width", 0),
                height=f.media.dimension.get("height", 0),
            )
        elif f.media.type == "audio":
            result = await message.reply_audio(
                media[0], caption=caption, duration=f.media.duration,
                performer=f.author.name, thumbnail=mediathumb,
                title=f.media.title,
                filename=f.media.filenames[0] if f.media.filenames else None,
            )
        elif len(f.media.urls) == 1:
            if ".gif" in f.media.urls[0]:
                result = await message.reply_animation(
                    media[0], caption=caption,
                    filename=f.media.filenames[0] if f.media.filenames else None,
                )
            else:
                result = await message.reply_photo(
                    media[0], caption=caption,
                    filename=f.media.filenames[0] if f.media.filenames else None,
                )
        else:
            result = await self._upload_media_group(message, f, media, mediathumb, caption)

        await self._cache_upload_result(f, result)
        return result

    async def _upload_media_group(self, message, f, media, mediathumb, caption) -> tuple:
        if len(f.media.urls) <= 10:
            splits = [(media, f.media.urls, f.media.filenames)]
        else:
            mid = len(f.media.urls) // 2
            splits = [
                (media[:mid], f.media.urls[:mid], f.media.filenames[:mid]),
                (media[mid:], f.media.urls[mid:], f.media.filenames[mid:]),
            ]
        result = tuple()
        for sub_media, sub_urls, sub_fns in splits:
            sub_result = await message.reply_media_group([
                (InputMediaVideo(img, caption=caption, filename=fn, supports_streaming=True)
                 if ".gif" in mu else
                 InputMediaPhoto(img, caption=caption, filename=fn))
                for img, mu, fn in zip(sub_media, sub_urls, sub_fns)
            ])
            result += sub_result
        await message.reply_text(caption)
        return result

    async def _cache_upload_result(self, f: ParsedContent, result) -> None:
        if result is None or not f.media or not f.media.filenames:
            return
        if isinstance(result, tuple):
            for filename, item in zip(f.media.filenames, result):
                attachment = item.effective_attachment
                if isinstance(attachment, tuple):
                    await cache_media(filename, attachment[0])
                else:
                    await cache_media(filename, attachment)
        else:
            attachment = result.effective_attachment
            if isinstance(attachment, tuple):
                await cache_media(f.media.filenames[0], attachment[0])
            else:
                await cache_media(f.media.filenames[0], attachment)

    async def _process_fetch_task(self, task: UploadTask) -> None:
        f = task.parsed_content
        message = task.message
        no_media = task.fetch_mode == "cover"
        from ..bot import format_caption_for_telegram
        from ...model import MediaConstraints
        mc = MediaConstraints(
            max_upload_size=2 * 1024 * 1024 * 1024 if LOCAL_MODE else 50 * 1024 * 1024,
            max_download_size=2 * 1024 * 1024 * 1024,
            caption_max_length=1024,
            local_mode=LOCAL_MODE,
        )
        caption = format_caption_for_telegram(f, mc)
        async with RedisCache().lock(f.url, timeout=CACHES_TIMER["LOCK"]):
            if not f.media or not f.media.urls:
                return
            medias = []
            try:
                medias, mediathumb = await get_media_for_content(
                    f, compression=False, media_check_ignore=True, no_media=no_media
                )
                if mediathumb:
                    medias.insert(0, mediathumb)
                    mediafilenames = [f.media.thumbnail_filename] + f.media.filenames
                else:
                    mediafilenames = f.media.filenames

                if len(medias) == 1:
                    result = await message.reply_document(
                        document=medias[0], caption=caption, filename=mediafilenames[0],
                    )
                    await cache_media(mediafilenames[0], result.effective_attachment)
                else:
                    if len(medias) <= 10:
                        splits = [(medias, mediafilenames)]
                    else:
                        mid = len(medias) // 2
                        splits = [(medias[:mid], mediafilenames[:mid]), (medias[mid:], mediafilenames[mid:])]
                    result = tuple()
                    for sub_m, sub_fn in splits:
                        sub_result = await message.reply_media_group([
                            InputMediaDocument(m, filename=fn) for m, fn in zip(sub_m, sub_fn)
                        ])
                        result += sub_result
                    await message.reply_text(caption)
                    for filename, item in zip(mediafilenames, result):
                        attachment = item.effective_attachment
                        if isinstance(attachment, tuple):
                            await cache_media(filename, attachment[0])
                        else:
                            await cache_media(filename, attachment)
            except Exception as err:
                logger.exception(f"fetch 任务失败: {err} - {f.url}")
            finally:
                cleanup_medias(medias)

    async def _try_delete_share_message(self, task: UploadTask) -> None:
        message = task.message
        urls = task.urls
        try:
            if (len(urls) == 1 and message.chat.type != ChatType.CHANNEL
                    and not message.reply_to_message and message.text is not None
                    and not message.is_automatic_forward):
                match = re.match(BILIBILI_SHARE_URL_REGEX, message.text)
                if urls[0] == message.text or (match and match.group(0) == message.text):
                    await message.delete()
        except Exception as e:
            logger.debug(f"无法删除消息: {e}")

    async def start_workers(self) -> None:
        for i in range(self.max_workers):
            self.workers.append(asyncio.create_task(self._worker(i)))
        logger.info(f"启动了 {self.max_workers} 个上传 Worker")

    async def stop_workers(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        logger.info("所有上传 Worker 已停止")
```

- [ ] **Step 2: 提交**

```bash
git add biliparser/channel/telegram/uploader.py
git commit -m "feat: implement uploader.py with UploadQueueManager, get_media, handle_dash_media migrated from __main__.py"
```

---

## Task 9: 精简 utils.py 和更新 __init__.py 兼容入口

**Files:**
- Modify: `biliparser/utils.py`
- Modify: `biliparser/__init__.py`
- Test: `test/test_biliparser.py`（已有，验证兼容性）

- [ ] **Step 1: 精简 utils.py，只保留真正通用的工具**

```python
# biliparser/utils.py
import html
import math
import os
import re
import sys
from io import BytesIO
from pathlib import Path

from loguru import logger
from PIL import Image

LOCAL_FILE_PATH = Path(os.environ.get("LOCAL_TEMP_FILE_PATH", os.getcwd()))

logger.remove()
logger.add(sys.stdout, backtrace=True, diagnose=True)
if os.environ.get("LOG_TO_FILE"):
    logger.add("bili_feed.log", backtrace=True, diagnose=True, rotation="1 MB")


def compress(inpil, size=1280, fix_ratio=False, format="PNG") -> BytesIO:
    pil = Image.open(inpil)
    if fix_ratio:
        w, h = pil.size
        if w / h > 20:
            logger.info(f"{w}, {h}")
            new_h = math.ceil(w / 20)
            padded = Image.new("RGBA", (w, new_h))
            padded.paste(pil, (0, int((new_h - h) / 2)))
            pil = padded
        elif h / w > 20:
            logger.info(f"{w}, {h}")
            new_w = math.ceil(h / 20)
            padded = Image.new("RGBA", (new_w, h))
            padded.paste(pil, (int((new_w - h) / 2), 0))
            pil = padded
    if size > 0:
        pil.thumbnail((size, size), Image.Resampling.LANCZOS)
    outpil = BytesIO()
    if format.upper() == "JPEG":
        pil = pil.convert("RGB")
    pil.save(outpil, format, optimize=True)
    return outpil


def escape_markdown(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"([_*\[\]()~`>\#\+\-=|{}\.!\\])", r"\\\1", html.unescape(text))


def get_filename(url) -> str:
    target = re.search(r"\/([^\/]*\.\w{3,4})(?:$|\?)", url)
    if target:
        return target.group(1)
    return url
```

注意：删除以下内容（已迁移到对应层）：
- `BILIBILI_DESKTOP_HEADER`、`BILIBILI_DESKTOP_BUILD` → `provider/bilibili/api.py`
- `LOCAL_MODE`、`LOCAL_MEDIA_FILE_PATH` → `channel/telegram/uploader.py`
- `credentialFactory` 实例 → `provider/bilibili/credential.py`
- `ParserException`、`retry_catcher` → `provider/bilibili/api.py`
- `bili_api_request`、`referer_url` → `provider/bilibili/api.py`
- `from .cache import LOCAL_FILE_PATH` → 改为直接定义（`LOCAL_FILE_PATH` 留在 utils.py）
- `from .credentialFactory import CredentialFactory` → 删除

- [ ] **Step 2: 更新 __init__.py 兼容入口**

```python
# biliparser/__init__.py
"""
biliparser — 兼容入口

保持 `from biliparser import biliparser` 的向后兼容性。
内部改用 BilibiliProvider。
"""
from .model import MediaConstraints
from .provider.bilibili import BilibiliProvider

_provider = BilibiliProvider()
_default_constraints = MediaConstraints(
    max_upload_size=50 * 1024 * 1024,
    max_download_size=2 * 1024 * 1024 * 1024,
    caption_max_length=1024,
)


async def biliparser(urls, extra: dict | None = None):
    """向后兼容入口：解析 Bilibili URL，返回 ParsedContent 列表"""
    if isinstance(urls, str):
        urls = [urls]
    elif isinstance(urls, tuple):
        urls = list(urls)
    return await _provider.parse(list(set(urls)), _default_constraints, extra=extra)
```

- [ ] **Step 3: 运行现有测试确认兼容性**

```bash
python -m pytest test/test_biliparser.py::test_video_parser -v
```

Expected: PASSED（需要网络访问）

- [ ] **Step 4: 提交**

```bash
git add biliparser/utils.py biliparser/__init__.py
git commit -m "refactor: slim utils.py to logger/compress/get_filename only, update __init__.py to use BilibiliProvider"
```

---

## Task 10: 重写 __main__.py — 多通道启动逻辑

**Files:**
- Modify: `biliparser/__main__.py`

- [ ] **Step 1: 重写 __main__.py**

```python
# biliparser/__main__.py
"""
多通道启动入口

当前支持：TelegramChannel
未来可扩展：DiscordChannel、MatrixChannel 等
"""
import asyncio
import os
import sys

from .provider import ProviderRegistry
from .provider.bilibili import BilibiliProvider
from .utils import logger


def main() -> None:
    if not os.environ.get("TOKEN") and len(sys.argv) < 2:
        logger.error("Need TOKEN (env var or first argument).")
        sys.exit(1)
    if len(sys.argv) >= 2 and not os.environ.get("TOKEN"):
        os.environ["TOKEN"] = sys.argv[1]

    # 1. 注册 Provider
    registry = ProviderRegistry()
    registry.register(BilibiliProvider())
    # 未来可扩展：
    # registry.register(YouTubeProvider())

    # 2. 启动 Channel（目前只有 Telegram）
    from .channel.telegram import TelegramChannel
    telegram_channel = TelegramChannel()

    # 3. 启动（run_bot 内部处理 polling/webhook）
    asyncio.run(_start(telegram_channel, registry))


async def _start(channel, registry: ProviderRegistry) -> None:
    await channel.start(registry)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 提交**

```bash
git add biliparser/__main__.py
git commit -m "refactor: rewrite __main__.py as multi-channel launcher with ProviderRegistry"
```

---

## Task 11: 删除旧文件，运行完整测试

**Files:**
- Delete: `biliparser/cache.py`
- Delete: `biliparser/credentialFactory.py`
- Delete: `biliparser/database.py`
- Delete: `biliparser/strategy/` 目录

- [ ] **Step 1: 确认无残留引用**

```bash
grep -r "from .cache import\|from ..cache import\|from biliparser.cache" biliparser/ --include="*.py"
grep -r "from .credentialFactory\|from ..credentialFactory\|credentialFactory" biliparser/ --include="*.py" | grep -v "provider/bilibili"
grep -r "from .database\|from ..database\|from biliparser.database" biliparser/ --include="*.py"
grep -r "from .strategy\|from ..strategy\|biliparser.strategy" biliparser/ --include="*.py"
grep -r "from telegram.constants import" biliparser/provider/ --include="*.py"
```

Expected: 所有命令输出为空

- [ ] **Step 2: 删除旧文件**

```bash
rm biliparser/cache.py biliparser/credentialFactory.py biliparser/database.py
rm -rf biliparser/strategy/
```

- [ ] **Step 3: 运行完整测试套件**

```bash
python -m pytest test/ -v
```

Expected: 所有测试 PASSED

- [ ] **Step 4: 验证依赖方向（无 telegram 导入进入 provider 层）**

```bash
grep -r "from telegram" biliparser/provider/ --include="*.py"
grep -r "from telegram" biliparser/model.py
grep -r "from telegram" biliparser/storage/ --include="*.py"
```

Expected: 所有命令输出为空

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor: remove legacy cache.py, credentialFactory.py, database.py, strategy/ after migration"
```

---

## 验证清单

重构完成后，运行以下检查确认架构正确：

```bash
# 1. Provider 层不依赖 Telegram
grep -r "from telegram" biliparser/provider/ --include="*.py"  # 应为空

# 2. model.py 不依赖任何业务层
grep -r "^import\|^from" biliparser/model.py | grep -v "dataclasses\|pathlib\|__future__"  # 应为空

# 3. storage 层不依赖 Channel 或 Provider
grep -r "from.*channel\|from.*provider" biliparser/storage/ --include="*.py"  # 应为空

# 4. 兼容入口正常工作
python -c "from biliparser import biliparser; print('OK')"

# 5. 完整测试
python -m pytest test/ -v
```

