import asyncio
import os
import re
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from async_timeout import timeout
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
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
    InlineQueryHandler,
    MessageHandler,
    filters,
)
from tqdm import tqdm
from bilibili_api.video import VideoQuality

from . import biliparser
from .cache import CACHES_TIMER, RedisCache
from .database import db_close, db_init, file_cache
from .utils import (
    LOCAL_MEDIA_FILE_PATH,
    LOCAL_MODE,
    compress,
    escape_markdown,
    headers,
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
        header = headers.copy()
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
                            desc=filename,
                        ) as pbar:
                            async for chunk in response.aiter_bytes():
                                file.write(chunk)
                                pbar.update(len(chunk))
                elif media_check_ignore or mediatype[0] == "image":
                    img = await response.aread()
                    if compression and mediatype[1] in ["jpeg", "png"]:
                        logger.info(f"压缩: {url} {mediatype[1]}")
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


def message_to_urls(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[Message | None, list[Any]]:
    message = update.message or update.channel_post
    if (
        message is None
        or message.is_automatic_forward
        or (
            isinstance(message.forward_origin, MessageOriginUser)
            and (
                message.forward_origin.sender_user.is_bot
                and message.forward_origin.sender_user.username == context.bot.username
            )
        )
        or (
            isinstance(message.forward_origin, MessageOriginHiddenUser)
            and message.forward_origin.sender_user_name == context.bot.first_name
        )
        or (
            (
                isinstance(message.forward_origin, MessageOriginChat)
                or isinstance(message.forward_origin, MessageOriginChannel)
            )
            and message.forward_origin.author_signature == context.bot.first_name
        )
    ):
        return message, []
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
    message, urls = message_to_urls(update, context)
    if message is None:
        return
    isParse, isVideo = (
        bool(message.text and message.text.startswith("/parse")),
        bool(message.text and message.text.startswith("/video")),
    )
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
    logger.info(f"Parse: {urls}")
    try:
        await message.reply_chat_action(ChatAction.TYPING)
    except Exception:
        pass
    temp_msgs = []
    for f in await biliparser(urls, extra=extra):
        MAX_RETRIES: int = 4
        for attempt in range(1, MAX_RETRIES + 1):
            if isinstance(f, Exception):
                logger.warning(f"解析错误! {f}")
                if isParse or isVideo:
                    await message.reply_text(str(f))
                break
            if f.mediafilesize and (
                isParse or isVideo or message.chat.type == ChatType.PRIVATE
            ):
                wait_text = (
                    "处理中，"
                    if attempt == 1
                    else f"第 {attempt}/{MAX_RETRIES} 次尝试处理 URL：\n{escape_markdown(f.url)}\n"
                )
                wait_text += f"大约需要 {round(f.mediafilesize / 1000000)} 秒"
                wait_msg = await message.reply_text(wait_text)
                temp_msgs.append(wait_msg)
            async with RedisCache().lock(f.url, timeout=2 * CACHES_TIMER["LOCK"]):
                medias = []
                mediathumb = None
                try:
                    if not f.mediaurls:
                        await message.reply_text(f.caption)
                        break
                    else:
                        media, mediathumb = await get_media_mediathumb_by_parser(f)
                        if not media:
                            if mediathumb:
                                media = [mediathumb]
                                f.mediaurls = f.mediathumb
                                f.mediatype = "image"
                            else:
                                await message.reply_text(f.caption)
                                break
                        if f.mediatype == "video":
                            result = await message.reply_video(
                                media[0],
                                caption=f.caption,
                                supports_streaming=True,
                                cover=mediathumb,
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
                            result = await message.reply_media_group(
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
                                        media, f.mediaurls, f.mediafilename
                                    )
                                ],
                            )
                            await message.reply_text(f.caption)
                        # store file caches
                        if isinstance(result, tuple):  # media group
                            for filename, item in zip(f.mediafilename, result):
                                if isinstance(
                                    item.effective_attachment, tuple
                                ):  # PhotoSize
                                    await cache_media(
                                        filename, item.effective_attachment[0]
                                    )
                                else:
                                    await cache_media(
                                        filename, item.effective_attachment
                                    )
                        else:
                            if isinstance(
                                result.effective_attachment, tuple
                            ):  # PhotoSize
                                await cache_media(
                                    f.mediafilename[0],
                                    result.effective_attachment[0],
                                )
                            else:
                                await cache_media(
                                    f.mediafilename[0], result.effective_attachment
                                )
                        medias = [mediathumb, *media]
                except BadRequest as err:
                    if (
                        "Not enough rights to send" in err.message
                        or "Need administrator rights in the channel chat"
                        in err.message
                    ):
                        await message.chat.leave()
                        logger.warning(
                            f"{err} 第{attempt}次异常->权限不足，"
                            f"无法发送给 @{get_msg_username_or_chatid(message)}"
                        )
                        cleanup_medias(medias)
                        break
                    elif (
                        "Topic_deleted" in err.message
                        or "Topic_closed" in err.message
                        or "Message thread not found" in err.message
                    ):
                        logger.warning(
                            f"{err} 第{attempt}次异常->主题/话题已删除、关闭或早于加入时间，"
                            f"无法发送给 @{get_msg_username_or_chatid(message)}"
                        )
                        cleanup_medias(medias)
                        break
                    else:
                        logger.error(f"{err} 第{attempt}次异常->下载后上传: {f.url}")
                        f.mediaraws = True
                    cleanup_medias(medias)
                    continue
                except RetryAfter as err:
                    await asyncio.sleep(err.retry_after)
                    logger.error(f"{err} 第{attempt}次异常->限流: {f.url}")
                    cleanup_medias(medias)
                    continue
                except NetworkError as err:
                    logger.error(f"{err} 第{attempt}次异常->服务错误: {f.url}")
                except httpx.HTTPError as err:
                    logger.error(f"{err} 第{attempt}次异常->请求异常: {f.url}")
                except Exception as err:
                    logger.exception(err)
                else:
                    try:
                        # for link sharing privacy under group
                        if (
                            len(urls) == 1
                            and not update.channel_post
                            and not message.reply_to_message
                            and message.text is not None
                        ):
                            # Try to delete only if bot have delete permission
                            # and this message is only for sharing
                            match = re.match(BILIBILI_SHARE_URL_REGEX, message.text)
                            if urls[0] == message.text or (
                                match and match.group(0) == message.text
                            ):
                                await message.delete()
                    finally:
                        break
                finally:
                    cleanup_medias(medias)

            # Retry to obtain the link information
            f = (await biliparser(f.url))[0]
    for temp_msg in temp_msgs:
        await temp_msg.delete()


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, urls = message_to_urls(update, context)
    if message is None or not message.text:
        return
    if not urls:
        await message.reply_text("链接不正确")
        return
    no_media = message.text.startswith("/cover")
    logger.info(f"Fetch: {urls}")
    for f in await biliparser(urls):
        if isinstance(f, Exception):
            logger.warning(f"解析错误! {f}")
            await message.reply_text(str(f))
            continue
        async with RedisCache().lock(f.url, timeout=CACHES_TIMER["LOCK"]):
            if f.mediaurls:
                medias = []
                try:
                    medias, mediathumb = await get_media_mediathumb_by_parser(
                        f,
                        compression=False,
                        media_check_ignore=True,
                        no_media=no_media,
                    )
                    mediafilenames = f.mediafilename
                    if mediathumb:
                        medias.insert(0, mediathumb)
                        mediafilenames.insert(0, f.mediathumbfilename)
                    logger.info(f"上传中: {f.url}")
                    if len(medias) > 1:
                        result = await message.reply_media_group(
                            [
                                InputMediaDocument(media, filename=filename)
                                for media, filename in zip(medias, mediafilenames)
                            ],
                        )
                        await message.reply_text(f.caption)
                        for filename, item in zip(mediafilenames, result):
                            if isinstance(
                                item.effective_attachment, tuple
                            ):  # PhotoSize
                                await cache_media(
                                    filename, item.effective_attachment[0]
                                )
                            else:
                                await cache_media(filename, item.effective_attachment)
                    else:
                        result = await message.reply_document(
                            document=medias[0],
                            caption=f.caption,
                            filename=f.mediafilename[0],
                        )
                        await cache_media(
                            f.mediafilename[0], result.effective_attachment
                        )
                finally:
                    cleanup_medias(medias)


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
    message, urls = message_to_urls(update, context)
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
    await application.bot.set_my_commands(
        [
            ["start", "关于本 Bot"],
            ["parse", "获取匹配内容"],
            ["file", "获取匹配内容原始文件"],
            ["cover", "获取匹配内容原始文件预览"],
            ["video", "获取匹配清晰度视频，需参数：/video 720P BV号"],
            ["clear", "清除匹配内容缓存"],
        ]
    )
    bot_me = await application.bot.get_me()
    logger.info(f"Bot @{bot_me.username} started.")


async def post_shutdown(application: Application) -> None:
    await db_close()


def add_handler(application: Application) -> None:
    application.add_handler(CommandHandler("start", start, block=False))
    application.add_handler(CommandHandler("file", fetch, block=False))
    application.add_handler(CommandHandler("cover", fetch, block=False))
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
