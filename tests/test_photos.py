import asyncio
import io

import pytest
from PIL import Image

from bot.net import FetchError
from bot.photos import PhotoFinder, title_person
from bot.storage import NewsRecord, NewsStatus, Storage


def jpeg_source(size=(900, 600)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (40, 90, 30)).save(out, "PNG")
    return out.getvalue()


@pytest.mark.parametrize("title, person", [
    ("Жозе Моуринью: у нас сильный состав", "Жозе Моуринью"),
    ("Лангович — о Батракове: гимн Лиги чемпионов", "Лангович"),
    ("Источник: Головин пропустит матч", None),
    ("Романо: «Реал» договорился с Вальверде", None),
    ("Бавария: Кейн готов к матчу", None),
    ("«Реал» объявил о продлении контракта", None),
    ("Головин пропустит еще 2-3 недели", None),
])
def test_title_person(title, person):
    assert title_person(title) == person


def story(news_id, source, title, *, entities=(), image="", offset=0):
    return NewsRecord(id=news_id, source=source, lang="ru", url=f"https://{source}.example/{news_id}",
                      title=title, summary="", published_at=1_000_000 + offset, fetched_at=0,
                      status=NewsStatus.SENT, entities=tuple(entities), relevant=True,
                      image=image)


class FakeHttp:
    def __init__(self, files=None, json_answers=None):
        self.files = files or {}
        self.json_answers = json_answers or []
        self.requested: list[str] = []

    async def fetch_bytes(self, url, *, timeout=None, headers=None):
        self.requested.append(url)
        if url not in self.files:
            raise FetchError("HTTP 404")
        return self.files[url]

    async def fetch_text(self, url, *, timeout=None):
        raise FetchError("HTTP 403")  # pages are blocked, only feed pictures work

    async def fetch_json(self, url, params=None, *, headers=None):
        self.requested.append(f"{url}?{params.get('gsrsearch') or params.get('titles')}")
        return self.json_answers.pop(0)


def test_photo_of_another_story_about_the_same_person(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        record = story("c", "championat", "Жозе Моуринью: у нас сильный состав",
                       entities=("Моуринью", "Реал Мадрид"))
        await db.add_news([
            record,
            story("r", "sportsru", "«Реал» обыграл «Севилью»", entities=("Реал Мадрид",),
                  image="https://img.example/real.jpg", offset=-600),
            story("m", "matchtv", "Моуринью ответил на критику", entities=("Моуринью",),
                  image="https://img.example/mou.jpg", offset=-3600),
        ])
        http = FakeHttp({"https://img.example/mou.jpg": jpeg_source(),
                         "https://img.example/real.jpg": jpeg_source()})
        picture = await PhotoFinder(http, db).for_news(record, ())
        assert picture is not None and picture.credit == ""
        assert http.requested == ["https://img.example/mou.jpg"]  # the person before the club
        await db.close()

    asyncio.run(scenario())


def test_wikipedia_photo_with_credit(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        record = story("c", "championat", "Лангович — о Батракове: гимн Лиги чемпионов")
        await db.add_news([record])
        http = FakeHttp(
            files={"https://upload.example/thumb.jpg": jpeg_source()},
            json_answers=[
                {"query": {"pages": {
                    "2": {"index": 2, "title": "Сборная России по футболу"},
                    "1": {"index": 1, "title": "Лангович, Андрей Владимирович",
                          "pageimage": "Andrey_Langovich_2021.jpg"}}}},
                {"query": {"pages": {"-1": {"imageinfo": [{
                    "thumburl": "https://upload.example/thumb.jpg",
                    "extmetadata": {"Artist": {"value": '<a href="//x">Ivan Petrov</a>'},
                                    "LicenseShortName": {"value": "CC BY-SA 3.0"}}}]}}}},
            ])
        finder = PhotoFinder(http, db)
        picture = await finder.for_news(record, ())
        assert picture is not None
        assert picture.credit == "📷 Ivan Petrov / Wikimedia Commons, CC BY-SA 3.0"
        assert any("ru.wikipedia.org" in r and "Лангович футбол" in r for r in http.requested)
        assert await finder.wikipedia(record, ["Лангович"]) == picture  # cached, no requests
        await db.close()

    asyncio.run(scenario())


def test_no_picture_anywhere(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        record = story("c", "championat", "Головин пропустит еще 2-3 недели")
        await db.add_news([record])
        assert await PhotoFinder(FakeHttp(), db).for_news(record, ("https://img.example/x.jpg",)) \
            is None
        await db.close()

    asyncio.run(scenario())


def test_title_person_goes_before_other_stars(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        record = story("c", "sportexpress", "Данилов: «Мечтаю закрыть Мбаппе»",
                       entities=("Мбаппе",))
        await db.add_news([record, story("m", "sportsru", "Мбаппе забил дважды",
                                         entities=("Мбаппе",), image="https://img.example/m.jpg")])
        http = FakeHttp(
            files={"https://img.example/m.jpg": jpeg_source(),
                   "https://upload.example/d.jpg": jpeg_source()},
            json_answers=[
                {"query": {"pages": {"1": {"index": 1, "title": "Данилов, Игорь",
                                           "pageimage": "Danilov.jpg"}}}},
                {"query": {"pages": {"-1": {"imageinfo": [{
                    "thumburl": "https://upload.example/d.jpg", "extmetadata": {}}]}}}},
            ])
        picture = await PhotoFinder(http, db).for_news(record, ())
        assert picture is not None and "Wikimedia" in picture.credit
        assert "https://img.example/m.jpg" not in http.requested
        await db.close()

    asyncio.run(scenario())


def test_blocked_site_is_skipped_next_time(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        posts = [story(f"p{i}", "sportsru", f"Моуринью: ответ номер {i}", entities=("Моуринью",),
                       offset=60 * i) for i in range(3)]
        blocked = story("x", "championat", "Моуринью о Винисиусе", entities=("Моуринью",),
                        image="https://championat.example/1x1.gif", offset=-60)
        await db.add_news([*posts, blocked])
        http = FakeHttp(files={"https://championat.example/1x1.gif": b"GIF89a"},
                        json_answers=[{"query": {"pages": {}}}] * 6)
        finder = PhotoFinder(http, db)
        for post in posts:
            await finder.for_news(post, ())
        # the 1×1 picture and the blocked page fail on the first post: the host is left alone
        assert http.requested.count("https://championat.example/1x1.gif") == 1
        await db.close()

    asyncio.run(scenario())


def test_one_glitch_does_not_block_a_host(tmp_path):
    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        finder = PhotoFinder(FakeHttp(), db)
        url = "https://www.sports.ru/a.jpg"
        finder._host_failed(url)
        assert finder._host_ok(url)
        finder._host_failed(url)
        assert not finder._host_ok(url) and finder._host_ok("https://other.example/b.jpg")
        await db.close()

    asyncio.run(scenario())


def test_article_page_picture_comes_first(tmp_path):
    class PageHttp(FakeHttp):
        async def fetch_text(self, url, *, timeout=None):
            return '<meta property="og:image" content="https://img.example/page.jpg">'

    async def scenario():
        db = await Storage.open(tmp_path / "t.sqlite3")
        record = story("a", "sportsru", "«Реал» продлил контракт", image="https://img.example/rss.jpg")
        http = PageHttp({"https://img.example/page.jpg": jpeg_source(),
                         "https://img.example/rss.jpg": jpeg_source()})
        assert await PhotoFinder(http, db).for_news(record) is not None
        assert http.requested == ["https://img.example/page.jpg"]
        await db.close()

    asyncio.run(scenario())
