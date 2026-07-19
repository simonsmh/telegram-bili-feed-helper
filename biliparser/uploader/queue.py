"""
平台无关的上传队列骨架

UploadTask: 基类，context: Any 由各 channel 子类具体化
UploadQueueManager: 抽象基类，提供队列/worker/重试骨架，平台相关逻辑由子类实现
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from ..model import MediaConstraints, ParsedContent
from ..provider import ProviderRegistry
from ..provider.bilibili.api import CACHES_TIMER
from ..storage.cache import auto_renewing_lock
from ..utils import logger
from .download import cleanup_medias, get_media_for_content


@dataclass
class UploadTask:
    """上传任务基类 — context 由各 channel 子类具体化"""

    user_id: int
    context: Any  # Telegram: Message; Discord: TextChannel + Message; etc.
    parsed_content: ParsedContent
    media: list[Path | str]
    mediathumb: Path | str | None
    urls: list[str]
    task_type: str = "parse"
    fetch_mode: str | None = None
    task_id: str = field(default_factory=lambda: uuid4().hex)
    cancelled: bool = False


class UploadQueueManager(ABC):
    """上传队列管理器抽象基类

    子类需实现：
    - _do_upload(task) → Any        平台特定的发送逻辑
    - _do_cache(content, result)    平台特定的缓存逻辑
    - _handle_upload_error(...)     可 override，处理平台特定异常（默认处理 httpx + 通用异常）
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        constraints: MediaConstraints,
        max_workers: int = 4,
        max_user_tasks: int = 5,
        max_queue_size: int = 200,
    ):
        self.registry = registry
        self.constraints = constraints
        self.queue: asyncio.Queue[UploadTask] = asyncio.Queue(maxsize=max_queue_size)
        self.max_workers = max_workers
        self.max_user_tasks = max_user_tasks
        self.active_tasks: dict[int, dict[str, UploadTask]] = {}
        self.processing_tasks: dict[int, dict[str, asyncio.Task]] = {}
        self.workers: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    # ── 抽象方法（子类必须实现） ──────────────────────────────────────────────

    @abstractmethod
    async def _do_upload(self, task: UploadTask) -> Any:
        """执行平台特定的发送操作，返回平台响应对象（用于缓存），fetch 任务返回 None"""

    @abstractmethod
    async def _do_cache(self, content: ParsedContent, result: Any) -> None:
        """将平台响应中的媒体标识缓存起来"""

    # ── 可 override 的错误处理 ────────────────────────────────────────────────

    async def _handle_upload_error(self, err: Exception, task: UploadTask, attempt: int, max_retries: int) -> bool:
        """处理上传错误，返回 True 表示应重试，False 表示放弃。子类可 override 处理平台特定异常。"""
        if isinstance(err, httpx.HTTPError):
            logger.error(f"任务 {task.task_id[:8]} 第 {attempt}/{max_retries} 次网络错误: {err}")
            return True
        logger.exception(f"任务 {task.task_id[:8]} 第 {attempt}/{max_retries} 次未预期异常: {err}")
        return False

    # ── 队列管理 ──────────────────────────────────────────────────────────────

    async def submit(self, task: UploadTask) -> None:
        async with self._lock:
            if task.user_id not in self.active_tasks:
                self.active_tasks[task.user_id] = {}
            user_tasks = self.active_tasks[task.user_id]
            if len(user_tasks) >= self.max_user_tasks:
                logger.warning(f"用户 {task.user_id} 的任务数已达上限 ({self.max_user_tasks})，丢弃新任务")
                return
            for existing_task in user_tasks.values():
                if existing_task.urls == task.urls:
                    logger.info(f"用户 {task.user_id} 提交了重复的任务，忽略")
                    return
            self.active_tasks[task.user_id][task.task_id] = task
        await self.queue.put(task)
        logger.info(
            f"任务 {task.task_id[:8]} 已提交 (用户: {task.user_id}, 类型: {task.task_type}), 队列深度: {self.queue.qsize()}"
        )

    async def cancel_user_tasks(self, user_id: int) -> int:
        async with self._lock:
            cancelled_count = 0
            if user_id in self.processing_tasks:
                for task in self.processing_tasks[user_id].values():
                    task.cancel()
                    cancelled_count += 1
                del self.processing_tasks[user_id]
            if user_id in self.active_tasks:
                cancelled_count += len(self.active_tasks[user_id])
                del self.active_tasks[user_id]
            if cancelled_count > 0:
                logger.info(f"用户 {user_id} 手动取消了 ({cancelled_count} 个任务)")
            return cancelled_count

    async def get_user_tasks(self, user_id: int) -> list[str]:
        async with self._lock:
            tasks = self.active_tasks.get(user_id, {})
            return [f"{t.parsed_content.url} (ID: {t.task_id[:8]})" for t in tasks.values()]

    # ── Worker 生命周期 ───────────────────────────────────────────────────────

    async def start_workers(self) -> None:
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)
        logger.info(f"启动了 {self.max_workers} 个上传 Worker")

    async def stop_workers(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        logger.info("所有上传 Worker 已停止")

    async def _worker(self, worker_id: int) -> None:
        logger.info(f"上传 Worker {worker_id} 启动")
        while True:
            try:
                task = await self.queue.get()
                async with self._lock:
                    user_tasks = self.active_tasks.get(task.user_id, {})
                    if task.task_id not in user_tasks:
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
                        if (
                            task.user_id in self.processing_tasks
                            and task.task_id in self.processing_tasks[task.user_id]
                        ):
                            del self.processing_tasks[task.user_id][task.task_id]
                            if not self.processing_tasks[task.user_id]:
                                del self.processing_tasks[task.user_id]
                async with self._lock:
                    if task.user_id in self.active_tasks and task.task_id in self.active_tasks[task.user_id]:
                        del self.active_tasks[task.user_id][task.task_id]
                        if not self.active_tasks[task.user_id]:
                            del self.active_tasks[task.user_id]
                self.queue.task_done()
            except asyncio.CancelledError:
                logger.info(f"Worker {worker_id} 被取消")
                break
            except Exception as e:
                logger.exception(f"Worker {worker_id} 异常: {e}")
                self.queue.task_done()

    # ── 上传流程 ──────────────────────────────────────────────────────────────

    async def _process_upload(self, task: UploadTask) -> None:
        MAX_RETRIES = 4  # noqa: N806
        for attempt in range(1, MAX_RETRIES + 1):
            async with self._lock:
                if task.task_id not in self.active_tasks.get(task.user_id, {}):
                    return
            success = await self._try_upload_once(task, attempt, MAX_RETRIES)
            if success:
                return
            if attempt < MAX_RETRIES and not await self._retry_parse_url(task):
                break

    async def _retry_parse_url(self, task: UploadTask) -> bool:
        """重新解析 URL，使用构造时注入的 registry"""
        try:
            logger.info(f"任务 {task.task_id[:8]} 正在重新解析 URL: {task.parsed_content.url}")
            results = await self.registry.parse([task.parsed_content.url], self.constraints)
            if results and not isinstance(results[0], Exception):
                task.parsed_content = results[0]
                return True
            logger.error(f"任务 {task.task_id[:8]} 重新解析失败")
            return False
        except Exception as e:
            logger.exception(f"任务 {task.task_id[:8]} 重新解析时发生异常: {e}")
            return False

    async def _try_upload_once(self, task: UploadTask, attempt: int, max_retries: int) -> bool:
        f = task.parsed_content
        medias: list[Path | str] = []
        try:
            async with auto_renewing_lock(f.url, timeout=2 * CACHES_TIMER["LOCK"]):
                media, mediathumb = await get_media_for_content(f, cache_lookup=self._cache_lookup)

                # 无媒体时用 thumbnail 代替（仅图片类型）
                if not media and mediathumb and f.media and f.media.type not in ["video", "audio"]:
                    media = [mediathumb]
                    mediathumb = None  # 已并入 media，避免重复加入 medias

                task.media = media or []
                task.mediathumb = mediathumb

                # 收集所有需要在 finally 中清理的本地文件
                medias.extend(m for m in media if isinstance(m, Path))
                if isinstance(mediathumb, Path):
                    medias.append(mediathumb)

                result = await self._do_upload(task)
                if result is not None:
                    await self._do_cache(f, result)
                logger.info(f"任务 {task.task_id[:8]} 上传成功 (尝试 {attempt}/{max_retries})")
                return True

        except Exception as err:
            should_retry = await self._handle_upload_error(err, task, attempt, max_retries)
            return not should_retry
        finally:
            cleanup_medias(medias)

    async def _cache_lookup(self, filename: str) -> str | None:
        """默认缓存查询：无实现，子类可 override 提供平台特定缓存"""
        return None
