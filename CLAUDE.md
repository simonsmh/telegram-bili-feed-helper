# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Always use `uv` to run commands:

```bash
uv run pytest -v                          # all tests
uv run pytest test/test_model.py -x -s    # single test file
uv run pytest test/test_integration_bv.py::TestBilibiliProviderParse::test_parse_bare_bv -x -s  # single test
uv run ruff check .                       # lint
uv run ruff format --check .              # format check
uv run ruff check --fix .                 # auto-fix lint
uv run ruff format .                      # auto-format
```

## Architecture

Three-layer design: Channel → Provider → Model.

- **Model** (`biliparser/model.py`): Shared data types — `MediaConstraints`, `ParsedContent`, `PreparedMedia`, `Author`, `Comment`, `MediaInfo`. All layers depend on these; models depend on nothing else.
- **Provider** (`biliparser/provider/`): Fetches and parses content from external platforms. `ProviderRegistry` routes URLs to the right provider. `BilibiliProvider` dispatches to strategy classes in `provider/bilibili/` — `Video`, `Audio`, `Live`, `Opus`, `Read` — all inheriting from `Feed`.
- **Channel** (`biliparser/channel/`): Delivers parsed content to users. `TelegramChannel` declares its `MediaConstraints` and handles formatting/sending. Channels depend on providers via the registry; providers never import channel code.

Data flow: URL → `ProviderRegistry.parse()` → strategy `.handle(constraints)` → `Feed` → `_feed_to_parsed_content()` → `ParsedContent` → Channel formats and sends.

Architectural constraint enforced by `test_architecture.py`: providers must not import from channels, and models must not import business logic.

## Storage

- **Tortoise ORM** with PostgreSQL (prod) or SQLite (default) for `TelegramFileCache` (media file_id caching).
- **Redis** for API response caching with TTLs. Falls back to `FakeRedis` (JSON file-based) when `REDIS_URL` is unset.

## Key env vars

`TOKEN` (Telegram bot token, required), `DATABASE_URL`, `REDIS_URL`, `LOCAL_MODE`, `FFMPEG_PATH`, `HTTP_PROXY`. Bilibili cookies: `SESSDATA`, `BILI_JCT`, `BUVID3`, `BUVID4`, `AC_TIME_VALUE`. Full list in `stack.env`.

## Style

- Ruff for both linting and formatting (line length 120, target py310)
- Chinese comments and docstrings are normal in this codebase (RUF001/002/003 suppressed)
- `asyncio_mode = "auto"` in pytest — no need for `@pytest.mark.asyncio`
