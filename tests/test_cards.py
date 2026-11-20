import asyncio
import io
from datetime import datetime, timezone

from PIL import Image

from bot.cardmaker import CardMaker, _minute_key, mentions_both
from bot.cards import CardEvent, CardTeam, ResultCard, render_card, to_jpeg, H, W
from bot.catalog import EPL
from bot.espn import Goal, Match, TeamScore
from bot.net import FetchError
from bot.storage import NewsRecord, NewsStatus, Storage


def png(color, size=(200, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, "PNG")
    return out.getvalue()


def test_render_card_with_and_without_pictures():
    full = ResultCard(
        home=CardTeam("Германия", png((0, 0, 0, 255), (300, 180)), "000000", "ffcc00"),
        away=CardTeam("Нидерланды", png((255, 100, 0, 255), (300, 180)), "ff6600", "ffffff"),
        home_score=5, away_score=0,
        home_events=[CardEvent(f"{m}'", "Вирц") for m in range(10, 90, 10)],  # 8 → merged
        league_logo=png((0, 80, 200, 255)), hero=png((90, 90, 90, 255), (1296, 729)),
        brand="FUTERA", note="пен. 4:3",
    )
    bare = ResultCard(home=CardTeam("Бавария"), away=CardTeam("Кёльн"), home_score=None,
                      away_score=None, away_events=[CardEvent("80'", "Райтц", "red")],
                      hero=b"not an image")
    for card in (full, bare):
        image = Image.open(io.BytesIO(render_card(card)))
        assert image.format == "JPEG" and image.size == (W, H)


def test_to_jpeg_rejects_tiny_and_broken_pictures():
    assert to_jpeg(png((10, 20, 30, 255), (800, 450)))[:2] == b"\xff\xd8"
    assert to_jpeg(png((10, 20, 30, 255), (50, 50))) is None
    assert to_jpeg(b"<html>") is None


def test_match_report_titles():
    assert mentions_both("Германия разгромила Нидерланды в Лиге наций", "Германия", "Нидерланды")
    assert mentions_both("«Бавария» не смогла обыграть «Кёльн»", "Бавария", "Кельн")
    assert not mentions_both("Германия объявила состав", "Германия", "Нидерланды")


def test_minute_order():
    assert sorted(["90'+2'", "12'", "45'+1'", "45'"], key=_minute_key) == \
        ["12'", "45'", "45'+1'", "90'+2'"]


class FakeHttp:
    def __init__(self, files):
        self.files = files
        self.requested: list[str] = []

    async def fetch_bytes(self, url, *, timeout=None):
        self.requested.append(url)
        if url not in self.files:
            raise FetchError("HTTP 404")
        return self.files[url]

    async def fetch_text(self, url, *, timeout=None):
        return '<meta property="og:image" content="https://img.example/report.jpg">'


class FakeWriter:
    def __init__(self):
        self.calls = 0

    async def russian_surnames(self, names):
        self.calls += 1
        return {"Florian Wirtz": "Вирц"}


def test_card_maker_end_to_end(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        kickoff = datetime.now(timezone.utc).replace(microsecond=0)
        match = Match(
            id="1", league_code="eng.1", kickoff=kickoff, state="post",
            status_name="STATUS_FULL_TIME", completed=True, clock="90'",
            home=TeamScore("10", "Germany", 1, None, True, logo="https://l.example/ger.png"),
            away=TeamScore("20", "Netherlands", 0, None, False, logo="https://l.example/ned.png"),
            goals=(Goal("60'", "Florian Wirtz", "10", False, False),
                   Goal("70'", "Jan Unknown", "10", False, False)),
            league_logo="https://l.example/missing.png",
        )
        now = int(kickoff.timestamp())
        await db.add_news([NewsRecord(
            id="r", source="sportsru", lang="ru", url="https://news.example/report",
            title="Германия обыграла Нидерланды", summary="", published_at=now + 7200,
            fetched_at=now + 7200, status=NewsStatus.SENT)])
        http = FakeHttp({"https://l.example/ger.png": png((0, 0, 0, 255)),
                         "https://l.example/ned.png": png((255, 100, 0, 255)),
                         "https://img.example/report.jpg": png((80, 80, 80, 255), (1200, 675))})
        writer = FakeWriter()
        maker = CardMaker(http, db, writer, brand="FUTERA")
        card = await maker.make(match, EPL)
        assert Image.open(io.BytesIO(card)).size == (W, H)
        assert "https://img.example/report.jpg" in http.requested  # the match photo was used
        assert await db.get_value("ru_surnames") == {"Florian Wirtz": "Вирц"}

        await maker.make(match, EPL, hero=False)  # logos come from the cache this time
        assert http.requested.count("https://l.example/ger.png") == 1
        assert writer.calls == 2  # «Jan Unknown» is retried, «Florian Wirtz» is cached
        await db.close()

    asyncio.run(scenario())
