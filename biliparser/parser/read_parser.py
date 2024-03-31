import asyncio
import json
import os
import re
from io import BytesIO

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from telegraph.aio import Telegraph
from tortoise import timezone
from tortoise.expressions import Q

from ..database import (
    read_cache,
)
from ..model import Read
from ..utils import (
    ParserException,
    compress,
    escape_markdown,
    logger,
    retry_catcher,
)
from .reply_parser import parse_reply

telegraph = Telegraph(access_token=os.environ.get("TELEGRAPH_ACCESS_TOKEN", None))


@retry_catcher
async def parse_read(client: httpx.AsyncClient, url: str):
    async def relink(img):
        src = img.attrs.pop("data-src")
        img.attrs = {"src": src}
        logger.info(f"下载图片: {src}")
        async with client.stream("GET", f"https:{src}") as response:
            if response.status_code != 200:
                logger.error(f"图片获取错误: {src}")
                return
            media = BytesIO(await response.aread())
            mediatype = response.headers.get("content-type")
            if mediatype in ["image/jpeg", "image/png"]:
                content_length = int(response.headers.get("content-length"))
                logger.info(f"图片大小: {content_length} 压缩: {src} {mediatype}")
                if content_length > 1024 * 1024 * 5:
                    media = compress(media, fix_ratio=True)
                else:
                    media = compress(media, size=0, fix_ratio=True)
            try:
                resp = await telegraph.upload_file(media)
                logger.info(f"图片上传: {resp}")
                img.attrs["src"] = f"https://telegra.ph{resp[0].get('src')}"
            except Exception as e:
                logger.exception(f"图片上传错误: {e}")

    logger.info(f"处理文章信息: 链接: {url}")
    match = re.search(r"bilibili\.com\/read\/(?:cv|mobile\/|mobile\?id=)(\d+)", url)
    if not match:
        raise ParserException("文章链接错误", url)
    f = Read(url)
    f.read_id = int(match.group(1))
    r = await client.get(f"https://www.bilibili.com/read/cv{f.read_id}")
    cv_init = re.search(r"window\.__INITIAL_STATE__=(.*?);\(function\(\)", r.text)
    if not cv_init:
        raise ParserException("文章内容获取错误", url, cv_init)
    cv_content = json.loads(cv_init.group(1))
    f.uid = cv_content.get("readInfo").get("author").get("mid")
    f.user = cv_content.get("readInfo").get("author").get("name")
    f.content = cv_content.get("readInfo").get("summary")
    mediaurls = (
        cv_content.get("readInfo").get("banner_url")
        if cv_content.get("readInfo").get("banner_url")
        else cv_content.get("readInfo").get("image_urls")
    )
    if mediaurls:
        logger.info(f"文章mediaurls: {mediaurls}")
        f.mediaurls = mediaurls
        f.mediatype = "image"
    title = cv_content.get("readInfo").get("title")
    logger.info(f"文章ID: {f.read_id}")
    query = Q(read_id=f.read_id)
    cache = await read_cache.get_or_none(
        query,
        created__gte=timezone.now() - read_cache.timeout,
    )
    if cache:
        logger.info(f"拉取文章缓存: {cache.created}")
        graphurl = cache.graphurl
    else:
        article_content = cv_content.get("readInfo").get("content")
        if not telegraph.get_access_token():
            logger.info("creating_account")
            result = await telegraph.create_account(
                "bilifeedbot", "bilifeedbot", "https://t.me/bilifeedbot"
            )
            logger.info(f"Telegraph create_account: {result}")
        try:
            article = json.loads(article_content)
            result = article.get("ops")[0].get("insert").split("\n")
            logger.info(result)
            graphurl = (
                await telegraph.create_page(
                    title=title,
                    content=result,
                    author_name=f.user,
                    author_url=f"https://space.bilibili.com/{f.uid}",
                )
            ).get("url")
        except json.decoder.JSONDecodeError:
            article = BeautifulSoup(article_content, "lxml")
            if not isinstance(article, Tag):
                ParserException("文章内容解析错误", url, cv_init)
            imgs = article.find_all("img")
            task = list(relink(img) for img in imgs)  ## data-src -> src
            for _ in article.find_all("h1"):  ## h1 -> h3
                _.name = "h3"
            for item in ["span", "div"]:  ## remove tags
                for _ in article.find_all(item):
                    _.unwrap()
            for item in ["p", "figure", "figcaption"]:  ## clean tags
                for _ in article.find_all(item):
                    _.attrs = {}
            await asyncio.gather(*task)
            result = ""
            if isinstance(article.body, Tag):
                result = "".join(
                    [str(i) for i in article.body.contents]
                )  ## convert tags to string
            graphurl = (
                await telegraph.create_page(
                    title=title,
                    html_content=result,
                    author_name=f.user,
                    author_url=f"https://space.bilibili.com/{f.uid}",
                )
            ).get("url")
        logger.info(f"生成页面: {graphurl}")
        logger.info(f"文章缓存: {f.read_id}")
        cache = await read_cache.get_or_none(query)
        try:
            if cache:
                cache.graphurl = graphurl
                await cache.save(update_fields=["graphurl", "created"])
            else:
                await read_cache(read_id=f.read_id, graphurl=graphurl).save()
        except Exception as e:
            logger.exception(f"文章缓存失败: {e}")
    f.extra_markdown = f"[{escape_markdown(title)}]({graphurl})"
    f.replycontent = await parse_reply(client, f.read_id, f.reply_type)
    return f
