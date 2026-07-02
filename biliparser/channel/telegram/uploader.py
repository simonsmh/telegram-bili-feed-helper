"""
Telegram 专属上传逻辑

TelegramUploadTask: 在基类 UploadTask 上增加 message: Message 字段
TelegramUploadQueueManager: 实现 _do_upload/_do_cache/_handle_upload_error
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from telegram import InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message
from telegram.constants import ChatType
from telegram.error import BadRequest, NetworkError, RetryAfter

from ...model import ParsedContent
from ...provider.bilibili.api import CACHES_TIMER
from ...storage.cache import RedisCache
from ...storage.models import TelegramFileCache
from ...uploader.download import cleanup_medias, get_media_for_content
from ...uploader.queue import UploadQueueManager, UploadTask
from ...utils import logger
from .formatting import format_caption_for_telegram

BILIBILI_SHARE_URL_REGEX = r"(?i)【.*】 https://[\w\.]*?(?:bilibili\.com|b23\.tv|bili2?2?3?3?\.cn)\S+"


async def get_cached_media_file_id(filename: str) -> str | None:
    file = await TelegramFileCache.get_or_none(mediafilename=filename)
    if file:
        return file.file_id
    return None


async def cache_media(mediafilename: str, file) -> None:
    if not file:
        return
    try:
        await TelegramFileCache.update_or_create(mediafilename=mediafilename, defaults=dict(file_id=file.file_id))
    except Exception as e:
        logger.exception(e)


@dataclass
class TelegramUploadTask(UploadTask):
    """在基类基础上增加 Telegram Message 引用"""

    message: Message | None = field(default=None)

    def __post_init__(self) -> None:
        if self.message is None and isinstance(self.context, Message):
            self.message = self.context


class TelegramUploadQueueManager(UploadQueueManager):
    """Telegram 专属上传队列管理器"""

    async def _cache_lookup(self, filename: str) -> str | None:
        return await get_cached_media_file_id(filename)

    async def _do_upload(self, task: UploadTask) -> Any:
        assert isinstance(task, TelegramUploadTask)
        if task.task_type == "fetch":
            await self._process_fetch_task(task)
            return None
        result = await self._upload_media(task)
        await self._try_delete_share_message(task)
        return result

    async def _do_cache(self, content: ParsedContent, result: Any) -> None:
        await self._cache_upload_result(content, result)

    async def _handle_upload_error(self, err: Exception, task: UploadTask, attempt: int, max_retries: int) -> bool:
        """返回 True 表示应重试，False 表示放弃"""
        assert isinstance(task, TelegramUploadTask)
        f = task.parsed_content
        message = task.message

        if isinstance(err, BadRequest):
            if (
                "Not enough rights to send" in err.message
                or "Need administrator rights in the channel chat" in err.message
            ):
                if message:
                    await message.chat.leave()
                return False
            if any(x in err.message for x in ["Topic_deleted", "Topic_closed", "Message thread not found"]):
                return False
            logger.error(f"任务 {task.task_id[:8]} 第 {attempt}/{max_retries} 次上传失败 (BadRequest): {err}")
            if f.media:
                f.media.need_download = True
            return True

        if isinstance(err, RetryAfter):
            await asyncio.sleep(err.retry_after)
            return True

        if isinstance(err, NetworkError):
            logger.error(f"任务 {task.task_id[:8]} 第 {attempt}/{max_retries} 次网络错误: {err}")
            return True

        return await super()._handle_upload_error(err, task, attempt, max_retries)

    async def _upload_media(self, task: TelegramUploadTask) -> Any:
        f = task.parsed_content
        message = task.message
        media = task.media
        mediathumb = task.mediathumb

        caption = format_caption_for_telegram(f, self.constraints)

        if not media or not f.media or not message:
            if message:
                await message.reply_text(caption)
            return None

        if f.media.type == "video":
            result = await message.reply_video(
                media[0],
                caption=caption,
                supports_streaming=True,
                thumbnail=mediathumb,
                duration=f.media.duration,
                filename=f.media.filenames[0] if f.media.filenames else None,
                width=f.media.dimension.get("width", 0),
                height=f.media.dimension.get("height", 0),
            )
        elif f.media.type == "audio":
            result = await message.reply_audio(
                media[0],
                caption=caption,
                duration=f.media.duration,
                performer=f.author.name,
                thumbnail=mediathumb,
                title=f.media.title,
                filename=f.media.filenames[0] if f.media.filenames else None,
            )
        elif len(f.media.urls) == 1:
            if ".gif" in f.media.urls[0]:
                result = await message.reply_animation(
                    media[0],
                    caption=caption,
                    filename=f.media.filenames[0] if f.media.filenames else None,
                )
            else:
                result = await message.reply_photo(
                    media[0],
                    caption=caption,
                    filename=f.media.filenames[0] if f.media.filenames else None,
                )
        else:
            result = await self._upload_media_group(message, f, media, mediathumb, caption)

        return result

    async def _upload_media_group(
        self, message: Message, f: ParsedContent, media: list, mediathumb: Any, caption: str
    ) -> tuple:
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
            sub_result = await message.reply_media_group(
                [
                    (
                        InputMediaVideo(img, caption=caption, filename=fn, supports_streaming=True)
                        if ".gif" in mu
                        else InputMediaPhoto(img, caption=caption, filename=fn)
                    )
                    for img, mu, fn in zip(sub_media, sub_urls, sub_fns, strict=False)
                ],
            )
            result += sub_result
        await message.reply_text(caption)
        return result

    async def _cache_upload_result(self, f: ParsedContent, result: Any) -> None:
        if result is None or not f.media or not f.media.filenames:
            return
        if isinstance(result, tuple):
            for filename, item in zip(f.media.filenames, result, strict=False):
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

    async def _process_fetch_task(self, task: TelegramUploadTask) -> None:
        f = task.parsed_content
        message = task.message
        no_media = task.fetch_mode == "cover"

        if not message or not f.media or not f.media.urls:
            return

        caption = format_caption_for_telegram(f, self.constraints)
        medias = []
        try:
            async with RedisCache().lock(f.url, timeout=CACHES_TIMER["LOCK"]):
                medias, mediathumb = await get_media_for_content(
                    f,
                    compression=False,
                    media_check_ignore=True,
                    no_media=no_media,
                    cache_lookup=self._cache_lookup,
                )
                if mediathumb:
                    medias.insert(0, mediathumb)
                    mediafilenames = [f.media.thumbnail_filename, *f.media.filenames]
                else:
                    mediafilenames = f.media.filenames

                if len(medias) == 1:
                    result = await message.reply_document(
                        document=medias[0],
                        caption=caption,
                        filename=mediafilenames[0],
                    )
                    await cache_media(mediafilenames[0], result.effective_attachment)
                else:
                    if len(medias) <= 10:
                        splits = [(medias, mediafilenames)]
                    else:
                        mid = len(medias) // 2
                        splits = [
                            (medias[:mid], mediafilenames[:mid]),
                            (medias[mid:], mediafilenames[mid:]),
                        ]
                    result = ()
                    for sub_m, sub_fn in splits:
                        sub_result = await message.reply_media_group(
                            [InputMediaDocument(m, filename=fn) for m, fn in zip(sub_m, sub_fn, strict=False)],
                        )
                        result += sub_result
                    await message.reply_text(caption)
                    for filename, item in zip(mediafilenames, result, strict=False):
                        attachment = item.effective_attachment
                        if isinstance(attachment, tuple):
                            await cache_media(filename, attachment[0])
                        else:
                            await cache_media(filename, attachment)
        except Exception as err:
            logger.exception(f"fetch 任务失败: {err} - {f.url}")
            raise  # 让 _try_upload_once 的错误处理感知到失败
        finally:
            cleanup_medias(medias)

    async def _try_delete_share_message(self, task: TelegramUploadTask) -> None:
        message = task.message
        if not message:
            return
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
                if urls[0] == message.text or (match and match.group(0) == message.text):
                    await message.delete()
        except Exception as e:
            logger.debug(f"无法删除消息: {e}")
