"""News pipeline: fetch RSS → parse → classify → deduplicate → store → deliver."""

from __future__ import annotations

import asyncio
import calendar
import io
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field

import feedparser

from .classifier import Classifier
from .catalog import OTHER
from .net import FetchError, HttpClient
from .settings import BotSettings, SettingsStore
from .sources import SOURCES, Source
from .storage import NewsRecord, NewsStatus, Storage
from .textutil import (
    clean_url, fold, html_to_text, is_http_url, make_id, same_story, similarity,
    strip_feed_junk, title_fingerprint, truncate, url_key,
)

log = logging.getLogger(__name__)

SUMMARY_LIMIT = 900
TITLE_LIMIT = 300
DEDUP_WINDOW_SEC = 24 * 3600
FIRST_RUN_SAMPLE = 3
MAX_DELIVERY_ATTEMPTS = 5
DELIVERY_BATCH = 30
MAX_BACKOFF_SEC = 30 * 60
FETCH_CONCURRENCY = 6

_SEEDED_SOURCES_KEY = "seeded_sources"
_FIRST_RUN_KEY = "first_run_done"


@dataclass(slots=True)
class Candidate:
    id: str
    source: Source
    url: str
    title: str
    summary: str
    tags: tuple[str, ...]
    published_at: int


@dataclass(slots=True)
class SourceHealth:
    failures: int = 0
    last_error: str | None = None
    last_ok: float | None = None
    retry_at: float = 0.0


@dataclass(slots=True)
class CycleReport:
    started_at: float
    duration: float = 0.0
    sources_ok: int = 0
    sources_failed: int = 0
    new: int = 0
    queued: int = 0
    duplicates: int = 0
    filtered: int = 0
    seeded: int = 0
    expired: int = 0
    delivered: int = 0
    records: list[NewsRecord] = field(default_factory=list)


class FeedError(Exception):
    """The response is not a usable RSS/Atom document."""


# ------------------------------------------------------------------------ parsing

def _entry_time(entry: Mapping, now: int) -> int:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            try:
                ts = calendar.timegm(value)
            except (TypeError, ValueError, OverflowError):
                continue
            return min(ts, now)  # some feeds put items slightly in the future
    return now


def _entry_summary(entry: Mapping, title: str) -> str:
    summary = html_to_text(entry.get("summary") or entry.get("description") or "")
    for content in entry.get("content") or ():
        text = html_to_text(content.get("value") or "")
        if len(text) > len(summary) * 1.2:
            summary = text
    summary = strip_feed_junk(summary)
    if summary and fold(summary).startswith(fold(title)):
        summary = summary[len(title):].lstrip(" .:—–-\n")
    return truncate(summary, SUMMARY_LIMIT)


def parse_feed(source: Source, body: bytes, content_type: str, now: int) -> list[Candidate]:
    """Parse a feed document. Runs in a worker thread — must not touch the event loop."""
    headers = {"content-type": content_type} if content_type else None
    parsed = feedparser.parse(io.BytesIO(body), response_headers=headers)
    if not parsed.entries:
        if parsed.get("bozo"):
            raise FeedError(f"не RSS: {type(parsed.get('bozo_exception')).__name__}")
        return []

    candidates: list[Candidate] = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        title = truncate(html_to_text(entry.get("title") or "").replace("\n", " "), TITLE_LIMIT)
        if not title or not is_http_url(link):
            continue
        url = clean_url(link)
        tags = tuple(
            term.strip()
            for term in (tag.get("term") or tag.get("label") or "" for tag in entry.get("tags") or ())
            if term and term.strip()
        )[:12]
        candidates.append(Candidate(
            id=make_id(url_key(url)),
            source=source,
            url=url,
            title=title,
            summary=_entry_summary(entry, title),
            tags=tags,
            published_at=_entry_time(entry, now),
        ))
    return candidates


def find_duplicate(fingerprint: frozenset[str], lang: str,
                   recent: Iterable[tuple[str, str, frozenset[str]]]) -> str | None:
    """Id of an already known story that ``fingerprint`` repeats (the most similar one)."""
    best_id, best_score = None, -1.0
    for news_id, other_lang, other in recent:
        if other_lang != lang or not same_story(fingerprint, other):
            continue
        score = similarity(fingerprint, other)
        if score > best_score:
            best_id, best_score = news_id, score
    return best_id


# ---------------------------------------------------------------------- collector

class NewsCollector:
    def __init__(self, http: HttpClient, db: Storage, settings: SettingsStore,
                 classifier: Classifier, *, max_age_hours: int, interval: int,
                 sources: tuple[Source, ...] = SOURCES) -> None:
        self._http = http
        self._db = db
        self._settings = settings
        self._classifier = classifier
        self._max_age = max_age_hours * 3600
        self._interval = interval
        self._sources = tuple(s for s in sources if s.enabled)
        self._health = {s.key: SourceHealth() for s in self._sources}
        self._semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
        self.last_report: CycleReport | None = None

    @property
    def sources(self) -> tuple[Source, ...]:
        return self._sources

    @property
    def health(self) -> Mapping[str, SourceHealth]:
        return self._health

    async def reset_seeding(self) -> None:
        """Treat every source as new: what is in the feeds right now is not delivered."""
        await self._db.delete_value(_SEEDED_SOURCES_KEY)

    async def forget_sources(self, *, lang: str) -> None:
        """Re-seed the sources of a language that has just been switched on."""
        keys = {s.key for s in self._sources if s.lang == lang}
        seeded = set(await self._db.get_value(_SEEDED_SOURCES_KEY, []))
        if seeded & keys:
            await self._db.set_value(_SEEDED_SOURCES_KEY, sorted(seeded - keys))

    # ---------------------------------------------------------------- fetching

    async def _fetch(self, source: Source) -> list[Candidate] | None:
        health = self._health[source.key]
        try:
            async with self._semaphore:
                response = await self._http.fetch_feed(source.url)
            candidates = [] if response.not_modified else await asyncio.to_thread(
                parse_feed, source, response.body, response.content_type, int(time.time())
            )
        except (FetchError, FeedError) as exc:
            health.failures += 1
            health.last_error = str(exc)
            backoff = min(MAX_BACKOFF_SEC, self._interval * 2 ** (health.failures - 1))
            health.retry_at = time.time() + backoff
            log.warning("Источник %s недоступен (%s), повтор через %d мин",
                        source.name, exc, max(1, backoff // 60))
            return None
        except Exception:
            health.failures += 1
            health.last_error = "внутренняя ошибка"
            health.retry_at = time.time() + self._interval
            log.exception("Ошибка обработки источника %s", source.name)
            return None
        if health.failures:
            log.info("Источник %s снова доступен", source.name)
        health.failures, health.last_error, health.retry_at = 0, None, 0.0
        health.last_ok = time.time()
        return candidates

    # --------------------------------------------------------------- collecting

    async def collect(self, *, seeding: bool = True) -> CycleReport:
        """Fetch all due sources and store new items. ``seeding=False`` is for previews."""
        report = CycleReport(started_at=time.time())
        settings = await self._settings.get()
        now = int(report.started_at)

        due = [s for s in self._sources
               if s.lang in settings.langs and self._health[s.key].retry_at <= now]
        results = await asyncio.gather(*(self._fetch(s) for s in due))

        seeded_sources = set(await self._db.get_value(_SEEDED_SOURCES_KEY, []))
        first_run = seeding and not await self._db.get_value(_FIRST_RUN_KEY, False)
        newly_seeded: set[str] = set()
        unique: dict[str, Candidate] = {}
        for source, candidates in zip(due, results):
            if candidates is None:
                report.sources_failed += 1
                continue
            report.sources_ok += 1
            if seeding and source.key not in seeded_sources:
                newly_seeded.add(source.key)
            for candidate in candidates:
                known = unique.get(candidate.id)
                if known is None or candidate.published_at < known.published_at:
                    unique[candidate.id] = candidate

        existing = await self._db.existing_news_ids(unique)
        fresh = sorted((c for c in unique.values() if c.id not in existing),
                       key=lambda c: (c.published_at, c.id))
        recent = await self._db.recent_fingerprints(since=now - DEDUP_WINDOW_SEC)

        records = [self._evaluate(c, settings, recent, newly_seeded, now) for c in fresh]

        if first_run and newly_seeded:
            self._pick_first_run_sample(records)

        await self._db.add_news(records)
        if newly_seeded:
            await self._db.set_value(_SEEDED_SOURCES_KEY, sorted(seeded_sources | newly_seeded))
            log.info("Источники запомнены без отправки: %s", ", ".join(sorted(newly_seeded)))
        if first_run and newly_seeded:
            await self._db.set_value(_FIRST_RUN_KEY, True)

        report.records = records
        report.new = len(records)
        for record in records:
            if record.status == NewsStatus.PENDING:
                report.queued += 1
            elif record.status == NewsStatus.DUPLICATE:
                report.duplicates += 1
            elif record.status == NewsStatus.FILTERED:
                report.filtered += 1
            elif record.status == NewsStatus.SKIPPED:
                report.seeded += 1
            elif record.status == NewsStatus.EXPIRED:
                report.expired += 1
        report.duration = time.time() - report.started_at
        self.last_report = report
        return report

    @staticmethod
    def _pick_first_run_sample(records: list[NewsRecord]) -> None:
        """Queue a few of the freshest distinct stories so the owner sees the format at once."""
        picked: list[NewsRecord] = []
        for record in sorted(records, key=lambda r: r.published_at, reverse=True):
            if record.status != NewsStatus.SKIPPED:
                continue
            if any(same_story(record.fingerprint, other.fingerprint) for other in picked):
                continue
            picked.append(record)
            if len(picked) == FIRST_RUN_SAMPLE:
                break
        for record in picked:
            record.status = NewsStatus.PENDING

    def _evaluate(self, c: Candidate, settings: BotSettings,
                  recent: list[tuple[str, str, frozenset[str]]],
                  newly_seeded: set[str], now: int) -> NewsRecord:
        result = self._classifier.classify(c.title, f"{c.summary}\n{' · '.join(c.tags)}",
                                           preset=c.source.category)
        categories = tuple(k for k in result.categories if k in settings.categories)
        if not result.categories and OTHER.key in settings.categories and not result.excluded:
            categories = (OTHER.key,)
        relevant = bool(categories) and not result.hard_excluded
        fingerprint = title_fingerprint(c.title)

        dup_of = None
        if c.published_at < now - self._max_age:
            status = NewsStatus.EXPIRED
        elif not relevant:
            status = NewsStatus.FILTERED
        elif dup_of := find_duplicate(fingerprint, c.source.lang, recent):
            status = NewsStatus.DUPLICATE
        elif c.source.key in newly_seeded:
            status = NewsStatus.SKIPPED
        else:
            status = NewsStatus.PENDING

        if relevant and status in (NewsStatus.PENDING, NewsStatus.SKIPPED, NewsStatus.DUPLICATE):
            recent.append((c.id, c.source.lang, fingerprint))

        return NewsRecord(
            id=c.id, source=c.source.key, lang=c.source.lang, url=c.url, title=c.title,
            summary=c.summary, published_at=c.published_at, fetched_at=now, status=status,
            categories=categories or result.categories, entities=result.entities[:6],
            fingerprint=fingerprint, relevant=relevant, dup_of=dup_of,
        )

    # --------------------------------------------------------------- delivery

    async def deliver_pending(
        self, send: Callable[[NewsRecord], Awaitable[bool]], *,
        is_paused: Callable[[], Awaitable[bool]],
    ) -> int:
        """Send queued items through ``send``. Returns how many were delivered.

        ``send`` returns False when Telegram permanently rejected the message and raises
        when Telegram is unreachable (delivery then stops until the next cycle).
        """
        now = int(time.time())
        expired = await self._db.expire_pending(published_before=now - self._max_age - 3600)
        if expired:
            log.info("Устарели и не будут отправлены: %d", expired)
        delivered = 0
        for record in await self._db.pending_news(DELIVERY_BATCH):
            if await is_paused():
                break
            if await send(record):
                await self._db.mark_news_sent(record.id, int(time.time()))
                delivered += 1
            else:
                await self._db.register_news_failure(record.id, MAX_DELIVERY_ATTEMPTS)
        return delivered
