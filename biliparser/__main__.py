import asyncio
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from async_timeout import timeout
from bilibili_api.video import VideoQuality
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
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
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
from telegram.error import BadRequest, NetworkError, RetryAfter
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
from tqdm import tqdm

from . import biliparser
from .cache import CACHES_TIMER, RedisCache
from .database import db_close, db_init, file_cache
from .utils import (
    BILIBILI_DESKTOP_HEADER,
    LOCAL_MEDIA_FILE_PATH,
    LOCAL_MODE,
    compress,
    escape_markdown,
    logger,
    referer_url,
)

BILIBILI_URL_REGEX = r"(?i)(?:https?://)?[\w\.]*?(?:bilibili(?:bb)?\.com|(?:b23(?:bb)?|acg)\.tv|bili2?2?3?3?\.cn)\S+|BV\w{10}"
BILIBILI_SHARE_URL_REGEX = (
    r"(?i)【.*】 https://[\w\.]*?(?:bilibili\.com|b23\.tv|bili2?2?3?3?\.cn)\S+"
)

SOURCE_CODE_MARKUP = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                text="源代码",
                url="https://github.com/simonsmh/telegram-bili-feed-helper",
            )
        ]
    ]
)


@dataclass
class UploadTask:
    """上传任务数据结构"""

    user_id: int
    message: Message
    parsed_content: Any  # Video/Audio/Opus/Read/Live object
    media: list[Path | str]
    mediathumb: Path | str | None
    is_parse_cmd: bool
    is_video_cmd: bool
    urls: list[str]
    task_type: str = "parse"  # "parse" or "fetch"
    fetch_mode: str | None = None  # "file" or "cover"
    task_id: str = field(default_factory=lambda: uuid4().hex)
    cancelled: bool = field(default=False)


class UploadQueueManager:
    """上传队列管理器 - 处理 Telegram API 限流"""

    def __init__(
        self, max_workers: int = 4, max_user_tasks: int = 5, max_queue_size: int = 200
    ):
        self.queue: asyncio.Queue[UploadTask] = asyncio.Queue(maxsize=max_queue_size)
        self.max_workers = max_workers
        self.max_user_tasks = max_user_tasks
        self.active_tasks: dict[
            int, dict[str, UploadTask]
        ] = {}  # user_id -> {task_id -> UploadTask}
        self.processing_tasks: dict[
            int, dict[str, asyncio.Task]
        ] = {}  # user_id -> {task_id -> asyncio.Task}
        self.workers: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    async def submit(self, task: UploadTask) -> None:
        """提交任务到队列，支持多任务并发"""
        async with self._lock:
            # 初始化用户任务字典
            if task.user_id not in self.active_tasks:
                self.active_tasks[task.user_id] = {}

            # 检查用户任务数量是否超过限制
            user_tasks = self.active_tasks[task.user_id]
            if len(user_tasks) >= self.max_user_tasks:
                logger.warning(
                    f"用户 {task.user_id} 的任务数已达上限 ({self.max_user_tasks})，丢弃新任务 (URLs: {task.urls})"
                )
                return

            # 检查重复任务
            for existing_task in user_tasks.values():
                if existing_task.urls == task.urls:
                    logger.info(
                        f"用户 {task.user_id} 提交了重复的任务 (URLs: {task.urls})，忽略"
                    )
                    return

            # 添加新任务
            self.active_tasks[task.user_id][task.task_id] = task
            logger.info(
                f"用户 {task.user_id} 提交新任务 {task.task_id[:8]}，当前用户任务数: {len(self.active_tasks[task.user_id])}"
            )

        await self.queue.put(task)
        logger.info(
            f"任务 {task.task_id[:8]} 已提交 (用户: {task.user_id}, 类型: {task.task_type}), 当前队列深度: {self.queue.qsize()}"
        )

    async def cancel_user_tasks(self, user_id: int) -> int:
        """取消指定用户的所有任务"""
        async with self._lock:
            cancelled_count = 0

            # 取消正在运行的任务
            if user_id in self.processing_tasks:
                for task_id, task in self.processing_tasks[user_id].items():
                    task.cancel()
                    cancelled_count += 1
                del self.processing_tasks[user_id]

            # 清除活跃任务记录
            if user_id in self.active_tasks:
                cancelled_count += len(self.active_tasks[user_id])
                del self.active_tasks[user_id]

            if cancelled_count > 0:
                logger.info(f"用户 {user_id} 手动取消了 ({cancelled_count} 个任务)")
                return cancelled_count
            return 0

    async def get_user_tasks(self, user_id: int) -> list[str]:
        """获取用户当前的任务列表"""
        async with self._lock:
            tasks = self.active_tasks.get(user_id, {})
            return [
                f"{t.parsed_content.url} (ID: {t.task_id[:8]})" for t in tasks.values()
            ]

    async def _worker(self, worker_id: int) -> None:
        """上传工作线程"""
        logger.info(f"上传 Worker {worker_id} 启动")
        while True:
            try:
                task = await self.queue.get()

                # 检查任务是否仍然有效
                async with self._lock:
                    user_tasks = self.active_tasks.get(task.user_id, {})
                    if task.task_id not in user_tasks:
                        logger.info(
                            f"任务 {task.task_id[:8]} 已被取消，Worker {worker_id} 跳过 (用户: {task.user_id})"
                        )
                        self.queue.task_done()
                        continue

                logger.info(
                    f"Worker {worker_id} 开始处理任务 {task.task_id[:8]} (用户: {task.user_id})"
                )

                # 创建子任务处理上传，以便支持取消
                process_task = asyncio.create_task(self._process_upload(task))

                async with self._lock:
                    if task.user_id not in self.processing_tasks:
                        self.processing_tasks[task.user_id] = {}
                    self.processing_tasks[task.user_id][task.task_id] = process_task

                try:
                    await process_task
                except asyncio.CancelledError:
                    if process_task.cancelled():
                        logger.info(
                            f"任务 {task.task_id[:8]} 被取消 (用户: {task.user_id})"
                        )
                    else:
                        raise  # Worker 被取消
                finally:
                    async with self._lock:
                        if (
                            task.user_id in self.processing_tasks
                            and task.task_id in self.processing_tasks[task.user_id]
                        ):
                            del self.processing_tasks[task.user_id][task.task_id]
                            if not self.processing_tasks[task.user_id]:
                                del self.processing_tasks[task.user_id]

                # 清理任务记录
                async with self._lock:
                    if (
                        task.user_id in self.active_tasks
                        and task.task_id in self.active_tasks[task.user_id]
                    ):
                        del self.active_tasks[task.user_id][task.task_id]
                        if not self.active_tasks[task.user_id]:
                            del self.active_tasks[task.user_id]

                self.queue.task_done()
                logger.info(
                    f"Worker {worker_id} 完成任务 {task.task_id[:8]}, 剩余队列: {self.queue.qsize()}"
                )

            except asyncio.CancelledError:
                logger.info(f"Worker {worker_id} 被取消")
                break
            except Exception as e:
                logger.exception(f"Worker {worker_id} 异常: {e}")
                self.queue.task_done()

    async def _process_upload(self, task: UploadTask) -> None:
        """处理单个上传任务，包含重试逻辑"""
        if task.task_type == "fetch":
            await self._process_fetch_task(task)
            return

        # parse 模式的重试逻辑
        MAX_RETRIES = 4
        logger.info(f"任务 {task.task_id[:8]} 进入 _process_upload")
        for attempt in range(1, MAX_RETRIES + 1):
            logger.info(
                f"任务 {task.task_id[:8]} 尝试第 {attempt} 次，正在获取锁检查状态"
            )
            # 检查任务是否仍然有效
            async with self._lock:
                user_tasks = self.active_tasks.get(task.user_id, {})
                if task.task_id not in user_tasks:
                    logger.warning(
                        f"任务 {task.task_id[:8]} 在重试第 {attempt} 次时被取消，停止重试"
                    )
                    return
            logger.info(
                f"任务 {task.task_id[:8]} 状态检查通过，准备调用 _try_upload_once"
            )

            success = await self._try_upload_once(task, attempt, MAX_RETRIES)
            if success:
                return

            # 重试前重新解析 URL
            if attempt < MAX_RETRIES:
                if not await self._retry_parse_url(task):
                    break

    async def _try_upload_once(
        self, task: UploadTask, attempt: int, max_retries: int
    ) -> bool:
        """单次上传尝试，返回是否成功"""
        f = task.parsed_content
        message = task.message

        logger.info(f"任务 {task.task_id[:8]} 准备获取 Redis 锁: {f.url}")
        async with RedisCache().lock(f.url, timeout=2 * CACHES_TIMER["LOCK"]):
            logger.info(f"任务 {task.task_id[:8]} 已获取 Redis 锁: {f.url}")
            medias = []
            try:
                # 处理无媒体或纯文本情况
                if not f.mediaurls:
                    await message.reply_text(f.caption)
                    return True

                # 下载媒体
                logger.info(f"任务 {task.task_id[:8]} 开始下载媒体...")
                media, mediathumb = await self._download_and_prepare_media(f, message)
                logger.info(
                    f"任务 {task.task_id[:8]} 媒体下载完成: {len(media) if media else 0} 个文件"
                )
                if media is None:
                    return True

                if media:
                    medias.extend(media)
                if mediathumb:
                    medias.append(mediathumb)

                task.media = media
                task.mediathumb = mediathumb

                # 上传媒体
                logger.info(f"任务 {task.task_id[:8]} 开始上传媒体...")
                await self._upload_media(task, attempt)
                logger.info(f"任务 {task.task_id[:8]} 媒体上传完成")

                # 成功上传，尝试删除原消息
                await self._try_delete_share_message(task)

                logger.info(
                    f"任务 {task.task_id[:8]} 上传成功 (尝试 {attempt}/{max_retries})"
                )
                return True

            except (BadRequest, RetryAfter, NetworkError, httpx.HTTPError) as err:
                should_retry = await self._handle_upload_error(
                    err, task, attempt, max_retries, medias
                )
                return not should_retry
            except Exception as err:
                logger.exception(
                    f"任务 {task.task_id[:8]} 第 {attempt}/{max_retries} 次尝试发生未预期异常: {err}"
                )
                cleanup_medias(medias)
                return False
            finally:
                cleanup_medias(medias)

    async def _download_and_prepare_media(self, f, message):
        """下载并准备媒体文件"""
        media, mediathumb = await get_media_mediathumb_by_parser(f)
        if not media:
            if mediathumb:
                media = [mediathumb]
                f.mediaurls = [f.mediathumb]
                f.mediatype = "image"
            else:
                await message.reply_text(f.caption)
                return None, None
        return media, mediathumb

    async def _handle_upload_error(self, err, task, attempt, max_retries, medias):
        """处理上传错误，返回是否应该重试"""
        f = task.parsed_content
        message = task.message

        if isinstance(err, BadRequest):
            if (
                "Not enough rights to send" in err.message
                or "Need administrator rights in the channel chat" in err.message
            ):
                await message.chat.leave()
                logger.warning(
                    f"权限不足，离开聊天 {get_msg_username_or_chatid(message)}"
                )
                cleanup_medias(medias)
                return False
            elif (
                "Topic_deleted" in err.message
                or "Topic_closed" in err.message
                or "Message thread not found" in err.message
            ):
                logger.warning(f"主题已删除/关闭: {err}")
                cleanup_medias(medias)
                return False
            else:
                logger.error(
                    f"任务 {task.task_id[:8]} 第 {attempt}/{max_retries} 次上传失败 (BadRequest): {err} - {f.url}"
                )
                f.mediaraws = True
                cleanup_medias(medias)
                return True

        elif isinstance(err, RetryAfter):
            wait_time = err.retry_after
            logger.warning(
                f"任务 {task.task_id[:8]} 触发 Telegram API 限流 (RetryAfter), 等待 {wait_time} 秒: {f.url}"
            )
            cleanup_medias(medias)
            await asyncio.sleep(wait_time)
            return True

        elif isinstance(err, NetworkError):
            logger.error(
                f"任务 {task.task_id[:8]} 第 {attempt}/{max_retries} 次网络错误: {err} - {f.url}"
            )
            cleanup_medias(medias)
            return True

        elif isinstance(err, httpx.HTTPError):
            logger.error(
                f"任务 {task.task_id[:8]} 第 {attempt}/{max_retries} 次 HTTP 请求异常: {err} - {f.url}"
            )
            cleanup_medias(medias)
            return True

        return False

    async def _retry_parse_url(self, task: UploadTask) -> bool:
        """重新解析 URL，返回是否成功"""
        f = task.parsed_content
        try:
            logger.info(f"任务 {task.task_id[:8]} 正在重新解析 URL: {f.url}")
            f = (await biliparser(f.url))[0]
            if isinstance(f, Exception):
                logger.error(f"任务 {task.task_id[:8]} 重新解析失败: {f}")
                await task.message.reply_text(f"重试失败: {escape_markdown(str(f))}")
                return False
            task.parsed_content = f
            return True
        except Exception as e:
            logger.exception(f"任务 {task.task_id[:8]} 重新解析时发生异常: {e}")
            return False

    async def _process_fetch_task(self, task: UploadTask) -> None:
        """处理 fetch 任务（/file 和 /cover 命令）"""
        f = task.parsed_content
        message = task.message
        no_media = task.fetch_mode == "cover"

        if task.cancelled:
            logger.info(f"任务 {task.task_id[:8]} (fetch) 已被取消，停止处理")
            return

        async with RedisCache().lock(f.url, timeout=CACHES_TIMER["LOCK"]):
            if not f.mediaurls:
                return

            medias = []
            try:
                # 下载媒体（fetch 模式：无压缩，检查忽略）
                medias, mediathumb = await get_media_mediathumb_by_parser(
                    f,
                    compression=False,
                    media_check_ignore=True,
                    no_media=no_media,
                )

                if mediathumb:
                    medias.insert(0, mediathumb)
                    mediafilenames = [f.mediathumbfilename] + f.mediafilename
                else:
                    mediafilenames = f.mediafilename

                logger.info(f"任务 {task.task_id[:8]} (fetch) 开始上传: {f.url}")

                # Upload documents
                if len(medias) == 1:
                    result = await message.reply_document(
                        document=medias[0],
                        caption=f.caption,
                        filename=mediafilenames[0],
                    )
                    await cache_media(mediafilenames[0], result.effective_attachment)
                else:
                    # Multi-file upload
                    if len(medias) <= 10:
                        medias_splits = [medias]
                        mediafilenames_splits = [mediafilenames]
                    else:
                        mid_list = len(medias) // 2
                        medias_splits = [medias[:mid_list], medias[mid_list:]]
                        mediafilenames_splits = [
                            mediafilenames[:mid_list],
                            mediafilenames[mid_list:],
                        ]

                    result = tuple()
                    for sub_medias, sub_mediafilenames in zip(
                        medias_splits, mediafilenames_splits
                    ):
                        sub_result = await message.reply_media_group(
                            [
                                InputMediaDocument(media, filename=filename)
                                for media, filename in zip(
                                    sub_medias, sub_mediafilenames
                                )
                            ],
                        )
                        result += sub_result

                    await message.reply_text(f.caption)

                    # Cache all files
                    for filename, item in zip(mediafilenames, result):
                        attachment = item.effective_attachment
                        if isinstance(attachment, tuple):  # PhotoSize
                            await cache_media(filename, attachment[0])
                        else:
                            await cache_media(filename, attachment)

                logger.info(f"任务 {task.task_id[:8]} (fetch) 上传成功")

            except Exception as err:
                logger.exception(
                    f"任务 {task.task_id[:8]} (fetch) 失败: {err} - {f.url}"
                )
            finally:
                cleanup_medias(medias)

    async def _upload_media(self, task: UploadTask, attempt: int) -> Any:
        """执行实际的媒体上传"""
        f = task.parsed_content
        message = task.message
        media = task.media
        mediathumb = task.mediathumb

        if not media:
            if not f.mediaurls:
                await message.reply_text(f.caption)
                return None
            else:
                # 没有媒体但有URL，用缩略图
                if mediathumb:
                    media = [mediathumb]
                    f.mediaurls = [f.mediathumb]
                    f.mediatype = "image"
                else:
                    await message.reply_text(f.caption)
                    return None

        # 根据媒体类型上传
        if f.mediatype == "video":
            result = await message.reply_video(
                media[0],
                caption=f.caption,
                supports_streaming=True,
                thumbnail=mediathumb,
                duration=f.mediaduration,
                filename=f.mediafilename[0],
                width=f.mediadimention["width"],
                height=f.mediadimention["height"],
            )
        elif f.mediatype == "audio":
            result = await message.reply_audio(
                media[0],
                caption=f.caption,
                duration=f.mediaduration,
                performer=f.user,
                thumbnail=mediathumb,
                title=f.mediatitle,
                filename=f.mediafilename[0],
            )
        elif len(f.mediaurls) == 1:
            if ".gif" in f.mediaurls[0]:
                result = await message.reply_animation(
                    media[0],
                    caption=f.caption,
                    filename=f.mediafilename[0],
                )
            else:
                result = await message.reply_photo(
                    media[0],
                    caption=f.caption,
                    filename=f.mediafilename[0],
                )
        else:
            # 多图处理
            result = await self._upload_media_group(message, f, media, mediathumb)

        # 缓存文件ID
        await self._cache_upload_result(f, result)

        return result

    async def _upload_media_group(
        self, message: Message, f: Any, media: list, mediathumb: Any
    ) -> tuple:
        """上传媒体组（多图/视频）"""
        if len(f.mediaurls) <= 10:
            medias_splits = [media]
            mediaurls_splits = [f.mediaurls]
            mediafilenames_splits = [f.mediafilename]
        else:
            mid_list = len(f.mediaurls) // 2
            medias_splits = [media[:mid_list], media[mid_list:]]
            mediaurls_splits = [f.mediaurls[:mid_list], f.mediaurls[mid_list:]]
            mediafilenames_splits = [
                f.mediafilename[:mid_list],
                f.mediafilename[mid_list:],
            ]

        result = tuple()
        for sub_imgs, sub_mediaurls, sub_mediafilenames in zip(
            medias_splits, mediaurls_splits, mediafilenames_splits
        ):
            sub_result = await message.reply_media_group(
                [
                    (
                        InputMediaVideo(
                            img,
                            caption=f.caption,
                            filename=filename,
                            supports_streaming=True,
                        )
                        if ".gif" in mediaurl
                        else InputMediaPhoto(
                            img,
                            caption=f.caption,
                            filename=filename,
                        )
                    )
                    for img, mediaurl, filename in zip(
                        sub_imgs, sub_mediaurls, sub_mediafilenames
                    )
                ],
            )
            result += sub_result

        await message.reply_text(f.caption)
        return result

    async def _cache_upload_result(self, f: Any, result: Any) -> None:
        """缓存上传结果的文件ID"""
        if result is None:
            return

        if isinstance(result, tuple):  # media group
            for filename, item in zip(f.mediafilename, result):
                attachment = item.effective_attachment
                if isinstance(attachment, tuple):  # PhotoSize
                    await cache_media(filename, attachment[0])
                else:
                    await cache_media(filename, attachment)
        else:
            attachment = result.effective_attachment
            if isinstance(attachment, tuple):  # PhotoSize
                await cache_media(f.mediafilename[0], attachment[0])
            else:
                await cache_media(f.mediafilename[0], attachment)

    async def _try_delete_share_message(self, task: UploadTask) -> None:
        """尝试删除分享链接消息（隐私保护）"""
        message = task.message
        urls = task.urls

        try:
            if (
                len(urls) == 1
                and message.chat.type != ChatType.CHANNEL
                and not message.reply_to_message
                and message.text is not None
                and not message.is_automatic_forward
            ):
                match = re.match(BILIBILI_SHARE_URL_REGEX, message.text)
                if urls[0] == message.text or (
                    match and match.group(0) == message.text
                ):
                    await message.delete()
                    logger.debug(f"已删除分享消息: {message.text}")
        except Exception as e:
            logger.debug(f"无法删除消息: {e}")

    async def start_workers(self) -> None:
        """启动工作线程"""
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)
        logger.info(f"启动了 {self.max_workers} 个上传 Worker")

    async def stop_workers(self) -> None:
        """停止工作线程"""
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        logger.info("所有上传 Worker 已停止")


async def get_description(context: ContextTypes.DEFAULT_TYPE) -> str:
    bot_me = await context.bot.get_me()
    description: str = (
        f"欢迎使用 @{bot_me.username} 的 Inline 模式来转发动态，您也可以将 Bot 添加到群组或频道自动匹配消息。\n"
        f"Inline 模式限制：只可发单张图，消耗设备流量，安全性低。\n"
        f"群组模式限制：{'图片小于 10MB，视频小于 50MB，' if not LOCAL_MODE else ''}通过 Bot 上传速度较慢。\n"
    )
    return description


def get_msg_username_or_chatid(message: Message) -> str:
    return message.chat.username if message.chat.username else str(message.chat.id)


async def get_cache_media(filename) -> str | None:
    file = await file_cache.get_or_none(mediafilename=filename)
    if file:
        return file.file_id
    return None


async def get_media(
    client: httpx.AsyncClient,
    referer,
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
        file_id = await get_cache_media(filename)
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
                    raise NetworkError(
                        f"媒体文件获取错误: {response.status_code} {url}->{referer}"
                    )
                content_type = response.headers.get("content-type")
                if content_type is None:
                    raise NetworkError(
                        f"媒体文件获取错误: 无法获取 content-type {url}->{referer}"
                    )
                mediatype = content_type.split("/")
                total = int(response.headers.get("content-length", 0))
                if mediatype[0] in ["video", "audio", "application"]:
                    with open(temp_media, "wb") as file:
                        with tqdm(
                            total=total,
                            unit_scale=True,
                            unit_divisor=1024,
                            unit="B",
                            desc=response.request.url.host + "->" + filename,
                        ) as pbar:
                            async for chunk in response.aiter_bytes():
                                file.write(chunk)
                                pbar.update(len(chunk))
                elif media_check_ignore or mediatype[0] == "image":
                    img = await response.aread()
                    if compression and mediatype[1] in ["jpeg", "png"]:
                        logger.info(f"压缩: {url} {mediatype[1]}")
                        if is_thumbnail:
                            img = compress(
                                BytesIO(img), size=320, format="JPEG"
                            ).getvalue()
                        else:
                            img = compress(BytesIO(img)).getvalue()
                    with open(temp_media, "wb") as file:
                        file.write(img)
                else:
                    raise NetworkError(
                        f"媒体文件类型错误: {mediatype} {url}->{referer}"
                    )
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


async def cache_media(
    mediafilename: str,
    file,
):
    if not file:
        return
    try:
        return await file_cache.update_or_create(
            mediafilename=mediafilename, defaults=dict(file_id=file.file_id)
        )
    except Exception as e:
        logger.exception(e)
        return


async def message_to_urls(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[Message | None, list[Any]]:
    message = update.message or update.channel_post
    if message is None:
        return message, []
    if isinstance(message.forward_origin, MessageOriginUser):
        if (
            message.forward_origin.sender_user.is_bot
            and message.forward_origin.sender_user.username == context.bot.username
        ):
            return message, []
    elif isinstance(message.forward_origin, MessageOriginHiddenUser):
        if message.forward_origin.sender_user_name == context.bot.first_name:
            return message, []
    elif isinstance(message.forward_origin, MessageOriginChat):
        if message.forward_origin.author_signature == context.bot.first_name:
            return message, []
    elif isinstance(message.forward_origin, MessageOriginChannel):
        if message.forward_origin.author_signature == context.bot.first_name:
            return message, []
        else:
            ## If the bot is in the channel, return
            try:
                self_user = await message.forward_origin.chat.get_member(context.bot.id)
                ## Bot must be an administrator to access the member list.
                if self_user.status == "administrator":
                    return message, []
            except Exception:
                ## Member list is inaccessible.
                pass
    urls = re.findall(BILIBILI_URL_REGEX, message.text or message.caption or "")
    if message.entities:
        for entity in message.entities:
            if entity.url:
                urls.extend(re.findall(BILIBILI_URL_REGEX, entity.url))
    return message, urls


async def get_media_mediathumb_by_parser(
    f, compression=True, media_check_ignore=False, no_media: bool = False
) -> tuple[list | list[Path | str], Path | str | None]:
    async with httpx.AsyncClient(
        http2=True,
        follow_redirects=True,
        proxy=os.environ.get("FILE_PROXY", os.environ.get("HTTP_PROXY")),
    ) as client:
        # Handle thumbnail
        mediathumb = None
        if f.mediathumb:
            if f.mediaraws or LOCAL_MODE:
                mediathumb = await get_media(
                    client,
                    f.url,
                    f.mediathumb,
                    f.mediathumbfilename,
                    compression=compression,
                    media_check_ignore=False,
                    no_cache=True,
                    is_thumbnail=True,
                )
            else:
                mediathumb = referer_url(f.mediathumb, f.url)

        # Handle main media
        media = []
        if no_media:
            return media, mediathumb

        # Local mode or raw media requested
        if f.mediaraws or LOCAL_MODE:
            if hasattr(f, "dashtype") and f.dashtype == "dash":
                media = await handle_dash_media(f, client)
                if media:
                    return media, mediathumb
            tasks = [
                get_media(
                    client,
                    f.url,
                    m,
                    fn,
                    compression=compression,
                    media_check_ignore=media_check_ignore,
                )
                for m, fn in zip(f.mediaurls, f.mediafilename)
            ]
            media = [m for m in await asyncio.gather(*tasks) if m]

        # Remote mode
        else:
            if hasattr(f, "dashtype") and f.dashtype == "dash":
                cache_dash = await get_cache_media(f.mediafilename[0])
                if cache_dash:
                    media = [cache_dash]
                    return media, mediathumb
            if f.mediatype in ["video", "audio"]:
                media = [referer_url(f.mediaurls[0], f.url)]
            else:
                media = f.mediaurls

        return media, mediathumb


async def handle_dash_media(f, client: httpx.AsyncClient):
    res = []
    try:
        if (
            f.mediatype == "image" or f.quality != VideoQuality._8K
        ):  # 仅支持dash/自定义清晰度的场景
            f.mediatype = "video"
            cache_dash_file = LOCAL_MEDIA_FILE_PATH / f"{f.bvid}{f.quality.name}.mp4"
        else:
            cache_dash_file = LOCAL_MEDIA_FILE_PATH / f.mediafilename[0]
        cache_dash = await get_cache_media(cache_dash_file.name)
        if cache_dash:
            return [cache_dash]

        # Download dash segments
        tasks = [
            get_media(client, f.url, m, fn) for m, fn in zip(f.dashurls, f.dashfilename)
        ]
        res = [m for m in await asyncio.gather(*tasks) if m]
        if len(res) < 2:
            logger.error(f"DASH媒体下载失败: {f.url}")
            return []
        # Merge segments
        cmd = [os.environ.get("FFMPEG_PATH", "ffmpeg"), "-y"]
        for item in res:
            cmd.extend(["-i", str(item)])
        cmd.extend(
            ["-vcodec", "copy", "-acodec", "copy", str(cache_dash_file.absolute())]
        )
        logger.info(f"开始合并，执行命令：{' '.join(cmd)}")
        subprocess.run(cmd, check=True)

        f.mediaurls = [str(cache_dash_file.absolute())]
        f.mediafilename = [cache_dash_file.name]
        logger.debug(f"合并完成: {f.url} , 文件名: {f.mediafilename}")

        return [cache_dash_file]
    except subprocess.CalledProcessError as e:
        logger.error(f"DASH媒体处理失败: {f.url} - {str(e)}")
        return []
    finally:
        for item in res:
            if isinstance(item, Path):
                item.unlink(missing_ok=True)


def cleanup_medias(medias):
    for item in medias:
        if isinstance(item, Path):
            item.unlink(missing_ok=True)


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 Bilibili URL 解析请求 - 重构版本（保持原逻辑，使用队列上传）"""
    message, urls = await message_to_urls(update, context)
    if message is None:
        return

    # 解析命令类型和参数
    isParse = bool(message.text and message.text.startswith("/parse"))
    isVideo = bool(message.text and message.text.startswith("/video"))
    extra = None

    if isVideo:
        if (
            not message.text
            or message.text == "/video"
            or len(texts := message.text.split(" ")) < 2
        ):
            await message.reply_text("参数不正确，例如：/video 720P BV1Y25Nz4EZ3")
            return
        extra = {"quality": texts[1]}

    if not urls:
        if isParse or isVideo or message.chat.type == ChatType.PRIVATE:
            await message.reply_text("链接不正确")
        return

    logger.info(
        f"Parse: {urls} (用户: {message.from_user.id if message.from_user else 'unknown'})"
    )

    try:
        await message.reply_chat_action(ChatAction.TYPING)
    except Exception:
        pass

    # 解析 URLs
    if not urls:
        return

    parsed_results = await biliparser(urls, extra=extra)

    # 处理每个解析结果
    for f in parsed_results:
        # 处理解析错误
        if isinstance(f, Exception):
            logger.warning(f"解析错误: {f}")
            if isParse or isVideo:
                await message.reply_text(str(f))
            continue

        # 处理无媒体内容（纯文本）
        if not f.mediaurls:
            await message.reply_text(f.caption)
            continue

        # 提交上传任务到队列（Worker 会处理下载和上传）
        user_id = message.from_user.id if message.from_user else message.chat.id
        task = UploadTask(
            user_id=user_id,
            message=message,
            parsed_content=f,
            media=[],  # 初始为空，Worker 会下载
            mediathumb=None,
            is_parse_cmd=isParse,
            is_video_cmd=isVideo,
            urls=urls,
        )

        upload_queue_manager: UploadQueueManager = context.bot_data[
            "upload_queue_manager"
        ]
        await upload_queue_manager.submit(task)
        logger.info(f"已提交上传任务: {f.url} (用户: {user_id})")


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /file 和 /cover 命令 - 重构版本（使用队列）"""
    message, urls = await message_to_urls(update, context)
    if message is None or not message.text:
        return
    if not urls:
        await message.reply_text("链接不正确")
        return

    fetch_mode = "cover" if message.text.startswith("/cover") else "file"
    logger.info(
        f"Fetch ({fetch_mode}): {urls} (用户: {message.from_user.id if message.from_user else 'unknown'})"
    )

    # 解析 URLs
    if not urls:
        return

    parsed_results = await biliparser(urls)

    # 处理每个解析结果
    for f in parsed_results:
        # 处理解析错误
        if isinstance(f, Exception):
            logger.warning(f"解析错误: {f}")
            await message.reply_text(str(f))
            continue

        # 处理无媒体内容
        if not f.mediaurls:
            continue

        # 提交 fetch 任务到队列
        user_id = message.from_user.id if message.from_user else message.chat.id
        task = UploadTask(
            user_id=user_id,
            message=message,
            parsed_content=f,
            media=[],  # Worker 会下载
            mediathumb=None,
            is_parse_cmd=False,
            is_video_cmd=False,
            urls=urls,
            task_type="fetch",
            fetch_mode=fetch_mode,
        )

        upload_queue_manager: UploadQueueManager = context.bot_data[
            "upload_queue_manager"
        ]
        await upload_queue_manager.submit(task)
        logger.info(f"已提交 fetch 任务: {f.url} (用户: {user_id}, 模式: {fetch_mode})")


async def inlineparse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def inline_query_answer(inline_query: InlineQuery, msg):
        try:
            await inline_query.answer(msg, cache_time=0, is_personal=True)
        except BadRequest as err:
            if (
                "Query is too old and response timeout expired or query id is invalid"
                in err.message
            ):
                logger.error(f"{err} -> Inline请求超时: {f.url}")
            else:
                logger.exception(err)
                raise err
        except Exception as err:
            logger.exception(err)
            raise err

    inline_query = update.inline_query
    if inline_query is None:
        return
    query = inline_query.query
    helpmsg = [
        InlineQueryResultArticle(
            id=uuid4().hex,
            title="帮助",
            description="将 Bot 添加到群组或频道可以自动匹配消息，请注意 Inline 模式存在限制：只可发单张图，消耗设备流量。",
            reply_markup=SOURCE_CODE_MARKUP,
            input_message_content=InputTextMessageContent(
                await get_description(context)
            ),
        )
    ]
    if not query:
        return await inline_query_answer(inline_query, helpmsg)
    url_re = re.search(BILIBILI_URL_REGEX, query)
    if url_re is None:
        return await inline_query_answer(inline_query, helpmsg)
    url = url_re.group(0)
    logger.info(f"Inline: {url}")
    [f] = await biliparser(url)
    if isinstance(f, Exception):
        logger.warning(f"解析错误! {f}")
        results = [
            InlineQueryResultArticle(
                id=uuid4().hex,
                title="解析错误！",
                description=escape_markdown(f.__str__()),
                input_message_content=InputTextMessageContent(str(f)),
            )
        ]
        return await inline_query_answer(inline_query, results)

    if not f.mediaurls:
        results = [
            InlineQueryResultArticle(
                id=uuid4().hex,
                title=f.user,
                description=f.content,
                input_message_content=InputTextMessageContent(f.caption),
            )
        ]
    else:
        if f.mediatype == "video":
            cache_file_id = await get_cache_media(f.mediafilename[0])
            results = [
                (
                    InlineQueryResultCachedVideo(
                        id=uuid4().hex,
                        video_file_id=cache_file_id,
                        caption=f.caption,
                        title=f.mediatitle,
                        description=f"{f.user}: {f.content}",
                    )
                    if cache_file_id
                    else InlineQueryResultVideo(
                        id=uuid4().hex,
                        caption=f.caption,
                        title=f.mediatitle,
                        description=f"{f.user}: {f.content}",
                        mime_type="video/mp4",
                        thumbnail_url=f.mediathumb,
                        video_url=referer_url(f.mediaurls[0], f.url),
                        video_duration=f.mediaduration,
                        video_width=f.mediadimention["width"],
                        video_height=f.mediadimention["height"],
                    )
                )
            ]
        elif f.mediatype == "audio":
            cache_file_id = await get_cache_media(f.mediafilename[0])
            results = [
                (
                    InlineQueryResultCachedAudio(
                        id=uuid4().hex,
                        audio_file_id=cache_file_id,
                        caption=f.caption,
                    )
                    if cache_file_id
                    else InlineQueryResultAudio(
                        id=uuid4().hex,
                        caption=f.caption,
                        title=f.mediatitle,
                        audio_duration=f.mediaduration,
                        audio_url=referer_url(f.mediaurls[0], f.url),
                        performer=f.user,
                    )
                )
            ]
        else:
            cache_file_ids = await asyncio.gather(
                *[get_cache_media(filename) for filename in f.mediafilename]
            )
            results = [
                (
                    (
                        InlineQueryResultCachedGif(
                            id=uuid4().hex,
                            gif_file_id=cache_file_id,
                            caption=f.caption,
                            title=f"{f.user}: {f.content}",
                        )
                        if ".gif" in mediaurl
                        else InlineQueryResultCachedPhoto(
                            id=uuid4().hex,
                            photo_file_id=cache_file_id,
                            caption=f.caption,
                            title=f.user,
                            description=f.content,
                        )
                    )
                    if cache_file_id
                    else (
                        InlineQueryResultGif(
                            id=uuid4().hex,
                            caption=f.caption,
                            title=f"{f.user}: {f.content}",
                            gif_url=mediaurl,
                            thumbnail_url=mediaurl,
                        )
                        if ".gif" in mediaurl
                        else InlineQueryResultPhoto(
                            id=uuid4().hex,
                            caption=f.caption,
                            title=f.user,
                            description=f.content,
                            photo_url=mediaurl + "@1280w.jpg",
                            thumbnail_url=mediaurl + "@512w_512h.jpg",
                        )
                    )
                )
                for mediaurl, cache_file_id in zip(f.mediaurls, cache_file_ids)
            ]
    return await inline_query_answer(inline_query, results)


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, urls = await message_to_urls(update, context)
    if message is None:
        return
    if not urls:
        await message.reply_text("链接不正确")
        return
    logger.info(f"Clear: {urls}")
    for f in await biliparser(urls):
        for key, value in f.cache_key.items():
            if value:
                await RedisCache().delete(value)
        await message.reply_text(f"清除缓存成功：{escape_markdown(f.url)}\n请重新获取")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """取消用户在队列中的所有任务"""
    message = update.effective_message
    if message is None:
        return

    user_id = message.from_user.id if message.from_user else message.chat.id

    # 取消该用户的所有任务
    upload_queue_manager: UploadQueueManager = context.bot_data["upload_queue_manager"]
    cancelled_count = await upload_queue_manager.cancel_user_tasks(user_id)

    if cancelled_count > 0:
        await message.reply_text(f"已取消 {cancelled_count} 个排队中的任务")
        logger.info(f"用户 {user_id} 通过 /cancel 命令取消了 {cancelled_count} 个任务")
    else:
        await message.reply_text("当前没有正在排队的任务")


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示用户当前的任务"""
    message = update.effective_message
    if message is None:
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    upload_queue_manager: UploadQueueManager = context.bot_data["upload_queue_manager"]
    user_tasks = await upload_queue_manager.get_user_tasks(user_id)

    if user_tasks:
        await message.reply_text(
            "当前正在进行的任务:\n"
            + "\n".join(escape_markdown(task) for task in user_tasks)
        )
    else:
        await message.reply_text("当前没有正在进行的任务")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception(context.error)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        await get_description(context), reply_markup=SOURCE_CODE_MARKUP
    )


async def post_init(application: Application) -> None:
    await db_init()

    # 初始化上传队列管理器
    max_workers = int(os.environ.get("UPLOAD_WORKERS", 4))
    max_user_tasks = int(os.environ.get("MAX_USER_TASKS", 5))
    max_queue_size = int(os.environ.get("MAX_QUEUE_SIZE", 200))
    upload_queue_manager = UploadQueueManager(
        max_workers=max_workers,
        max_user_tasks=max_user_tasks,
        max_queue_size=max_queue_size,
    )
    await upload_queue_manager.start_workers()
    application.bot_data["upload_queue_manager"] = upload_queue_manager
    logger.info(
        f"上传队列管理器已启动 ({max_workers} 个 worker, 单用户任务上限: {max_user_tasks})"
    )

    await application.bot.set_my_commands(
        [
            ["start", "关于本 Bot"],
            ["parse", "获取匹配内容"],
            ["file", "获取匹配内容原始文件"],
            ["cover", "获取匹配内容原始文件预览"],
            ["video", "获取匹配清晰度视频，需参数：/video 720P BV号"],
            ["clear", "清除匹配内容缓存"],
            ["tasks", "查看当前任务"],
            ["cancel", "取消正在排队的任务"],
        ]
    )
    bot_me = await application.bot.get_me()
    logger.info(f"Bot @{bot_me.username} started.")


async def post_shutdown(application: Application) -> None:
    # 停止上传队列管理器
    upload_queue_manager: UploadQueueManager | None = application.bot_data.get(
        "upload_queue_manager"
    )
    if upload_queue_manager:
        await upload_queue_manager.stop_workers()
        logger.info("上传队列管理器已停止")

    await db_close()


def add_handler(application: Application) -> None:
    application.add_handler(CommandHandler("start", start, block=False))
    application.add_handler(CommandHandler("file", fetch, block=False))
    application.add_handler(CommandHandler("cover", fetch, block=False))
    application.add_handler(CommandHandler("cancel", cancel, block=False))
    application.add_handler(CommandHandler("tasks", tasks, block=False))
    application.add_handler(CommandHandler("clear", clear, block=False))
    application.add_handler(CommandHandler("video", parse, block=False))
    application.add_handler(CommandHandler("parse", parse, block=False))
    application.add_handler(
        MessageHandler(
            filters.Entity(MessageEntity.URL)
            | filters.Entity(MessageEntity.TEXT_LINK)
            | filters.Regex(BILIBILI_URL_REGEX)
            | filters.CaptionRegex(BILIBILI_URL_REGEX),
            parse,
            block=False,
        )
    )
    application.add_handler(InlineQueryHandler(inlineparse, block=False))
    application.add_error_handler(error_handler)


def main() -> None:
    if os.environ.get("TOKEN"):
        TOKEN = os.environ["TOKEN"]
    elif len(sys.argv) >= 2:
        TOKEN = sys.argv[1]
    else:
        logger.error("Need TOKEN.")
        sys.exit(1)
    application = (
        Application.builder()
        .defaults(
            Defaults(
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_notification=True,
                allow_sending_without_reply=True,
                block=False,
            )
        )
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .media_write_timeout(300)
        .read_timeout(60)
        .write_timeout(60)
        .base_url(os.environ.get("API_BASE_URL", "https://api.telegram.org/bot"))
        .base_file_url(
            os.environ.get("API_BASE_FILE_URL", "https://api.telegram.org/file/bot")
        )
        .local_mode(bool(LOCAL_MODE))
        .concurrent_updates(
            int(os.environ.get("SEMAPHORE_SIZE", 256))
            if os.environ.get("SEMAPHORE_SIZE")
            else True
        )
        .rate_limiter(
            AIORateLimiter(max_retries=int(os.environ.get("API_MAX_RETRIES", 5)))
        )
        .build()
    )
    add_handler(application)
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


if __name__ == "__main__":
    main()
