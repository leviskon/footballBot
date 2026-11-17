"""RSS sources. To add a feed, append a ``Source`` — it is picked up on the next start.

``category`` pins every item of a single-league feed to that league; general feeds are
classified by keywords. Every feed below was checked to be alive in November 2026.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Source:
    key: str
    name: str
    url: str
    lang: str  # "ru" | "en"
    category: str | None = None
    enabled: bool = True


SOURCES: tuple[Source, ...] = (
    # Russian-language outlets
    Source("sportsru", "Sports.ru", "https://www.sports.ru/rss/rubric.xml?s=208", "ru"),
    Source("championat", "Чемпионат", "https://www.championat.com/rss/news/football/", "ru"),
    Source("sportexpress", "Спорт-Экспресс",
           "https://www.sport-express.ru/services/materials/news/football/se/", "ru"),
    Source("soccerru", "Soccer.ru", "https://www.soccer.ru/rss.xml", "ru"),
    Source("rambler", "Рамблер Спорт", "https://sport.rambler.ru/rss/football/", "ru"),
    # English-language outlets
    Source("bbc", "BBC Sport", "https://feeds.bbci.co.uk/sport/football/rss.xml", "en"),
    Source("guardian", "The Guardian", "https://www.theguardian.com/football/rss", "en"),
    Source("skysports", "Sky Sports", "https://www.skysports.com/rss/12040", "en"),
    Source("footballitalia", "Football Italia", "https://football-italia.net/feed/", "en",
           category="seriea"),
    Source("footballespana", "Football España", "https://www.football-espana.net/feed", "en",
           category="laliga"),
    Source("gffn", "Get French Football News", "https://www.getfootballnewsfrance.com/feed/", "en",
           category="ligue1"),
    Source("bulinews", "Bulinews", "https://bulinews.com/rss.xml", "en", category="bundesliga"),
)

SOURCE_BY_KEY: dict[str, Source] = {s.key: s for s in SOURCES}
LANGUAGES: dict[str, str] = {"ru": "🇷🇺 Русские источники", "en": "🇬🇧 Английские источники"}
LANGUAGE_NAMES: dict[str, str] = {"ru": "русские", "en": "английские"}

if len(SOURCE_BY_KEY) != len(SOURCES):  # pragma: no cover - guards against copy-paste errors
    raise RuntimeError("duplicate source keys in SOURCES")
