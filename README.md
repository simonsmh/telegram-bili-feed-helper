# Telegram-Bili-Feed-Helper
[![Codacy Badge](https://api.codacy.com/project/badge/Grade/ee65189aead04bfda4aa6ac79f798628)](https://www.codacy.com/manual/simonsmh/telegram-bili-feed-helper?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=simonsmh/telegram-bili-feed-helper&amp;utm_campaign=Badge_Grade)
[![Require: Python 3.7](https://img.shields.io/badge/Python-3.7-blue)](https://www.python.org/)
[![Require: python-telegram-bot >= 20](https://img.shields.io/badge/python--telegram--bot-%3E%3D%2020-blue)](https://github.com/python-telegram-bot/python-telegram-bot)

Telegram bot for Bili Feed Helper.

[![Demo Bot](https://img.shields.io/badge/Demo-Bot-green)](https://t.me/bilifeedbot)

## License

[![License: GPL v3](https://img.shields.io/badge/License-GPL%20v3-blue)](https://www.gnu.org/licenses/gpl-3.0)

## Env Guide

### Official bot api
- Required: `TOKEN`: Telegram bot token
- Recommend: `REDIS_URL`: redis url string like `rediss://usr:pswd@host:port` supported by redis-py
- Supported: 
  - Listening on `HOST`:`PORT`
  - `DATABASE_URL`: file cache db url string supported by tortoise orm
  - `FILE_TABLE`: file cache db name
  - `VIDEO_CODEC`: Video codec in `avc`/`hev`/`av01`

### Self hosted bot api
- See Official bot api
- Extra:
  - `LOCAL_MODE`: Set 1 to enable
  - `API_BASE_URL`: Self hosted bot api endpoint like `http://127.0.0.1:8081/bot`
  - `API_BASE_FILE_URL`: Self hosted bot api file endpoint like `http://127.0.0.1:8081/file/bot`
  - `LOCAL_TEMP_FILE_PATH`: Set to docker mounting point like `/var/lib/telegram-bot-api/.tmp/`
  - `VIDEO_SIZE_LIMIT`: Max video file size in bytes `2e9` by default under local mode


## Credit

[Bilibili API](https://github.com/Nemo2011/bilibili-api): 
- https://api.bilibili.com/x/polymer/web-dynamic/desktop/v1/detail
- https://api.bilibili.com/audio/music-service-c/songs/playing
- https://api.bilibili.com/audio/music-service-c/url
- https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom
- https://api.bilibili.com/x/web-interface/view
- https://api.bilibili.com/x/player/playurl
- https://api.bilibili.com/x/v2/reply/wbi/main
- https://api.bilibili.com/pgc/view/web/season
