"""
平台无关的媒体下载逻辑

提供给所有 channel 复用：get_media, handle_dash_media, get_media_for_content, cleanup_medias

cache_lookup: 可选的缓存查询函数，签名为 async (filename: str) -> str | None
  由调用方注入（如 Telegram channel 注入 TelegramFileCache 查询）
  不传则跳过缓存查询，始终重新下载
"""

import asyncio
import os
import subprocess
from collections.abc import Callable, Coroutine
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
from async_timeout import timeout
from tqdm import tqdm

from ..model import ParsedContent
from ..provider.bilibili.api import BILIBILI_DESKTOP_HEADER, CACHES_TIMER, referer_url
from ..utils import compress, logger

LOCAL_MEDIA_FILE_PATH = Path(os.environ.get("LOCAL_TEMP_FILE_PATH", str(Path.cwd()))) / ".tmp"
LOCAL_MODE = bool(os.environ.get("LOCAL_MODE", False))

CacheLookup = Callable[[str], Coroutine[None, None, str | None]]


def cleanup_medias(medias) -> None:
    """删除临时下载的媒体文件（Path 类型），跳过字符串（file_id）"""
    for item in medias:
        if isinstance(item, Path):
            item.unlink(missing_ok=True)


async def get_media(
    client: httpx.AsyncClient,
    referer,
    url: Path | str,
    filename: str,
    compression: bool = True,
    media_check_ignore: bool = False,
    no_cache: bool = False,
    is_thumbnail: bool = False,
    cache_lookup: CacheLookup | None = None,
) -> Path | str | None:
    """下载单个媒体文件到本地临时目录，返回本地 Path 或缓存 file_id"""
    if isinstance(url, Path):
        return url
    if not no_cache and cache_lookup is not None:
        file_id = await cache_lookup(filename)
        if file_id:
            return file_id
    LOCAL_MEDIA_FILE_PATH.mkdir(parents=True, exist_ok=True)
    media = LOCAL_MEDIA_FILE_PATH / filename
    temp_media = LOCAL_MEDIA_FILE_PATH / uuid4().hex
    try:
        header = BILIBILI_DESKTOP_HEADER.copy()
        header["Referer"] = referer
        async with timeout(CACHES_TIMER["LOCK"]), client.stream("GET", url, headers=header) as response:
            logger.info(f"下载开始: {url}")
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"媒体文件获取错误: {response.status_code} {url}->{referer}",
                    request=response.request,
                    response=response,
                )
            content_type = response.headers.get("content-type")
            if content_type is None:
                raise httpx.HTTPStatusError(
                    f"媒体文件获取错误: 无法获取 content-type {url}->{referer}",
                    request=response.request,
                    response=response,
                )
            mediatype = content_type.split("/")
            total = int(response.headers.get("content-length", 0))
            if mediatype[0] in ["video", "audio", "application"]:
                with (
                    temp_media.open("wb") as file,
                    tqdm(
                        total=total,
                        unit_scale=True,
                        unit_divisor=1024,
                        unit="B",
                        desc=response.request.url.host + "->" + filename,
                    ) as pbar,
                ):
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
                with temp_media.open("wb") as file:
                    file.write(img)
            else:
                raise ValueError(f"媒体文件类型错误: {mediatype} {url}->{referer}")
            media.unlink(missing_ok=True)
            temp_media.rename(media)
            logger.info(f"完成下载: {media}")
            return media
    except asyncio.TimeoutError:
        logger.error(f"下载超时: {url}->{referer}")
        raise httpx.TimeoutException(f"下载超时: {url}")
    except Exception as e:
        logger.error(f"下载错误: {url}->{referer}")
        logger.exception(e)
    finally:
        temp_media.unlink(missing_ok=True)


async def handle_dash_media(
    f: ParsedContent,
    client: httpx.AsyncClient,
    cache_lookup: CacheLookup | None = None,
):
    """处理 DASH 视频合并（多轨流下载后用 ffmpeg 合并）"""
    if not f.media or not f.media.merge_streams or len(f.media.urls) < 2:
        return []
    res = []
    try:
        # Use a distinct merged filename to avoid ffmpeg reading and writing the same file
        base_name = f.media.filenames[0] if f.media.filenames else "merged"
        merged_name = Path(base_name).stem + "_merged.mp4"
        cache_dash_file = LOCAL_MEDIA_FILE_PATH / merged_name

        if cache_lookup is not None:
            cache_dash = await cache_lookup(cache_dash_file.name)
            if cache_dash:
                f.media.urls = [str(cache_dash_file.absolute())]
                f.media.filenames = [cache_dash_file.name]
                f.media.merge_streams = False
                return [cache_dash]

        tasks = [
            get_media(client, f.url, m, fn, no_cache=True, cache_lookup=cache_lookup)
            for m, fn in zip(f.media.urls, f.media.filenames, strict=False)
        ]
        res = [m for m in await asyncio.gather(*tasks) if m]
        if len(res) < 2:
            logger.error(f"DASH媒体下载失败: {f.url}")
            return []
        cmd = [os.environ.get("FFMPEG_PATH", "ffmpeg"), "-y"]
        for item in res:
            cmd.extend(["-i", str(item)])
        cmd.extend(["-vcodec", "copy", "-acodec", "copy", str(cache_dash_file.absolute())])
        logger.info(f"开始合并，执行命令：{' '.join(cmd)}")
        subprocess.run(cmd, check=True)  # noqa: ASYNC221, S603

        f.media.urls = [str(cache_dash_file.absolute())]
        f.media.filenames = [cache_dash_file.name]
        f.media.merge_streams = False
        logger.debug(f"合并完成: {f.url}")
        return [cache_dash_file]
    except subprocess.CalledProcessError as e:
        logger.error(f"DASH媒体处理失败: {f.url} - {e!s}")
        return []
    finally:
        for item in res:
            if isinstance(item, Path):
                item.unlink(missing_ok=True)


async def get_media_for_content(
    f: ParsedContent,
    compression: bool = True,
    media_check_ignore: bool = False,
    no_media: bool = False,
    cache_lookup: CacheLookup | None = None,
) -> tuple[list, Path | str | None]:
    """下载并准备媒体文件，返回 (media_list, thumbnail)"""
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
                    client,
                    f.url,
                    f.media.thumbnail,
                    f.media.thumbnail_filename,
                    compression=compression,
                    media_check_ignore=False,
                    no_cache=True,
                    is_thumbnail=True,
                    cache_lookup=cache_lookup,
                )
            else:
                mediathumb = referer_url(f.media.thumbnail, f.url)

        media = []
        if no_media:
            return media, mediathumb

        if f.media.merge_streams:
            # DASH 多轨流必须下载后合并，无论是否 local 模式
            media = await handle_dash_media(f, client, cache_lookup=cache_lookup)
            if media:
                return media, mediathumb
        elif f.media.need_download or LOCAL_MODE:
            tasks = [
                get_media(
                    client,
                    f.url,
                    m,
                    fn,
                    compression=compression,
                    media_check_ignore=media_check_ignore,
                    cache_lookup=cache_lookup,
                )
                for m, fn in zip(f.media.urls, f.media.filenames, strict=False)
            ]
            media = [m for m in await asyncio.gather(*tasks) if m]
        else:
            if f.media.type in ["video", "audio"]:
                media = [referer_url(f.media.urls[0], f.url)]
            else:
                media = f.media.urls

        return media, mediathumb
