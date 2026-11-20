"""Builds result cards from ESPN matches: downloads logos, finds a match photo in the news,
translates scorer names and renders the picture (see :mod:`bot.cards`)."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path

from .cards import CardEvent, CardTeam, ResultCard, render_card
from .catalog import Category
from .espn import Match, TeamScore
from .net import FetchError, HttpClient
from .render import team_name
from .photos import PhotoFinder
from .storage import Storage
from .textutil import fold
from .writer import LLMError, PostWriter

log = logging.getLogger(__name__)

ASSET_TIMEOUT_SEC = 15
LOGO_CACHE_SIZE = 200
HERO_WINDOW_SEC = 12 * 3600  # match reports published up to this long after kickoff
HERO_CANDIDATES = 3
_NAMES_KEY = "ru_surnames"
BRAND_LOGO = Path(__file__).resolve().parent / "assets" / "brand.png"


def _stems(name: str) -> list[str]:
    """Word stems of a Russian team name: «Боруссия Дортмунд» → ["борус", "дортм"]."""
    words = [w for w in fold(name).replace("-", " ").split() if len(w) >= 3]
    return [w[:5] if len(w) > 5 else w[:-1] if len(w) > 3 else w for w in words]


def mentions_both(title: str, home: str, away: str) -> bool:
    """Whether a news title is about the match between ``home`` and ``away``."""
    text = fold(title)
    return all(any(stem in text for stem in _stems(team)) for team in (home, away))


def _latin_surname(name: str) -> str:
    parts = name.split()
    return parts[-1] if parts else name


class CardMaker:
    def __init__(self, http: HttpClient, db: Storage, writer: PostWriter | None,
                 photos: PhotoFinder, *, brand: str = "") -> None:
        self._http = http
        self._photos = photos
        self._db = db
        self._writer = writer
        self._brand = brand
        self._brand_logo = BRAND_LOGO.read_bytes() if BRAND_LOGO.is_file() else None
        self._assets: OrderedDict[str, bytes | None] = OrderedDict()
        self._names: dict[str, str] | None = None

    # ---------------------------------------------------------------- assets

    async def _asset(self, url: str | None) -> bytes | None:
        if not url:
            return None
        if url in self._assets:
            self._assets.move_to_end(url)
            return self._assets[url]
        try:
            data: bytes | None = await self._http.fetch_bytes(url, timeout=ASSET_TIMEOUT_SEC)
        except FetchError as exc:
            log.info("Не удалось скачать %s: %s", url, exc)
            return None  # not cached: maybe it loads next time
        self._assets[url] = data
        if len(self._assets) > LOGO_CACHE_SIZE:
            self._assets.popitem(last=False)
        return data

    async def _hero(self, match: Match, home: str, away: str) -> bytes | None:
        """The photo of the freshest news story about this match, if the bot has one."""
        start = int(match.kickoff.timestamp())
        stories = [r for r in await self._db.news_between(start, start + HERO_WINDOW_SEC)
                   if mentions_both(r.title, home, away)]
        for story in stories[:HERO_CANDIDATES]:
            photo = await self._photos.story_photo(story)
            if photo:
                log.info("Фото для карточки %s — %s: %s", home, away, story.title)
                return photo
        return None

    # ----------------------------------------------------------------- names

    async def _surnames(self, names: list[str]) -> dict[str, str]:
        if self._names is None:
            stored = await self._db.get_value(_NAMES_KEY, {})
            self._names = stored if isinstance(stored, dict) else {}
        missing = [n for n in dict.fromkeys(names) if n and n not in self._names]
        if missing and self._writer is not None:
            try:
                found = await self._writer.russian_surnames(missing)
            except LLMError as exc:
                log.warning("Не удалось перевести фамилии: %s", exc)
                found = {}
            if found:
                self._names.update(found)
                await self._db.set_value(_NAMES_KEY, self._names)
        return {n: self._names.get(n) or _latin_surname(n) for n in names}

    # ------------------------------------------------------------------ card

    @staticmethod
    def _note(match: Match) -> str:
        if match.on_penalties and match.home.shootout is not None \
                and match.away.shootout is not None:
            return f"пен. {match.home.shootout}:{match.away.shootout}"
        if match.after_extra_time:
            return "после доп. времени"
        return ""

    async def events(self, match: Match) -> tuple[list[CardEvent], list[CardEvent]]:
        """Goals and red cards of each side in time order, with Russian surnames.

        ESPN credits an own goal to the team that benefits from it."""
        players = [g.player for g in match.goals] + [c.player for c in match.red_cards]
        surnames = await self._surnames(players)
        sides: dict[str, list[tuple[float, CardEvent]]] = {match.home.id: [], match.away.id: []}
        for goal in match.goals:
            if goal.team_id in sides:
                kind = "og" if goal.own_goal else "pen" if goal.penalty else "goal"
                sides[goal.team_id].append(
                    (_minute_key(goal.minute), CardEvent(goal.minute, surnames[goal.player], kind)))
        for card in match.red_cards:
            if card.team_id in sides:
                sides[card.team_id].append(
                    (_minute_key(card.minute), CardEvent(card.minute, surnames[card.player], "red")))

        def ordered(team_id: str) -> list[CardEvent]:
            return [e for _, e in sorted(sides[team_id], key=lambda item: item[0])]

        return ordered(match.home.id), ordered(match.away.id)

    async def make(self, match: Match, league: Category, *, hero: bool = True) -> bytes:
        """Render the result card of a finished match (JPEG bytes)."""
        home_name, away_name = team_name(match.home.name), team_name(match.away.name)
        home_logo, away_logo, league_logo, hero_photo, (home_events, away_events) = \
            await asyncio.gather(
                self._asset(match.home.logo), self._asset(match.away.logo),
                self._asset(match.league_logo),
                self._hero(match, home_name, away_name) if hero else _none(),
                self.events(match),
            )

        def card_team(team: TeamScore, name: str, logo: bytes | None) -> CardTeam:
            return CardTeam(name, logo, team.color, team.alt_color)

        card = ResultCard(
            home=card_team(match.home, home_name, home_logo),
            away=card_team(match.away, away_name, away_logo),
            home_score=match.home.score, away_score=match.away.score,
            home_events=home_events, away_events=away_events,
            league_logo=league_logo, hero=hero_photo, note=self._note(match),
            brand=self._brand, brand_logo=self._brand_logo,
        )
        return await asyncio.to_thread(render_card, card)


async def _none() -> None:
    return None


def _minute_key(minute: str) -> float:
    """Sort key for «45'+2'», «90'», «12'»."""
    digits = [int(part) for part in "".join(c if c.isdigit() else " " for c in minute).split()]
    if not digits:
        return 999.0
    return digits[0] + (digits[1] / 100 if len(digits) > 1 else 0)
