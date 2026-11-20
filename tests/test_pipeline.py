"""End-to-end tests of the news and match pipelines with fake network clients."""

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from html import escape

from bot.access import Access
from bot.classifier import Classifier
from bot.espn import Match, TeamScore
from bot.matches import MatchService
from bot.net import FeedResponse, FetchError
from bot.news import MAX_DELIVERY_ATTEMPTS, NewsCollector, parse_feed
from bot.settings import BotSettings, SettingsStore
from bot.sources import Source
from bot.storage import NewsStatus, Storage

RU = Source("ru1", "Тест RU", "https://ru.example/rss", "ru")
RU2 = Source("ru2", "Тест RU 2", "https://ru2.example/rss", "ru")
EN = Source("en1", "Test EN", "https://en.example/rss", "en")


def rss(*items: tuple[str, str, float]) -> bytes:
    body = "".join(
        f"<item><title>{escape(title)}</title><link>{link}</link>"
        f"<description>{escape(title)} — подробности.</description>"
        f"<pubDate>{formatdate(ts, usegmt=True)}</pubDate></item>"
        for title, link, ts in items
    )
    return f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>' \
           f"<title>t</title>{body}</channel></rss>".encode()


class FakeHttp:
    def __init__(self) -> None:
        self.feeds: dict[str, bytes] = {}
        self.broken: set[str] = set()

    async def fetch_feed(self, url: str) -> FeedResponse:
        if url in self.broken:
            raise FetchError("HTTP 503")
        return FeedResponse(False, self.feeds.get(url, rss()), "application/rss+xml")


async def make_collector(db: Storage, http: FakeHttp) -> NewsCollector:
    settings = SettingsStore(db)
    return NewsCollector(http, db, settings, Classifier(), max_age_hours=3, interval=60,
                         sources=(RU, RU2, EN))


def run(coro):
    return asyncio.run(coro)


def test_first_run_and_incremental_updates(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        http = FakeHttp()
        collector = await make_collector(db, http)
        now = time.time()

        titles = [
            "«Арсенал» продлил контракт с Сакой",
            "Райс пропустит матч с «Челси» из-за травмы",
            "Артета высказался о борьбе за титул",
            "«Арсенал» интересуется защитником «Бенфики»",
            "Одегор вернулся в общую группу",
            "Мерино признан игроком месяца в АПЛ",
        ]
        http.feeds[RU.url] = rss(*[
            (title, f"https://ru.example/a{i}", now - 900 + i) for i, title in enumerate(titles)
        ])
        report = await collector.collect()
        # English sources are off by default, so only the two Russian ones were fetched.
        assert report.sources_ok == 2
        assert report.queued == 3 and report.seeded == 3  # first-run sample of 3

        sent: list[str] = []

        async def send(record):
            sent.append(record.title)
            return True

        async def not_paused():
            return False

        assert await collector.deliver_pending(send, is_paused=not_paused) == 3
        assert len(sent) == 3

        # 2) Next cycle: relevant → queued, RPL → filtered, paraphrase → duplicate,
        #    old → expired, already known → ignored.
        http.feeds[RU2.url] = rss(
            ("«Барселона» обыграла «Бетис» со счётом 3:1", "https://ru2.example/1", now - 60),
            ("«Зенит» обыграл «Спартак» в РПЛ", "https://ru2.example/2", now - 50),
            ("«Реал» продлил контракт с Винисиусом до 2030 года", "https://ru2.example/3", now - 40),
            ("«Ливерпуль» уволил тренера", "https://ru2.example/old", now - 5 * 3600),
        )
        # RU2 was seeded in the first run (empty feed), so these are genuinely new.
        report = await collector.collect()
        statuses = {r.url.rsplit("/", 1)[1]: r.status for r in report.records}
        assert statuses == {"1": NewsStatus.PENDING, "2": NewsStatus.FILTERED,
                            "3": NewsStatus.PENDING, "old": NewsStatus.EXPIRED}

        http.feeds[RU.url] = rss(
            ("«Реал» и Винисиус продлили контракт до 2030 года", "https://ru.example/dup", now - 30),
        )
        report = await collector.collect()
        [record] = report.records
        assert record.status == NewsStatus.DUPLICATE and record.dup_of is not None

        # 3) Delivery failures: Telegram rejects → attempts grow → eventually «failed».
        async def reject(record):
            return False

        for _ in range(MAX_DELIVERY_ATTEMPTS):
            await collector.deliver_pending(reject, is_paused=not_paused)
        assert await db.count_news(status=NewsStatus.PENDING) == 0
        assert await db.count_news(status=NewsStatus.FAILED) == 2

        latest = await db.latest_news(10, {"laliga", "epl"})
        assert latest and all(r.relevant for r in latest)
        await db.close()

    run(scenario())


def test_enabling_a_language_seeds_its_sources(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        http = FakeHttp()
        collector = await make_collector(db, http)
        now = time.time()
        await collector.collect()  # first run with empty feeds

        http.feeds[EN.url] = rss(("Arsenal agree new deal with Saka", "https://en.example/1", now))
        await collector._settings.update(lambda s: s.langs.add("en"))
        await collector.forget_sources(lang="en")
        report = await collector.collect()
        [record] = report.records
        assert record.status == NewsStatus.SKIPPED  # old content of a new source is not sent

        http.feeds[EN.url] = rss(("Chelsea sack their manager", "https://en.example/2", now))
        report = await collector.collect()
        [record] = report.records
        assert record.status == NewsStatus.PENDING
        await db.close()

    run(scenario())


def test_failing_source_backs_off(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        http = FakeHttp()
        http.broken.add(RU.url)
        collector = await make_collector(db, http)
        report = await collector.collect()
        assert report.sources_failed == 1
        health = collector.health[RU.key]
        assert health.failures == 1 and health.retry_at > time.time()
        report = await collector.collect()  # still backing off → not even requested
        assert report.sources_failed == 0 and report.sources_ok == 1
        await db.close()

    run(scenario())


def test_settings_roundtrip_and_validation(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        store = SettingsStore(db)
        await store.update(lambda s: (s.categories.discard("epl"), s.langs.add("en")))
        fresh = SettingsStore(db)
        settings = await fresh.get()
        assert "epl" not in settings.categories and settings.langs == {"ru", "en"}
        await db.close()

    run(scenario())
    garbage = BotSettings.from_json({"categories": ["epl", "nope"], "langs": "ru", "paused": 1})
    assert garbage.categories == {"epl"} and garbage.langs == {"ru"} and garbage.paused is False


def test_owner_claim(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        access = Access(db, frozenset())
        await access.load()
        assert access.can_claim and access.recipients == ()
        assert await access.claim(111)
        assert not await access.claim(222)
        assert access.is_admin(111) and not access.is_admin(222)

        reloaded = Access(db, frozenset())
        await reloaded.load()
        assert reloaded.recipients == (111,)

        static = Access(db, frozenset({5, 3}))
        await static.load()
        assert static.recipients == (3, 5) and not static.can_claim
        await db.close()

    run(scenario())


# ---------------------------------------------------------------------- matches

def make_match(match_id: str, *, final: bool, kickoff: datetime) -> Match:
    return Match(
        id=match_id, league_code="eng.1", kickoff=kickoff,
        state="post" if final else "pre", status_name="STATUS_FULL_TIME" if final else "",
        completed=final, clock="90'",
        home=TeamScore("1", "Arsenal", 2 if final else None, None, final),
        away=TeamScore("2", "Chelsea", 1 if final else None, None, False),
    )


class FakeEspn:
    def __init__(self) -> None:
        self.matches: list[Match] = []

    async def matches_on(self, code, days):
        return [m for m in self.matches if m.league_code == code]

    async def scoreboard(self, code, day):
        return [m for m in self.matches if m.league_code == code]


def test_results_are_seeded_then_sent_once(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        settings = SettingsStore(db)
        await settings.update(lambda s: s.categories.intersection_update({"epl"}))
        espn = FakeEspn()
        service = MatchService(espn, db, settings, tz=timezone.utc, interval=60,
                               digest_time=datetime.now(timezone.utc).time())
        kickoff = datetime.now(timezone.utc) - timedelta(hours=2)
        espn.matches = [make_match("old", final=True, kickoff=kickoff)]

        sent: list[str] = []

        async def send(match, league):
            sent.append(f"{league.key}:{match.id}")
            return True

        assert await service.poll_results(send, force=True) == 0  # seeding
        espn.matches.append(make_match("new", final=True, kickoff=kickoff))
        espn.matches.append(make_match("later", final=False, kickoff=kickoff + timedelta(hours=3)))
        assert await service.poll_results(send, force=True) == 1
        assert await service.poll_results(send, force=True) == 0
        assert sent == ["epl:new"]
        assert await db.get_match("new") == ("epl", int(kickoff.timestamp()))
        found = await service.find_match("new")  # «✍️ Сделать пост» under the result
        assert found is not None and found[0].id == "new" and found[1].key == "epl"
        assert await service.find_match("unknown") is None

        # Digest: sent once per day, inside the window after DIGEST_TIME.
        digests: list[str] = []

        async def send_digest(text):
            digests.append(text)
            return True

        assert await service.maybe_send_digest(send_digest, lambda d, g: ["digest"])
        assert not await service.maybe_send_digest(send_digest, lambda d, g: ["digest"])
        assert digests == ["digest"]
        await db.close()

    run(scenario())


def test_live_match_polls_often_idle_rarely(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        service = MatchService(FakeEspn(), db, SettingsStore(db), tz=timezone.utc,
                               interval=60, digest_time=datetime.now(timezone.utc).time())
        now = time.time()
        live = replace(make_match("x", final=False, kickoff=datetime.now(timezone.utc)),
                       state="in")
        far = make_match("y", final=False,
                         kickoff=datetime.now(timezone.utc) + timedelta(days=2))
        assert service._next_delay([live], now) == 60
        assert service._next_delay([far], now) > 60
        await db.close()

    run(scenario())


def test_topic_pages_are_skipped():
    source = replace(RU, skip_urls=r"_DN-\d+\.html")
    now = int(time.time())
    body = rss(("Real Madrid", "https://site.example/football/real-madrid_DN-2023.html", now),
               ("«Реал» продлил контракт с Вальверде", "https://site.example/news/1", now))
    assert [c.title for c in parse_feed(source, body, "application/rss+xml", now)] == [
        "«Реал» продлил контракт с Вальверде"]


def test_latest_button_never_repeats_news(tmp_path):
    from datetime import time as dtime
    from pathlib import Path

    from bot.app import App
    from bot.config import Config

    class FakeNotifier:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, chat_id, text, *, keyboard=None, preview_url=None):
            self.sent.append(preview_url)
            return True

        async def broadcast(self, text, *, keyboard=None, preview_url=None):
            self.sent.append(preview_url)
            return True

    async def scenario():
        db = await Storage.open(tmp_path / "bot.sqlite3")
        config = Config(bot_token="", admin_ids=frozenset({1}), tz=timezone.utc, tz_name="UTC",
                        db_path=Path(tmp_path), log_dir=Path(tmp_path), log_level="INFO",
                        news_interval=60, matches_interval=60, max_news_age_hours=3,
                        digest_time=dtime(9), request_timeout=5)
        app = App(config, bot=None, db=db, http=FakeHttp())
        app.notifier = notifier = FakeNotifier()

        now = int(time.time())
        http = FakeHttp()
        http.feeds[RU.url] = rss(
            ("«Реал» продлил контракт с Вальверде до 2031 года", "https://ru.example/a", now - 60),
            ("«Барселона» обыграла «Севилью» в матче Ла Лиги", "https://ru.example/b", now - 50),
        )
        app.collector = await make_collector(db, http)
        await app.collector.collect()  # first run: stories are remembered (some queued as a sample)

        assert await app.send_latest(1, 5) == 2
        first = list(notifier.sent)
        assert sorted(first) == ["https://ru.example/a", "https://ru.example/b"]

        # Pressing the button again shows nothing, and regular delivery does not repeat them.
        assert await app.send_latest(1, 5) == 0
        await app.news_cycle()
        assert notifier.sent == first
        await db.close()

    run(scenario())


def test_feed_item_picture_is_kept():
    now = int(time.time())
    body = (
        '<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">'
        "<channel><title>t</title>"
        "<item><title>«Реал» продлил контракт</title><link>https://ru.example/a</link>"
        '<media:thumbnail url="https://img.example/small.jpg" width="240"/>'
        '<media:content url="https://img.example/big.jpg" medium="image" width="1200"/></item>'
        "<item><title>«Барселона» выиграла</title><link>https://ru.example/b</link>"
        '<enclosure url="https://img.example/enc.jpg" type="image/jpeg" length="1"/></item>'
        "<item><title>«Бавария» сыграла</title><link>https://ru.example/c</link>"
        "<description>&lt;p&gt;&lt;img src=\"/i/c.png\"&gt;Текст&lt;/p&gt;</description></item>"
        "<item><title>Без картинки</title><link>https://ru.example/d</link></item>"
        "</channel></rss>"
    ).encode()
    images = [c.image for c in parse_feed(RU, body, "application/rss+xml", now)]
    assert images == ["https://img.example/big.jpg", "https://img.example/enc.jpg",
                      "https://ru.example/i/c.png", ""]


def test_old_database_gets_the_image_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT NOT NULL, lang TEXT NOT NULL,"
                 " url TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',"
                 " categories TEXT NOT NULL DEFAULT '', entities TEXT NOT NULL DEFAULT '',"
                 " fingerprint TEXT NOT NULL DEFAULT '', relevant INTEGER NOT NULL DEFAULT 0,"
                 " status TEXT NOT NULL, dup_of TEXT, attempts INTEGER NOT NULL DEFAULT 0,"
                 " published_at INTEGER NOT NULL, fetched_at INTEGER NOT NULL, sent_at INTEGER)")
    conn.execute("INSERT INTO news(id, source, lang, url, title, status, published_at, fetched_at)"
                 " VALUES('x', 'ru1', 'ru', 'https://ru.example/x', 'Старая', 'sent', 1, 1)")
    conn.commit()
    conn.close()

    async def scenario():
        db = await Storage.open(path)
        record = await db.get_news("x")
        assert record is not None and record.title == "Старая" and record.image == ""
        await db.close()

    run(scenario())
