"""Finding a picture for a news post.

Some sites block pictures for servers abroad (Championat answers with a 1×1 pixel, Sport-
Express with 403), so a post's picture is looked for step by step:

1. the article's own picture and the one from the RSS item (passed in by the caller);
2. the same story told by another outlet (the bot's duplicate detection);
3. the person from the title («Жозе Моуринью: …»): another fresh story about them from the
   bot's database, else their photo from Wikipedia (free licence, credited in the caption);
4. the other players, coaches and clubs the news mentions, the same way.

Hosts that failed to give a picture are skipped for a while, so a post does not wait for
the same blocked site again and again.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from .cards import to_jpeg
from .catalog import EXTRA_TEAM_NAMES, STARS, CATEGORIES
from .net import FetchError, HttpClient
from .storage import NewsRecord, Storage
from .textutil import fold, html_to_text, same_story, truncate
from .writer import extract_image

log = logging.getLogger(__name__)

TIMEOUT_SEC = 15
RELATED_WINDOW_SEC = 12 * 3600  # other outlets' versions of a story
SUBJECT_WINDOW_SEC = 48 * 3600  # other stories about the same person or club
MAX_DOWNLOADS = 3  # pages tried per step, so a post never waits too long
WIKI_CACHE_SIZE = 300
IMAGE_TIMEOUT_SEC = 8
BAD_HOST_SEC = 30 * 60
# Wikimedia asks API clients to identify themselves instead of posing as a browser.
WIKI_HEADERS = {"User-Agent": "FootballNewsBot/1.0 (Telegram channel helper; python-aiohttp)"}

STAR_NAMES = frozenset(e.name for e in STARS.entities)
_TEAM_WORDS = frozenset(
    fold(name) for name in [*EXTRA_TEAM_NAMES.values(),
                            *(e.name for c in CATEGORIES if c is not STARS for e in c.entities)]
)
# Words that open a title like a name but are not the person the news is about.
_NOT_A_SUBJECT = frozenset(fold(w) for w in (
    "Источник", "Источники", "СМИ", "Инсайдер", "Официально", "Слух", "Слухи", "Видео", "Фото",
    "Инфографика", "Статистика", "Прогноз", "Трансферы", "Мнение", "Опрос", "Эксперт", "Агент",
    "Журналист", "Чемпионат", "Лига", "Матч", "Тренер", "Президент", "Клуб", "Сборная",
    # insiders and journalists: news cite them, the photo should show the subject
    "Романо", "Ди Марцио", "Педулла", "Орнстейн", "Плеттенберг", "Фальк", "Джейкобс",
    "Моретто", "Ромеро", "Скира", "Галетти", "Окинс", "Ауна", "Конур", "Таволиери", "Шет",
    "Джойс", "Стоун", "Romano", "Ornstein", "Di Marzio", "Plettenberg",
))
_TITLE_PERSON_RE = re.compile(
    r"^\s*((?:[A-ZА-ЯЁ][\w'’\-]+\s+){0,2}[A-ZА-ЯЁ][\w'’\-]+)\s*(?::|—|–|\s-\s)")


@dataclass(frozen=True, slots=True)
class Picture:
    data: bytes  # JPEG
    credit: str = ""  # attribution the licence requires (Wikimedia photos)


def title_person(title: str) -> str | None:
    """«Жозе Моуринью: …», «Лангович — о Батракове» → the person the news is about."""
    match = _TITLE_PERSON_RE.match(title)
    if not match:
        return None
    name = match.group(1)
    words = {fold(w) for w in name.split()}
    if fold(name) in _NOT_A_SUBJECT or words & _NOT_A_SUBJECT or fold(name) in _TEAM_WORDS:
        return None
    return name


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _stem(word: str) -> str:
    word = fold(word)
    return word[:5] if len(word) > 5 else word


def _mentions(text: str, name: str) -> bool:
    """Whether ``text`` mentions ``name`` (by the stem of its last word: «Моуринью» in
    «Моуринью», «Батракова» for «Батраков»)."""
    words = name.split()
    return bool(words) and _stem(words[-1]) in fold(text)


def _credit(meta: dict) -> str:
    def value(key: str) -> str:
        item = meta.get(key) or {}
        return html_to_text(str(item.get("value") or "")).replace("\n", " ").strip()

    artist = truncate(value("Artist"), 60) or "неизвестный автор"
    licence = value("LicenseShortName")
    return f"📷 {artist} / Wikimedia Commons" + (f", {licence}" if licence else "")


class PhotoFinder:
    def __init__(self, http: HttpClient, db: Storage) -> None:
        self._http = http
        self._db = db
        self._wiki: OrderedDict[str, Picture | None] = OrderedDict()
        # hosts that gave no usable picture (a timeout, a 1×1 pixel, 403…) → skip until
        self._bad_hosts: dict[str, float] = {}
        self._strikes: dict[str, int] = {}  # failures in a row per host

    # ------------------------------------------------------------ downloading

    def _host_ok(self, url: str | None) -> bool:
        return bool(url) and self._bad_hosts.get(_host(url), 0.0) <= time.monotonic()

    def _host_failed(self, url: str) -> None:
        """Two failures in a row put a host aside (one may be a passing network glitch)."""
        host = _host(url)
        self._strikes[host] = self._strikes.get(host, 0) + 1
        if self._strikes[host] >= 2:
            self._bad_hosts[host] = time.monotonic() + BAD_HOST_SEC
            self._strikes[host] = 0
            log.info("Сайт %s не отдаёт фото — пропускаю его %d мин", host, BAD_HOST_SEC // 60)

    async def download(self, url: str | None, *, headers: dict[str, str] | None = None
                       ) -> bytes | None:
        """A picture from the web re-encoded as a JPEG for Telegram (None if unusable).

        A host that gives nothing usable is not asked again for a while."""
        if not url or not self._host_ok(url):
            return None
        try:
            data = await self._http.fetch_bytes(url, timeout=IMAGE_TIMEOUT_SEC, headers=headers)
        except FetchError as exc:
            log.info("Не удалось скачать фото %s: %s", url, exc)
            self._host_failed(url)
            return None
        photo = await asyncio.to_thread(to_jpeg, data)
        if photo is None:
            log.info("Фото %s не подходит (заглушка или слишком маленькое)", url)
            self._host_failed(url)
        else:
            self._strikes.pop(_host(url), None)
        return photo

    async def story_photo(self, story: NewsRecord) -> bytes | None:
        """The picture of a stored story: the one from the feed (a single quick download),
        else the page's own."""
        photo = await self.download(story.image or None)
        if photo or not self._host_ok(story.url):
            return photo
        try:
            html = await self._http.fetch_text(story.url, timeout=TIMEOUT_SEC)
        except FetchError:
            self._host_failed(story.url)
            return None
        return await self.download(extract_image(html, story.url))

    async def _first_photo(self, stories: Iterable[NewsRecord]) -> tuple[bytes, NewsRecord] | None:
        tried = 0
        for story in stories:
            if tried == MAX_DOWNLOADS:
                break
            if not self._host_ok(story.image or None) and not self._host_ok(story.url):
                continue  # both the picture and the page are on hosts that fail
            tried += 1
            photo = await self.story_photo(story)
            if photo:
                return photo, story
        return None

    # ------------------------------------------------------------------ steps

    async def same_story(self, record: NewsRecord) -> Picture | None:
        """The same story told by another outlet."""
        stories = [
            r for r in await self._db.news_between(record.published_at - RELATED_WINDOW_SEC,
                                                   record.published_at + RELATED_WINDOW_SEC)
            if r.id != record.id and r.source != record.source and r.lang == record.lang
            and (r.dup_of == record.id or record.dup_of == r.id
                 or same_story(record.fingerprint, r.fingerprint))
        ]
        stories.sort(key=lambda r: abs(r.published_at - record.published_at))
        found = await self._first_photo(stories)
        if found:
            log.info("Фото для «%s»: та же новость у %s", record.title, found[1].source)
            return Picture(found[0])
        return None

    async def same_subject(self, record: NewsRecord, subjects: Sequence[str]) -> Picture | None:
        """A fresh story from another outlet about one of ``subjects`` (people or clubs)."""
        if not subjects:
            return None
        stories = [r for r in await self._db.news_between(
                       record.published_at - SUBJECT_WINDOW_SEC,
                       record.published_at + RELATED_WINDOW_SEC, limit=1000)
                   if r.id != record.id and r.source != record.source and r.relevant]
        for subject in subjects:
            # the subject must lead the other story too, so its photo most likely shows them
            matching = [r for r in stories
                        if subject in r.entities[:2] or _mentions(r.title[:60], subject)]
            matching.sort(key=lambda r: abs(r.published_at - record.published_at))
            found = await self._first_photo(matching)
            if found:
                log.info("Фото для «%s»: новость про «%s» у %s", record.title, subject,
                         found[1].source)
                return Picture(found[0])
        return None

    async def wikipedia(self, record: NewsRecord, people: Sequence[str]) -> Picture | None:
        """The photo of one of ``people`` from their Wikipedia article."""
        for name in list(dict.fromkeys(p for p in people if p))[:2]:
            picture = await self._wiki_person(name)
            if picture:
                log.info("Фото для «%s»: Википедия, %s", record.title, name)
                return picture
        return None

    async def _wiki_person(self, name: str) -> Picture | None:
        if name in self._wiki:
            self._wiki.move_to_end(name)
            return self._wiki[name]
        try:
            picture = await self._wiki_lookup(name)
        except (FetchError, KeyError, IndexError, TypeError, AttributeError) as exc:
            log.info("Википедия не нашла фото «%s»: %s", name, exc)
            return None  # not cached: a network error may pass
        self._wiki[name] = picture
        if len(self._wiki) > WIKI_CACHE_SIZE:
            self._wiki.popitem(last=False)
        return picture

    async def _wiki_lookup(self, name: str) -> Picture | None:
        cyrillic = bool(re.search(r"[а-яё]", name, re.IGNORECASE))
        api = f"https://{'ru' if cyrillic else 'en'}.wikipedia.org/w/api.php"
        found = await self._http.fetch_json(api, params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{name} {'футбол' if cyrillic else 'football'}", "gsrlimit": "3",
            "prop": "pageimages", "piprop": "name", "pilicense": "free",
        }, headers=WIKI_HEADERS)
        pages = sorted((found.get("query") or {}).get("pages", {}).values(),
                       key=lambda p: p.get("index", 99))
        page = next((p for p in pages if _mentions(p.get("title", ""), name)), None)
        if page is None or not page.get("pageimage"):
            return None
        info = await self._http.fetch_json(api, params={
            "action": "query", "format": "json", "titles": f"File:{page['pageimage']}",
            "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": "1280",
            "iiextmetadatafilter": "Artist|LicenseShortName",
        }, headers=WIKI_HEADERS)
        image = next(iter(info["query"]["pages"].values()))["imageinfo"][0]
        data = await self.download(image.get("thumburl") or image.get("url"), headers=WIKI_HEADERS)
        if not data:
            return None
        return Picture(data, _credit(image.get("extmetadata") or {}))

    # ------------------------------------------------------------------ chain

    async def for_news(self, record: NewsRecord, images: Sequence[str]) -> Picture | None:
        """The best available picture for a news post (see the module docstring)."""
        for url in images:
            photo = await self.download(url)
            if photo:
                return Picture(photo)
        hero = title_person(record.title)
        stars = [e for e in record.entities if e in STAR_NAMES and e != hero]
        clubs = [e for e in record.entities if e not in STAR_NAMES]
        steps: list[Callable[[], Awaitable[Picture | None]]] = [
            lambda: self.same_story(record),
            # the person from the title comes first, even if only Wikipedia has their photo:
            # «Данилов: мечтаю закрыть Мбаппе» must not get a photo of Mbappé
            lambda: self.same_subject(record, [hero] if hero else []),
            lambda: self.wikipedia(record, [hero] if hero else []),
            lambda: self.same_subject(record, stars + clubs),
            lambda: self.wikipedia(record, stars),
        ]
        for step in steps:
            try:
                picture = await step()
            except Exception:
                log.exception("Ошибка при поиске фото")
                continue
            if picture:
                return picture
        log.info("Пост без фото: «%s» — ни у статьи, ни у других сайтов, ни в Википедии",
                 record.title)
        return None
