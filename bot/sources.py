"""RSS sources. To add a feed, append a ``Source`` — it is picked up on the next start.

``category`` pins every item of a single-league feed to that league; general feeds are
classified by keywords. Every feed below was checked to be alive in November 2026.
Only Russian sources are on by default; the other languages are switched on in «⚙️ Настройки».
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
    skip_urls: str | None = None  # regex: items whose link matches it are not news (topic pages)


SOURCES: tuple[Source, ...] = (
    # Russian-language outlets
    Source("sportsru", "Sports.ru", "https://www.sports.ru/rss/rubric.xml?s=208", "ru"),
    Source("championat", "Чемпионат", "https://www.championat.com/rss/news/football/", "ru"),
    Source("sportexpress", "Спорт-Экспресс",
           "https://www.sport-express.ru/services/materials/news/football/se/", "ru"),
    Source("soccerru", "Soccer.ru", "https://www.soccer.ru/rss.xml", "ru"),
    Source("rambler", "Рамблер Спорт", "https://sport.rambler.ru/rss/football/", "ru"),
    Source("matchtv", "Матч ТВ", "https://matchtv.ru/news/rss/football", "ru"),
    # English-language outlets
    Source("bbc", "BBC Sport", "https://feeds.bbci.co.uk/sport/football/rss.xml", "en"),
    Source("guardian", "The Guardian", "https://www.theguardian.com/football/rss", "en"),
    Source("skysports", "Sky Sports", "https://www.skysports.com/rss/12040", "en"),
    Source("athletic", "The Athletic", "https://www.nytimes.com/athletic/rss/football/", "en"),
    Source("cbssports", "CBS Sports", "https://www.cbssports.com/rss/headlines/soccer/", "en"),
    Source("transfermarkt", "Transfermarkt", "https://www.transfermarkt.com/rss/news", "en"),
    Source("footballitalia", "Football Italia", "https://football-italia.net/feed/", "en",
           category="seriea"),
    Source("footballespana", "Football España", "https://www.football-espana.net/feed", "en",
           category="laliga"),
    Source("gffn", "Get French Football News", "https://www.getfootballnewsfrance.com/feed/", "en",
           category="ligue1"),
    Source("bulinews", "Bulinews", "https://bulinews.com/rss.xml", "en", category="bundesliga"),
    # Spanish-language outlets
    Source("marca", "Marca", "https://www.marca.com/rss/googlenews/futbol.xml", "es"),
    Source("as", "AS", "https://feeds.as.com/mrss-s/pages/as/site/as.com/section/futbol/portada/",
           "es"),
    Source("mundodeportivo", "Mundo Deportivo", "https://www.mundodeportivo.com/rss/futbol", "es"),
    Source("sportes", "Sport", "https://www.sport.es/es/rss/last-news/news.xml", "es"),
    # Italian-language outlets (incl. the insiders Gianluca Di Marzio and Alfredo Pedullà)
    Source("gazzetta", "La Gazzetta dello Sport",
           "https://www.gazzetta.it/dynamic-feed/rss/section/Calcio.xml", "it"),
    Source("corrieredellosport", "Corriere dello Sport",
           "https://www.corrieredellosport.it/rss/calcio", "it"),
    Source("tuttosport", "Tuttosport", "https://www.tuttosport.com/rss/calcio", "it"),
    Source("skysportit", "Sky Sport Italia", "https://sport.sky.it/rss/sport_calcio.xml", "it"),
    Source("dimarzio", "Джанлука Ди Марцио", "https://www.gianlucadimarzio.com/rss", "it"),
    Source("pedulla", "Альфредо Педулла", "https://www.alfredopedulla.com/feed/", "it"),
    # German-language outlets
    Source("kicker", "Kicker", "https://newsfeed.kicker.de/news/fussball", "de"),
    Source("bild", "Bild", "https://www.bild.de/feed/bundesliga.xml", "de"),
    # French-language outlets
    Source("lequipe", "L'Équipe", "https://dwh.lequipe.fr/api/edito/rss?path=/Football/", "fr"),
    Source("footmercato", "Foot Mercato", "https://www.footmercato.net/flux-rss", "fr"),
    Source("rmcsport", "RMC Sport", "https://rmcsport.bfmtv.com/rss/football/", "fr",
           skip_urls=r"_DN-\d+\.html"),
)

SOURCE_BY_KEY: dict[str, Source] = {s.key: s for s in SOURCES}
LANGUAGES: dict[str, str] = {
    "ru": "🇷🇺 Русские источники",
    "en": "🇬🇧 Английские источники",
    "es": "🇪🇸 Испанские источники",
    "it": "🇮🇹 Итальянские источники",
    "de": "🇩🇪 Немецкие источники",
    "fr": "🇫🇷 Французские источники",
}
LANGUAGE_NAMES: dict[str, str] = {
    "ru": "русские", "en": "английские", "es": "испанские", "it": "итальянские",
    "de": "немецкие", "fr": "французские",
}

if len(SOURCE_BY_KEY) != len(SOURCES):  # pragma: no cover - guards against copy-paste errors
    raise RuntimeError("duplicate source keys in SOURCES")
