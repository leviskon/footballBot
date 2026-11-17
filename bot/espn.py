"""Client for ESPN's public soccer scoreboard (no API key needed)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .net import FetchError, HttpClient

log = logging.getLogger(__name__)

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard"
NOT_PLAYED = frozenset({
    "STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_ABANDONED",
    "STATUS_SUSPENDED", "STATUS_DELAYED", "STATUS_FORFEIT",
})


@dataclass(frozen=True, slots=True)
class TeamScore:
    id: str
    name: str
    score: int | None
    shootout: int | None
    winner: bool


@dataclass(frozen=True, slots=True)
class Goal:
    minute: str
    player: str
    team_id: str
    penalty: bool
    own_goal: bool


@dataclass(frozen=True, slots=True)
class Card:
    minute: str
    player: str
    team_id: str


@dataclass(frozen=True, slots=True)
class Match:
    id: str
    league_code: str
    kickoff: datetime
    state: str  # "pre" | "in" | "post"
    status_name: str
    completed: bool
    clock: str
    home: TeamScore
    away: TeamScore
    goals: tuple[Goal, ...] = ()
    red_cards: tuple[Card, ...] = ()
    venue: str | None = None
    note: str | None = None

    @property
    def is_final(self) -> bool:
        return self.completed and self.state == "post" and self.status_name not in NOT_PLAYED

    @property
    def is_live(self) -> bool:
        return self.state == "in"

    @property
    def is_cancelled(self) -> bool:
        return self.status_name in NOT_PLAYED

    @property
    def after_extra_time(self) -> bool:
        return self.status_name == "STATUS_FINAL_AET"

    @property
    def on_penalties(self) -> bool:
        return self.status_name == "STATUS_FINAL_PEN" or (
            self.home.shootout is not None and self.away.shootout is not None
        )

    def team(self, team_id: str) -> TeamScore | None:
        if team_id == self.home.id:
            return self.home
        if team_id == self.away.id:
            return self.away
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _team(raw: dict[str, Any]) -> TeamScore:
    team = raw.get("team") or {}
    return TeamScore(
        id=str(team.get("id") or raw.get("id") or ""),
        name=str(team.get("displayName") or team.get("shortDisplayName") or team.get("name") or "?"),
        score=_to_int(raw.get("score")),
        shootout=_to_int(raw.get("shootoutScore")),
        winner=bool(raw.get("winner")),
    )


def _player(detail: dict[str, Any]) -> str:
    athletes = detail.get("athletesInvolved") or []
    if athletes and isinstance(athletes[0], dict):
        return str(athletes[0].get("displayName") or athletes[0].get("shortName") or "—")
    return "—"


def _parse_event(event: dict[str, Any], code: str) -> Match:
    competition = event["competitions"][0]
    status = event.get("status") or competition.get("status") or {}
    status_type = status.get("type") or {}
    competitors = competition["competitors"]
    home_raw = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away_raw = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

    goals: list[Goal] = []
    reds: list[Card] = []
    for detail in competition.get("details") or []:
        if not isinstance(detail, dict) or detail.get("shootout"):
            continue
        minute = str((detail.get("clock") or {}).get("displayValue") or "").strip()
        team_id = str((detail.get("team") or {}).get("id") or "")
        if detail.get("scoringPlay"):
            goals.append(Goal(minute, _player(detail), team_id,
                              bool(detail.get("penaltyKick")), bool(detail.get("ownGoal"))))
        elif detail.get("redCard"):
            reds.append(Card(minute, _player(detail), team_id))

    note = None
    for item in competition.get("notes") or []:
        text = str((item or {}).get("headline") or (item or {}).get("text") or "").strip()
        if text and "penalt" not in text.lower():
            note = text
            break

    kickoff = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)

    return Match(
        id=str(event["id"]),
        league_code=code,
        kickoff=kickoff,
        state=str(status_type.get("state") or "pre"),
        status_name=str(status_type.get("name") or ""),
        completed=bool(status_type.get("completed")),
        clock=str(status.get("displayClock") or ""),
        home=_team(home_raw),
        away=_team(away_raw),
        goals=tuple(goals),
        red_cards=tuple(reds),
        venue=((competition.get("venue") or {}).get("fullName") or None),
        note=note,
    )


def parse_scoreboard(data: Any, code: str) -> list[Match]:
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    matches = []
    for event in events:
        try:
            matches.append(_parse_event(event, code))
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            log.debug("Пропущен некорректный матч в %s: %r", code, exc)
    return matches


class EspnClient:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    async def scoreboard(self, code: str, day: date) -> list[Match]:
        data = await self._http.fetch_json(SCOREBOARD_URL.format(code=code),
                                           params={"dates": day.strftime("%Y%m%d")})
        return parse_scoreboard(data, code)

    async def matches_on(self, code: str, days: Iterable[date]) -> list[Match]:
        """Matches of a competition on several days, deduplicated and sorted by kickoff.

        Raises ``FetchError`` only if every request failed.
        """
        days = list(days)
        results = await asyncio.gather(*(self.scoreboard(code, d) for d in days),
                                       return_exceptions=True)
        by_id: dict[str, Match] = {}
        errors = []
        for result in results:
            if isinstance(result, FetchError):
                errors.append(result)
                continue
            if isinstance(result, BaseException):
                raise result
            for match in result:
                by_id[match.id] = match
        if errors and len(errors) == len(days):
            raise errors[0]
        return sorted(by_id.values(), key=lambda m: (m.kickoff, m.id))
