from datetime import datetime, timezone
from types import SimpleNamespace

from bot.cards import CardEvent
from bot.catalog import UNL
from bot.espn import Match, TeamScore
from bot.rich import match_html, news_html, rich_message, sent_photo


def test_news_html_structure_and_escaping():
    post = ("❗️ «РЕАЛ» & «БАРСЕЛОНА» <СПОРЯТ>\n\nПервый абзац.\nВторая строка.\n\n"
            "«Короткая цитата» — Моуринью\n\n«" + "Очень длинная цитата. " * 15 + "»")
    html = news_html(post, signature="⚽️ FUTERA | Футбол\n📷 автор", with_photo=True,
                     credit="📷 Zafer / Wikimedia", source=("Чемпионат", "https://x.example/a?b=1&c=2"))
    assert html.startswith('<figure><img src="tg://photo?id=hero"><figcaption>📷 Zafer / Wikimedia'
                           "</figcaption></figure><h2>❗️ «РЕАЛ» &amp; «БАРСЕЛОНА» &lt;СПОРЯТ&gt;</h2>")
    assert "<p>Первый абзац.</p><p>Вторая строка.</p>" in html
    assert "<blockquote><p>«Короткая цитата»</p><cite>Моуринью</cite></blockquote>" in html
    assert "<blockquote expandable>«Очень длинная цитата." in html
    assert "<footer>⚽️ FUTERA | Футбол</footer><footer>📷 автор</footer>" in html
    assert 'url="https://x.example/a?b=1&amp;c=2"' in html and "🔗 Источник: Чемпионат" in html


def test_news_html_without_photo_or_source():
    html = news_html("ЗАГОЛОВОК\nТекст")
    assert html == "<h2>ЗАГОЛОВОК</h2><p>Текст</p>"


def test_match_html_has_a_goal_table_and_local_time():
    kickoff = datetime(2026, 11, 17, 19, 45, tzinfo=timezone.utc)
    match = Match(id="401861194", league_code="uefa.nations", kickoff=kickoff, state="post",
                  status_name="STATUS_FULL_TIME", completed=True, clock="90'",
                  home=TeamScore("1", "Denmark", 2, None, False),
                  away=TeamScore("2", "Norway", 3, None, True), venue="Parken")
    events = ([CardEvent("14'", "Хёйбьерг"), CardEvent("11'", "Хёйлунн")],
              [CardEvent("90'+6'", "Холанд", "pen"), CardEvent("50'", "Остигор", "red")])
    html = match_html("🇳🇴 НОРВЕГИЯ ВЫРВАЛА ПОБЕДУ — 3:2!\n\nТекст.", match, UNL, events,
                      tz=timezone.utc, signature="⚽️ FUTERA")
    assert "<caption>🌍 Лига наций · Дания 2:3 Норвегия</caption>" in html
    rows = html.split("<tr>")[2:]
    assert [r.split("</td>")[0].split(">")[-1] for r in rows] == ["11’", "14’", "50’", "90’+6’"]
    assert "🟥 Остигор" in rows[2] and "⚽ Холанд (пен.)" in rows[3]
    assert f'<tg-time unix="{int(kickoff.timestamp())}" format="wDt">17 ноября, 19:45</tg-time>' in html
    assert "🏟 Parken" in html
    assert 'url="https://www.espn.com/soccer/match/_/gameId/401861194" style="success"' in html


def test_rich_message_media_and_reading_the_photo_back():
    with_photo = rich_message("<p>x</p>", "file-id")
    assert with_photo.media[0].id == "hero" and with_photo.media[0].media.media == "file-id"
    assert rich_message("<p>x</p>", None).media is None

    caption = SimpleNamespace(text=["📷 Zafer", " / Wikimedia"])
    block = SimpleNamespace(type="photo", photo=[SimpleNamespace(file_id="small"),
                                                 SimpleNamespace(file_id="big")], caption=caption)
    message = SimpleNamespace(rich_message=SimpleNamespace(blocks=[block]))
    assert sent_photo(message) == ("big", "📷 Zafer / Wikimedia")
    assert sent_photo(SimpleNamespace(rich_message=None)) == (None, "")
