"""Ready-to-publish channel posts written by a free LLM (Gemini, with Groq as a fallback).

The «✍️ Сделать пост» button under a news item or a match result calls :class:`PostWriter`.
It downloads the article, asks the first available model for a post in the channel's style
and returns plain text. Providers are tried in order: when one is overloaded, out of its free
quota or unreachable, the next one is used.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol

from .net import FetchError, HttpClient
from .sources import SOURCE_BY_KEY
from .storage import NewsRecord
from .textutil import clean_whitespace, truncate

log = logging.getLogger(__name__)

# An overloaded model is skipped quickly, so the next one still answers within a minute.
LLM_TIMEOUT_SEC = 25
ARTICLE_TIMEOUT_SEC = 15
# A model that failed is skipped for a while, so the next posts do not wait for it again.
COOLDOWN_SEC = 5 * 60
QUOTA_COOLDOWN_SEC = 30 * 60
ARTICLE_LIMIT = 6000
MIN_PARAGRAPH = 40

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(Exception):
    """The model could not produce a post (quota, overload, network, empty answer…)."""

    def __init__(self, message: str, *, quota: bool = False) -> None:
        super().__init__(message)
        self.quota = quota  # the free daily/minute limit is used up


# --------------------------------------------------------------------------- prompts

STYLE = """Ты — редактор русскоязычного футбольного Telegram-канала. Пиши готовые посты.

Как писать:
- Только на русском, даже если источник на другом языке. Имена игроков и названия клубов — в принятом русском написании, клубы в «ёлочках».
- Первая строка — короткий заголовок (до 10–12 слов) ЗАГЛАВНЫМИ БУКВАМИ, в начале одно подходящее эмодзи (🚨 — громкое или скандал, ❗️ — инсайд или важное заявление, 💰 — трансфер, 🤕 — травма, 🔥 — яркое событие, ⚽️ — остальное). Заголовок передаёт главный факт, а не цитату целиком.
- Дальше 1–3 коротких абзаца простым, понятным языком: в чём суть, нужный контекст, почему это важно. Не слишком длинно и не слишком коротко — примерно 250–600 знаков без заголовка.
- Если в тексте сказано, на кого ссылается новость (инсайдер, другое СМИ, клуб, сам игрок), укажи этот первоисточник: «Об этом сообщает …» или начни пост с «❗️Имя инсайдера:». Сайт, с которого взята новость (он указан в поле «Источник»), в посте не упоминай.
- Эмодзи 2–4 на весь пост, не перегибай.
- Только факты из присланного текста. Не добавляй ничего от себя: ни клубов, ни возраста, ни дат, ни подробностей, которых в тексте нет.
- Перед ответом проверь орфографию.
- Без хэштегов, ссылок, подписи канала и разметки (никаких **, __, #).
- Ответ — только текст поста, без пояснений.

Пример 1:
🚨 БОЛЕЛЬЩИК «ОВЬЕДО» ПОЛУЧИЛ УСЛОВНЫЙ СРОК ЗА РАСИЗМ В АДРЕС МБАППЕ

Болельщик испанского «Овьедо» приговорён к 11 месяцам условного лишения свободы за расистские оскорбления Килиана Мбаппе, нападающего мадридского «Реала».

⚖️ Суд вынес соответствующий приговор. Об этом сообщает TA.
Расизм не имеет места в футболе.

Пример 2:
❗️Хави Мигель и Нил Сола:
Жюль Кунде уже тренируется без боли в лодыжке.

Если все пойдет хорошо, ожидается, что он присоединится к тренировкам с командой в четверг и будет готов к матчу против «Вильярреала»."""

MATCH_TASK = """Сделай пост об итоге матча.
Заголовок — ЗАГЛАВНЫМИ БУКВАМИ со счётом. В начале эмодзи: для сборных — флаг страны-победителя (при ничьей — флаг хозяев), для клубов — флаг страны клуба-победителя или эмодзи турнира (🏆 Лига чемпионов, 🟠 Лига Европы, 🟢 Лига конференций). Например:
🇩🇪 ГЕРМАНИЯ РАЗГРОМИЛА НИДЕРЛАНДЫ — 5:0! 🔥
Глагол подбери по счёту и ходу игры (обыграла, разгромила, вырвала победу, сыграли вничью…).
Ниже коротко: кто забил (с минутами), удаления, если были, и чем результат важен, если это понятно из данных. Ничего не выдумывай."""


def news_prompt(record: NewsRecord, article: str) -> str:
    source = SOURCE_BY_KEY.get(record.source)
    parts = [
        "Сделай пост по этой новости.",
        f"Источник: {source.name if source else record.source}",
        f"Заголовок: {record.title}",
    ]
    if record.summary:
        parts.append(f"Анонс: {record.summary}")
    if article:
        parts.append(f"Текст статьи:\n{article}")
    return "\n\n".join(parts)


def match_prompt(result_text: str) -> str:
    return f"{MATCH_TASK}\n\nДанные матча:\n{result_text}"


# ------------------------------------------------------------------ article text

class _ArticleParser(HTMLParser):
    """Collects paragraph texts, skipping navigation, footers, scripts and the like."""

    _SKIP = frozenset({"script", "style", "noscript", "nav", "footer", "header", "aside",
                       "form", "figure", "figcaption", "button", "svg", "iframe"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.in_article = 0
        self.article_paragraphs: list[str] = []
        self._skip = 0
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "article":
            self.in_article += 1
        elif tag == "p" and not self._skip:
            self._flush()
            self._current = []
        elif tag == "br" and self._current is not None:
            self._current.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "article":
            self._flush()
            self.in_article = max(0, self.in_article - 1)
        elif tag == "p":
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._current is not None and not self._skip:
            self._current.append(data)

    def _flush(self) -> None:
        if self._current is None:
            return
        text = clean_whitespace(" ".join("".join(self._current).split()))
        self._current = None
        if len(text) >= MIN_PARAGRAPH:
            self.paragraphs.append(text)
            if self.in_article:
                self.article_paragraphs.append(text)

    def close(self) -> None:
        super().close()
        self._flush()


def extract_article(html: str, limit: int = ARTICLE_LIMIT) -> str:
    """Main text of a news page: its paragraphs, preferably those inside <article>."""
    parser = _ArticleParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is lenient; a broken page is not fatal
        return ""
    paragraphs = parser.article_paragraphs or parser.paragraphs
    text = "\n".join(dict.fromkeys(paragraphs))  # some pages repeat the lead paragraph
    return truncate(text, limit) if text else ""


# --------------------------------------------------------------------- providers

class Provider(Protocol):
    name: str

    async def generate(self, system: str, prompt: str) -> str: ...


def _error_message(data: Any) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:200]
        if isinstance(error, str):
            return error[:200]
    return ""


@dataclass(slots=True)
class GeminiProvider:
    http: HttpClient
    token: str
    model: str

    @property
    def name(self) -> str:
        return f"Gemini ({self.model})"

    async def generate(self, system: str, prompt: str) -> str:
        status, data = await self.http.post_json(
            GEMINI_URL.format(model=self.model),
            {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7},
            },
            headers={"x-goog-api-key": self.token},
            timeout=LLM_TIMEOUT_SEC,
        )
        if status != 200:
            raise LLMError(f"HTTP {status} {_error_message(data)}".strip(), quota=status == 429)
        try:
            candidate = data["candidates"][0]
            parts = candidate.get("content", {}).get("parts") or []
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("пустой ответ") from None
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text.strip():
            raise LLMError(f"пустой ответ ({candidate.get('finishReason', '?')})")
        return text


@dataclass(slots=True)
class GroqProvider:
    http: HttpClient
    token: str
    model: str

    @property
    def name(self) -> str:
        return f"Groq ({self.model})"

    async def generate(self, system: str, prompt: str) -> str:
        status, data = await self.http.post_json(
            GROQ_URL,
            {
                "model": self.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=LLM_TIMEOUT_SEC,
        )
        if status != 200:
            raise LLMError(f"HTTP {status} {_error_message(data)}".strip(), quota=status == 429)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError("пустой ответ") from None
        if not text.strip():
            raise LLMError("пустой ответ")
        return text


# ------------------------------------------------------------------------ writer

_MARKDOWN_RE = re.compile(r"\*\*|__|(?<!\w)\*(?=\S)|(?<=\S)\*(?!\w)|^#+\s*", re.MULTILINE)
_HASHTAG_LINE_RE = re.compile(r"^\s*(#\w+\s*)+$", re.MULTILINE)


def clean_post(text: str) -> str:
    """Strip markdown, hashtag lines and stray wrapping the model sometimes adds."""
    text = text.strip().strip("`").strip()
    text = _HASHTAG_LINE_RE.sub("", text)
    text = _MARKDOWN_RE.sub("", text)
    lines = [line.rstrip() for line in text.replace("\r", "").split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


class PostWriter:
    def __init__(self, http: HttpClient, providers: Sequence[Provider]) -> None:
        if not providers:
            raise ValueError("no LLM providers configured")
        self._http = http
        self._providers = tuple(providers)
        self._resting: dict[str, float] = {}  # provider name → skip it until this time

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    async def _article(self, url: str) -> str:
        try:
            html = await self._http.fetch_text(url, timeout=ARTICLE_TIMEOUT_SEC)
        except FetchError as exc:
            log.info("Не удалось скачать статью %s: %s — пишу по анонсу", url, exc)
            return ""
        return extract_article(html)

    def _order(self) -> list[Provider]:
        """Providers to try: resting ones go last (they are still better than nothing)."""
        now = time.monotonic()
        ready = [p for p in self._providers if self._resting.get(p.name, 0.0) <= now]
        return ready + [p for p in self._providers if p not in ready]

    async def _generate(self, prompt: str) -> str:
        errors: list[str] = []
        for provider in self._order():
            try:
                post = clean_post(await provider.generate(STYLE, prompt))
            except (LLMError, FetchError) as exc:
                log.warning("%s не написал пост: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
                quota = isinstance(exc, LLMError) and exc.quota
                self._resting[provider.name] = time.monotonic() + (
                    QUOTA_COOLDOWN_SEC if quota else COOLDOWN_SEC)
                continue
            if post:
                self._resting.pop(provider.name, None)
                return post
            errors.append(f"{provider.name}: пустой ответ")
        raise LLMError("; ".join(errors))

    async def news_post(self, record: NewsRecord) -> str:
        article = await self._article(record.url)
        return await self._generate(news_prompt(record, article))

    async def match_post(self, result_text: str) -> str:
        return await self._generate(match_prompt(result_text))
