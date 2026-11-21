"""Configuration loaded from environment variables / the .env file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(Exception):
    """A required setting is missing or has an invalid value."""


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    tz: tzinfo
    tz_name: str
    db_path: Path
    log_dir: Path
    log_level: str
    news_interval: int
    matches_interval: int
    max_news_age_hours: int
    digest_time: time
    request_timeout: int
    telegram_proxy: str | None = None
    telegram_api_url: str | None = None
    gemini_token: str | None = None
    gemini_models: tuple[str, ...] = ()
    groq_token: str | None = None
    groq_models: tuple[str, ...] = ()
    post_signature: str = ""
    card_brand: str = ""
    channel_id: int | str | None = None  # «@username» or «-100…»


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} должно быть целым числом, сейчас: {raw!r}") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} должно быть от {minimum} до {maximum}, сейчас: {value}")
    return value


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in re.split(r"[,;\s]+", raw):
        if not part:
            continue
        if not re.fullmatch(r"\d{1,20}", part):
            raise ConfigError(
                f"ADMIN_IDS: «{part}» не похоже на Telegram ID — нужны числа через запятую"
            )
        ids.add(int(part))
    return frozenset(ids)


def _parse_timezone(raw: str) -> tuple[tzinfo, str]:
    if raw:
        try:
            return ZoneInfo(raw), raw
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(
                f"TIMEZONE: неизвестный часовой пояс {raw!r}. Пример: Europe/Moscow, Asia/Almaty"
            ) from None
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    name = f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    return timezone(offset, name), name


def _parse_clock(name: str, raw: str, default: time) -> time:
    if not raw:
        return default
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ConfigError(f"{name} должно быть временем в формате ЧЧ:ММ, сейчас: {raw!r}")
    return time(int(match[1]), int(match[2]))


_PROXY_RE = re.compile(r"(?:http|https|socks4|socks5)://[^\s/]+(?::\d+)?/?", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def _optional_url(name: str, pattern: re.Pattern[str], example: str) -> str | None:
    raw = _env(name)
    if not raw:
        return None
    if not pattern.fullmatch(raw):
        raise ConfigError(f"{name} имеет неверный формат. Пример: {example}")
    return raw.rstrip("/")


# Tried in this order; the lighter model answers when the main one is overloaded.
# Free tier (Nov 2026): the Flash models allow ~20 requests a day each, the Flash Lite ones
# ~500, so Flash writes the first posts of the day and Lite takes over when it runs out.
# gemini-flash-latest is the newest and most overloaded one, hence near the end. Named
# versions may be retired by Google: a missing model is simply skipped.
DEFAULT_GEMINI_MODELS = ("gemini-3-flash-preview", "gemini-3.6-flash", "gemini-3.1-flash-lite",
                         "gemini-flash-lite-latest", "gemini-flash-latest")
DEFAULT_GROQ_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b")


def _list_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    items = tuple(part for part in re.split(r"[,;\s]+", _env(name)) if part)
    return items or default


def _parse_channel(raw: str) -> int | str | None:
    """«@futera», «futera», «https://t.me/futera» → "@futera"; «-1001234567890» → the int."""
    if not raw:
        return None
    if re.fullmatch(r"-100\d{5,}", raw):
        return int(raw)
    match = re.fullmatch(r"(?:https?://)?(?:t\.me/|telegram\.me/)?@?([A-Za-z][A-Za-z0-9_]{3,31})/?",
                         raw)
    if not match:
        raise ConfigError("CHANNEL_ID: укажи @username канала или его ID вида -1001234567890 "
                          "(ID пришлёт бот, если переслать ему любой пост из канала)")
    return "@" + match.group(1)


def _path_env(name: str, default: str) -> Path:
    path = Path(_env(name) or default)
    return path if path.is_absolute() else BASE_DIR / path


def load_config(*, require_token: bool = True) -> Config:
    load_dotenv(BASE_DIR / ".env", override=False, encoding="utf-8-sig")

    token = _env("BOT_TOKEN") or _env("BOT_API")
    if require_token:
        if not token:
            raise ConfigError(
                "не найден токен бота. Добавьте в файл .env строку BOT_TOKEN=<токен от @BotFather>"
            )
        if not _TOKEN_RE.fullmatch(token):
            raise ConfigError(
                "токен бота имеет неверный формат. Скопируйте его из @BotFather целиком, "
                "без пробелов и кавычек"
            )

    log_level = (_env("LOG_LEVEL") or "INFO").upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL должен быть одним из: {', '.join(_LOG_LEVELS)}")

    tz, tz_name = _parse_timezone(_env("TIMEZONE"))
    return Config(
        bot_token=token,
        admin_ids=_parse_admin_ids(_env("ADMIN_IDS")),
        tz=tz,
        tz_name=tz_name,
        db_path=_path_env("DB_PATH", "data/bot.sqlite3"),
        log_dir=_path_env("LOG_DIR", "logs"),
        log_level=log_level,
        news_interval=_int_env("NEWS_INTERVAL_SEC", 180, 60, 3600),
        matches_interval=_int_env("MATCHES_INTERVAL_SEC", 300, 60, 3600),
        max_news_age_hours=_int_env("MAX_NEWS_AGE_HOURS", 3, 1, 48),
        digest_time=_parse_clock("DIGEST_TIME", _env("DIGEST_TIME"), time(9, 0)),
        request_timeout=_int_env("REQUEST_TIMEOUT_SEC", 20, 5, 120),
        telegram_proxy=_optional_url("TELEGRAM_PROXY", _PROXY_RE,
                                     "socks5://user:password@1.2.3.4:1080 или http://127.0.0.1:8080"),
        telegram_api_url=_optional_url("TELEGRAM_API_URL", _URL_RE, "https://my-bot-api.example.com"),
        gemini_token=_env("GEMINI_TOKEN") or _env("GEMINI_API_KEY") or _env("GEMINI_API") or None,
        gemini_models=_list_env("GEMINI_MODELS", DEFAULT_GEMINI_MODELS),
        groq_token=_env("GROQ_TOKEN") or _env("GROQ_API_KEY") or _env("GROQ_API") or None,
        groq_models=_list_env("GROQ_MODELS", DEFAULT_GROQ_MODELS),
        post_signature=_env("POST_SIGNATURE").replace("\\n", "\n"),
        card_brand=_env("CARD_BRAND"),
        channel_id=_parse_channel(_env("CHANNEL_ID")),
    )
