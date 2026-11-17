"""SQLite storage: seen news, notified matches and small key/value settings."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    lang         TEXT NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    categories   TEXT NOT NULL DEFAULT '',
    entities     TEXT NOT NULL DEFAULT '',
    fingerprint  TEXT NOT NULL DEFAULT '',
    relevant     INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,
    dup_of       TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    published_at INTEGER NOT NULL,
    fetched_at   INTEGER NOT NULL,
    sent_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_news_status    ON news(status, published_at);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at);
CREATE INDEX IF NOT EXISTS idx_news_fetched   ON news(fetched_at);
CREATE INDEX IF NOT EXISTS idx_news_sent      ON news(sent_at);

CREATE TABLE IF NOT EXISTS matches (
    event_id   TEXT PRIMARY KEY,
    league     TEXT NOT NULL,
    kickoff    INTEGER NOT NULL,
    status     TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matches_created ON matches(created_at);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SQLITE_MAX_PARAMS = 900


class NewsStatus:
    PENDING = "pending"  # waiting to be delivered
    SENT = "sent"
    SKIPPED = "skipped"  # relevant, but seen while (re)seeding a source — not delivered
    FILTERED = "filtered"  # not about the tracked tournaments
    DUPLICATE = "duplicate"  # same story already came from another outlet
    EXPIRED = "expired"  # too old by the time it was seen
    FAILED = "failed"  # Telegram rejected it repeatedly


class MatchStatus:
    SENT = "sent"
    SEEDED = "seeded"
    FAILED = "failed"


@dataclass(slots=True)
class NewsRecord:
    id: str
    source: str
    lang: str
    url: str
    title: str
    summary: str
    published_at: int
    fetched_at: int
    status: str
    categories: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    fingerprint: frozenset[str] = field(default_factory=frozenset)
    relevant: bool = False
    dup_of: str | None = None
    attempts: int = 0
    sent_at: int | None = None

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.id, self.source, self.lang, self.url, self.title, self.summary,
            ",".join(self.categories), "|".join(self.entities), " ".join(sorted(self.fingerprint)),
            int(self.relevant), self.status, self.dup_of, self.attempts,
            self.published_at, self.fetched_at, self.sent_at,
        )

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> NewsRecord:
        return cls(
            id=row["id"],
            source=row["source"],
            lang=row["lang"],
            url=row["url"],
            title=row["title"],
            summary=row["summary"],
            published_at=row["published_at"],
            fetched_at=row["fetched_at"],
            status=row["status"],
            categories=tuple(filter(None, row["categories"].split(","))),
            entities=tuple(filter(None, row["entities"].split("|"))),
            fingerprint=frozenset(row["fingerprint"].split()),
            relevant=bool(row["relevant"]),
            dup_of=row["dup_of"],
            attempts=row["attempts"],
            sent_at=row["sent_at"],
        )


_NEWS_COLUMNS = (
    "id, source, lang, url, title, summary, categories, entities, fingerprint, relevant, "
    "status, dup_of, attempts, published_at, fetched_at, sent_at"
)


def _chunks(items: Sequence[str], size: int = _SQLITE_MAX_PARAMS) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


class Storage:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._db = conn

    @classmethod
    async def open(cls, path: Path | str) -> Storage:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.executescript(_SCHEMA)
        await conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self._db.close()

    async def _write(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run one write statement, commit it and return the number of affected rows."""
        cursor = await self._db.execute(sql, params)
        try:
            return cursor.rowcount
        finally:
            await cursor.close()
            await self._db.commit()

    # ------------------------------------------------------------------ key/value

    async def get_value(self, key: str, default: Any = None) -> Any:
        rows = await self._db.execute_fetchall("SELECT value FROM kv WHERE key = ?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except json.JSONDecodeError:
            return default

    async def set_value(self, key: str, value: Any) -> None:
        await self._write(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    async def delete_value(self, key: str) -> None:
        await self._write("DELETE FROM kv WHERE key = ?", (key,))

    # ----------------------------------------------------------------------- news

    async def existing_news_ids(self, ids: Iterable[str]) -> set[str]:
        unique = list(dict.fromkeys(ids))
        found: set[str] = set()
        for chunk in _chunks(unique):
            marks = ",".join("?" * len(chunk))
            rows = await self._db.execute_fetchall(
                f"SELECT id FROM news WHERE id IN ({marks})", tuple(chunk)
            )
            found.update(row["id"] for row in rows)
        return found

    async def add_news(self, records: Sequence[NewsRecord]) -> None:
        if not records:
            return
        marks = ",".join("?" * 16)
        await self._db.executemany(
            f"INSERT OR IGNORE INTO news({_NEWS_COLUMNS}) VALUES({marks})",
            [record.to_row() for record in records],
        )
        await self._db.commit()

    async def get_news(self, news_id: str) -> NewsRecord | None:
        rows = await self._db.execute_fetchall(
            f"SELECT {_NEWS_COLUMNS} FROM news WHERE id = ?", (news_id,)
        )
        return NewsRecord.from_row(rows[0]) if rows else None

    async def recent_fingerprints(self, since: int) -> list[tuple[str, str, frozenset[str]]]:
        rows = await self._db.execute_fetchall(
            "SELECT id, lang, fingerprint FROM news "
            "WHERE relevant = 1 AND published_at >= ? AND status IN (?, ?, ?, ?)",
            (since, NewsStatus.PENDING, NewsStatus.SENT, NewsStatus.SKIPPED, NewsStatus.DUPLICATE),
        )
        return [(row["id"], row["lang"], frozenset(row["fingerprint"].split())) for row in rows]

    async def pending_news(self, limit: int) -> list[NewsRecord]:
        rows = await self._db.execute_fetchall(
            f"SELECT {_NEWS_COLUMNS} FROM news WHERE status = ? "
            "ORDER BY published_at ASC, fetched_at ASC LIMIT ?",
            (NewsStatus.PENDING, limit),
        )
        return [NewsRecord.from_row(row) for row in rows]

    async def mark_news_sent(self, news_id: str, sent_at: int) -> None:
        await self._write(
            "UPDATE news SET status = ?, sent_at = ? WHERE id = ?",
            (NewsStatus.SENT, sent_at, news_id),
        )

    async def register_news_failure(self, news_id: str, max_attempts: int) -> None:
        await self._write(
            "UPDATE news SET attempts = attempts + 1, "
            "status = CASE WHEN attempts + 1 >= ? THEN ? ELSE status END WHERE id = ?",
            (max_attempts, NewsStatus.FAILED, news_id),
        )

    async def expire_pending(self, published_before: int) -> int:
        return await self._write(
            "UPDATE news SET status = ? WHERE status = ? AND published_at < ?",
            (NewsStatus.EXPIRED, NewsStatus.PENDING, published_before),
        )

    async def latest_news(self, limit: int, categories: set[str]) -> list[NewsRecord]:
        """Most recent relevant stories (newest first) in the given categories."""
        rows = await self._db.execute_fetchall(
            f"SELECT {_NEWS_COLUMNS} FROM news WHERE relevant = 1 AND status IN (?, ?, ?) "
            "ORDER BY published_at DESC LIMIT 300",
            (NewsStatus.SENT, NewsStatus.PENDING, NewsStatus.SKIPPED),
        )
        result: list[NewsRecord] = []
        for row in rows:
            record = NewsRecord.from_row(row)
            if categories.intersection(record.categories):
                result.append(record)
                if len(result) >= limit:
                    break
        return result

    async def count_news(self, *, status: str, since: int | None = None) -> int:
        if since is None:
            rows = await self._db.execute_fetchall(
                "SELECT COUNT(*) AS n FROM news WHERE status = ?", (status,)
            )
        else:
            column = "sent_at" if status == NewsStatus.SENT else "fetched_at"
            rows = await self._db.execute_fetchall(
                f"SELECT COUNT(*) AS n FROM news WHERE status = ? AND {column} >= ?",
                (status, since),
            )
        return int(rows[0]["n"])

    # -------------------------------------------------------------------- matches

    async def known_matches(self, event_ids: Iterable[str]) -> set[str]:
        unique = list(dict.fromkeys(event_ids))
        found: set[str] = set()
        for chunk in _chunks(unique):
            marks = ",".join("?" * len(chunk))
            rows = await self._db.execute_fetchall(
                f"SELECT event_id FROM matches WHERE event_id IN ({marks})", tuple(chunk)
            )
            found.update(row["event_id"] for row in rows)
        return found

    async def add_match(self, event_id: str, league: str, kickoff: int, status: str,
                        created_at: int) -> None:
        await self._write(
            "INSERT OR IGNORE INTO matches(event_id, league, kickoff, status, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (event_id, league, kickoff, status, created_at),
        )

    async def count_matches_sent(self, since: int) -> int:
        rows = await self._db.execute_fetchall(
            "SELECT COUNT(*) AS n FROM matches WHERE status = ? AND created_at >= ?",
            (MatchStatus.SENT, since),
        )
        return int(rows[0]["n"])

    # ---------------------------------------------------------------- maintenance

    async def prune(self, news_before: int, matches_before: int) -> tuple[int, int]:
        news = await self._write("DELETE FROM news WHERE fetched_at < ?", (news_before,))
        matches = await self._write("DELETE FROM matches WHERE created_at < ?", (matches_before,))
        return news, matches
