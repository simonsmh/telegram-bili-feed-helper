import asyncio
import os
import re
from functools import cached_property

import orjson
from bs4 import BeautifulSoup
from bs4.element import Tag
from telegraph.aio import Telegraph

from ..cache import CACHES_TIMER, RedisCache
from ..utils import ParserException, escape_markdown, logger, referer_url
from .feed import Feed

telegraph = Telegraph(access_token=os.environ.get("TELEGRAPH_ACCESS_TOKEN", None))


class Read(Feed):
    rawcontent: str = ""
    read_id: int = 0
    reply_type: int = 12

    @cached_property
    def url(self):
        return f"https://www.bilibili.com/read/cv{self.read_id}"
    
    @property
    def cache_key(self):
        return {
            "read:page": f"read:page:{self.read_id}",
            "read:graphurl": f"read:graphurl:{self.read_id}",
        }

    async def __relink(self, img):
        src = img.attrs.pop("data-src")
        img.attrs = {"src": src if "hdslb" not in src else referer_url(src, self.url)}

    async def handle(self):
        logger.info(f"处理文章信息: 链接: {self.rawurl}")
        match = re.search(
            r"bilibili\.com\/read\/(?:cv|mobile\/|mobile\?id=)(\d+)", self.rawurl
        )
        if not match:
            raise ParserException("文章链接错误", self.rawurl)
        self.read_id = int(match.group(1))
        # 获取文章
        # 1.获取缓存
        try:
            cache_base = await RedisCache().get(self.cache_key["read:page"])
        except Exception as e:
            logger.exception(f"拉取文章页面缓存错误: {e}")
            cache_base = None
        # 2.拉取文章
        if cache_base:
            logger.info(f"拉取文章页面缓存: {self.read_id}")
            cv_content = orjson.loads(cache_base)  # type: ignore
        else:
            try:
                r = await self.client.get(self.rawurl)
            except Exception as e:
                raise ParserException(
                    f"文章页面获取错误:{self.read_id}", self.rawurl, e
                )
                # 3.解析文章
            cv_init = re.search(
                r"window\.__INITIAL_STATE__=(.*?);\(function\(\)", r.text
            )
            if not cv_init:
                raise ParserException(
                    f"文章页面内容获取错误:{self.read_id}", self.rawurl, cv_init
                )
            cv_content = orjson.loads(cv_init.group(1))
        self.uid = cv_content.get("readInfo").get("author").get("mid")
        self.user = cv_content.get("readInfo").get("author").get("name")
        self.content = cv_content.get("readInfo").get("summary")
        mediaurls = (
            cv_content.get("readInfo").get("banner_url")
            if cv_content.get("readInfo").get("banner_url")
            else cv_content.get("readInfo").get("image_urls")
        )
        if mediaurls:
            logger.info(f"文章mediaurls: {mediaurls}")
            self.mediaurls = mediaurls
            self.mediatype = "image"
        title = cv_content.get("readInfo").get("title")
        if not cache_base:
            # 4.缓存文章
            try:
                cache_base = await RedisCache().set(
                    self.cache_key["read:page"],
                    orjson.dumps(cv_content),
                    ex=CACHES_TIMER["READ"],
                    nx=True,
                )
            except Exception as e:
                logger.exception(f"缓存文章页面错误: {e}")
        # 转存文章
        # 1.获取缓存
        try:
            cache_graphurl = await RedisCache().get(self.cache_key["read:graphurl"])
        except Exception as e:
            logger.exception(f"拉取文章链接缓存错误: {e}")
            cache_graphurl = None
        # 2.拉取文章
        if cache_graphurl:
            logger.info(f"拉取文章链接缓存: {self.read_id}")
            graphurl = cache_graphurl
        else:
            # 3.解析文章转为链接
            article_content = cv_content.get("readInfo").get("content")
            if not telegraph.get_access_token():
                logger.info("creating_account")
                result = await telegraph.create_account(
                    "bilifeedbot", "bilifeedbot", "https://t.me/bilifeedbot"
                )
                logger.info(f"Telegraph create_account: {result}")
            try:
                article = orjson.loads(article_content)
                result = article.get("ops")[0].get("insert").split("\n")
                logger.info(result)
                graphurl = (
                    await telegraph.create_page(
                        title=title,
                        content=result,
                        author_name=self.user,
                        author_url=f"https://space.bilibili.com/{self.uid}",
                    )
                ).get("url")
            except orjson.JSONDecodeError:
                article = BeautifulSoup(article_content, "lxml")
                if not isinstance(article, Tag):
                    raise ParserException("文章内容解析错误", self.rawurl, cv_content)
                imgs = article.find_all("img")
                task = list(self.__relink(img) for img in imgs)  ## data-src -> src
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
                        author_name=self.user,
                        author_url=f"https://space.bilibili.com/{self.uid}",
                    )
                ).get("url")
            logger.info(f"生成页面: {graphurl}")
            # 4.缓存文章
            try:
                await RedisCache().set(
                    self.cache_key["read:graphurl"],
                    orjson.dumps(graphurl),
                    ex=CACHES_TIMER["READ"],
                    nx=True,
                )
            except Exception as e:
                logger.exception(f"缓存文章链接错误: {e}")
        self.extra_markdown = f"[{escape_markdown(title)}]({graphurl})"
        self.replycontent = await self.parse_reply(self.read_id, self.reply_type)
        return self
