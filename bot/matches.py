"""Finished-match results and the daily fixtures preview."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time as dtime, timedelta, timezone, tzinfo

from .catalog import CATEGORIES, Category
from .espn import EspnClient, Match
from .net import FetchError
from .settings import BotSettings, SettingsStore
from .storage import MatchStatus, Storage

log = logging.getLogger(__name__)

IDLE_POLL_SEC = 30 * 60
RESULT_MAX_AGE_SEC = 36 * 3600
DIGEST_WINDOW = timedelta(hours=3)
OVERVIEW_CACHE_SEC = 120

_SEEDED_LEAGUES_KEY = "seeded_leagues"
_DIGEST_KEY = "digest_sent_on"

SendFn = Callable[[str], Awaitable[bool]]
ResultSendFn = Callable[[Match, Category], Awaitable[bool]]


def tracked_leagues(settings: BotSettings) -> list[Category]:
    return [c for c in CATEGORIES if c.espn_code and c.key in settings.categories]


class MatchService:
    def __init__(self, espn: EspnClient, db: Storage, settings: SettingsStore, *,
                 tz: tzinfo, interval: int, digest_time: dtime) -> None:
        self._espn = espn
        self._db = db
        self._settings = settings
        self._tz = tz
        self._interval = interval
        self._digest_time = digest_time
        self._next_poll: dict[str, float] = {}
        self._cache: dict[tuple[str, date], tuple[float, list[Match]]] = {}
        self.errors: dict[str, str] = {}

    async def reset_seeding(self) -> None:
        await self._db.delete_value(_SEEDED_LEAGUES_KEY)

    async def forget_league(self, key: str) -> None:
        """Re-seed a league that has just been switched on (no burst of old results)."""
        seeded = set(await self._db.get_value(_SEEDED_LEAGUES_KEY, []))
        if key in seeded:
            seeded.discard(key)
            await self._db.set_value(_SEEDED_LEAGUES_KEY, sorted(seeded))
        self._next_poll.pop(key, None)

    # ------------------------------------------------------------------ results

    def _next_delay(self, matches: list[Match], now: float) -> float:
        for match in matches:
            kickoff = match.kickoff.timestamp()
            if match.is_live:
                return self._interval
            if match.state == "pre" and now - 3 * 3600 <= kickoff <= now + IDLE_POLL_SEC:
                return self._interval
            if match.state == "post" and not match.completed and kickoff >= now - 4 * 3600:
                return self._interval  # just ended, final status not published yet
        return IDLE_POLL_SEC

    async def poll_results(self, send: ResultSendFn, *, force: bool = False) -> int:
        """Send results of matches that finished since the last poll.

        ``send(match, league)`` delivers one result (text, card…) and returns whether it
        reached anyone."""
        settings = await self._settings.get()
        if not settings.results or settings.paused:
            return 0
        now = time.time()
        due = [lg for lg in tracked_leagues(settings)
               if force or self._next_poll.get(lg.key, 0.0) <= now]
        if not due:
            return 0

        seeded = set(await self._db.get_value(_SEEDED_LEAGUES_KEY, []))
        seeded_before = set(seeded)
        today = datetime.now(timezone.utc).date()
        sent = 0
        try:
            for league in due:
                code = league.espn_code or ""
                try:
                    matches = await self._espn.matches_on(code, (today - timedelta(days=1), today))
                except FetchError as exc:
                    self.errors[league.key] = str(exc)
                    self._next_poll[league.key] = now + self._interval
                    log.warning("Не удалось получить матчи %s: %s", league.name, exc)
                    continue
                self.errors.pop(league.key, None)
                self._next_poll[league.key] = now + self._next_delay(matches, now)

                finals = [m for m in matches
                          if m.is_final and m.kickoff.timestamp() >= now - RESULT_MAX_AGE_SEC]
                known = await self._db.known_matches(m.id for m in finals)
                fresh = [m for m in finals if m.id not in known]

                if league.key not in seeded:
                    for match in fresh:
                        await self._db.add_match(match.id, league.key,
                                                 int(match.kickoff.timestamp()),
                                                 MatchStatus.SEEDED, int(now))
                    seeded.add(league.key)
                    continue

                for match in fresh:
                    if (await self._settings.get()).paused:
                        return sent
                    delivered = await send(match, league)
                    await self._db.add_match(
                        match.id, league.key, int(match.kickoff.timestamp()),
                        MatchStatus.SENT if delivered else MatchStatus.FAILED, int(time.time()),
                    )
                    sent += int(delivered)
        finally:
            if seeded != seeded_before:
                await self._db.set_value(_SEEDED_LEAGUES_KEY, sorted(seeded))
        return sent

    async def find_match(self, event_id: str) -> tuple[Match, Category] | None:
        """A match the bot has reported before, fetched fresh from ESPN."""
        known = await self._db.get_match(event_id)
        if known is None:
            return None
        league = next((c for c in CATEGORIES if c.key == known[0] and c.espn_code), None)
        if league is None:
            return None
        day = datetime.fromtimestamp(known[1], timezone.utc).date()
        try:
            matches = await self._espn.matches_on(
                league.espn_code or "", (day - timedelta(days=1), day, day + timedelta(days=1)))
        except FetchError as exc:
            log.warning("Не удалось получить матч %s: %s", event_id, exc)
            return None
        match = next((m for m in matches if m.id == event_id), None)
        return (match, league) if match else None

    # ----------------------------------------------------------------- overview

    async def _cached_matches(self, code: str, day: date) -> list[Match]:
        key = (code, day)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < OVERVIEW_CACHE_SEC:
            return hit[1]
        matches = await self._espn.scoreboard(code, day)
        self._cache[key] = (time.time(), matches)
        if len(self._cache) > 200:
            self._cache.clear()
        return matches

    async def day_overview(self, day: date | None = None
                           ) -> tuple[date, list[tuple[Category, list[Match]]], list[str]]:
        """Matches of tracked competitions on a local calendar day.

        Returns ``(day, [(league, matches)], [names of leagues that failed to load])``.
        """
        settings = await self._settings.get()
        day = day or datetime.now(self._tz).date()
        start = datetime.combine(day, dtime.min, tzinfo=self._tz)
        end = start + timedelta(days=1)
        # ESPN groups matches by its own calendar day, so look one day around the range.
        utc_day = start.astimezone(timezone.utc).date()
        utc_days = [utc_day + timedelta(days=offset) for offset in (-1, 0, 1)]

        groups: list[tuple[Category, list[Match]]] = []
        failed: list[str] = []
        for league in tracked_leagues(settings):
            code = league.espn_code or ""
            matches: dict[str, Match] = {}
            errors = 0
            for utc_date in utc_days:
                try:
                    for match in await self._cached_matches(code, utc_date):
                        matches[match.id] = match
                except FetchError as exc:
                    errors += 1
                    log.warning("Не удалось получить расписание %s: %s", league.name, exc)
            if errors == len(utc_days):
                failed.append(league.name)
                continue
            todays = sorted((m for m in matches.values() if start <= m.kickoff < end),
                            key=lambda m: (m.kickoff, m.id))
            if todays:
                groups.append((league, todays))
        return day, groups, failed

    async def maybe_send_digest(
        self, send: SendFn,
        render: Callable[[date, list[tuple[Category, list[Match]]]], list[str]],
    ) -> bool:
        settings = await self._settings.get()
        if not settings.digest or settings.paused:
            return False
        now = datetime.now(self._tz)
        planned = datetime.combine(now.date(), self._digest_time, tzinfo=self._tz)
        if not planned <= now < planned + DIGEST_WINDOW:
            return False
        today = now.date().isoformat()
        if await self._db.get_value(_DIGEST_KEY) == today:
            return False

        day, groups, failed = await self.day_overview(now.date())
        if failed and not groups:
            return False  # ESPN is down — try again on the next tick
        if groups:
            for chunk in render(day, groups):
                if not await send(chunk):
                    break
        await self._db.set_value(_DIGEST_KEY, today)
        log.info("Анонс матчей на %s: %d турниров", today, len(groups))
        return bool(groups)
