import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from bot.catalog import EPL, UNL
from bot.espn import parse_scoreboard
from bot.render import render_day, render_news, render_result, split_message, team_name
from bot.storage import NewsRecord

FIXTURES = Path(__file__).parent / "fixtures"
ALLOWED_TAGS = {"b", "i", "a", "code"}


class _TagChecker(HTMLParser):
    """Telegram HTML must use only supported tags and keep them balanced."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        assert tag in ALLOWED_TAGS, tag
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, tag


def assert_valid_html(text: str) -> None:
    checker = _TagChecker()
    checker.feed(text)
    checker.close()
    assert not checker.stack
    assert len(text) <= 4096


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_own_goal_is_credited_to_benefiting_team():
    [match] = parse_scoreboard(load("espn_own_goal.json"), "eng.1")
    assert match.is_final and not match.on_penalties
    own_goals = [g for g in match.goals if g.own_goal]
    assert own_goals and match.team(own_goals[0].team_id).name == "Liverpool"

    text = render_result(match, EPL)
    assert_valid_html(text)
    assert "<b>Ливерпуль 2:0 Брайтон</b>" in text
    assert "(автогол) — Ливерпуль" in text
    assert "#АПЛ" in text and "#Ливерпуль" in text


def test_penalty_shootout():
    matches = {m.id: m for m in parse_scoreboard(load("espn_penalties.json"), "uefa.nations")}
    final = next(m for m in matches.values() if m.status_name == "STATUS_FINAL_PEN")
    assert final.on_penalties and final.is_final
    assert len(final.goals) == 4  # shootout kicks are not goals

    text = render_result(final, UNL)
    assert_valid_html(text)
    assert "Португалия 2:2 Испания" in text
    assert "Серия пенальти 5:3 — побеждает Португалия" in text


def test_parse_scoreboard_tolerates_garbage():
    assert parse_scoreboard(None, "eng.1") == []
    assert parse_scoreboard({"events": [{"id": 1}]}, "eng.1") == []


def test_team_name_translation():
    assert team_name("Atlético Madrid") == "Атлетико"
    assert team_name("Bayern Munich") == "Бавария"
    assert team_name("Türkiye") == "Турция"
    assert team_name("Some Unknown FC") == "Some Unknown FC"


def test_render_news_escapes_and_tags():
    record = NewsRecord(
        id="abc", source="sportsru", lang="ru", url="https://www.sports.ru/x",
        title="«Арсенал» <обыграл> & победил", summary="Счёт 2:1 <b>не тег</b>",
        published_at=int(datetime.now(timezone.utc).timestamp()), fetched_at=0,
        status="pending", categories=("epl", "stars"), entities=("Арсенал", "Сака"),
    )
    text = render_news(record, timezone.utc)
    assert_valid_html(text)
    assert "&lt;обыграл&gt; &amp; победил" in text
    assert "#АПЛ #Арсенал #Сака" in text
    assert "Sports.ru" in text


def test_render_day_and_split():
    matches = parse_scoreboard(load("espn_penalties.json"), "uefa.nations")
    day = matches[0].kickoff.date()
    chunks = render_day(day, [(UNL, matches)], timezone.utc)
    assert len(chunks) == 1
    assert_valid_html(chunks[0])
    assert "Португалия" in chunks[0]

    long_text = "\n\n".join(f"<b>Блок {i}</b>\n" + "строка\n" * 30 for i in range(100))
    parts = split_message(long_text, limit=1000)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= 1000
        assert_valid_html(part)
    assert "".join(parts).count("<b>") == 100


def test_render_day_uses_local_time():
    [match] = parse_scoreboard(load("espn_own_goal.json"), "eng.1")
    tz = timezone(timedelta(hours=5))
    pre = replace(match, state="pre", completed=False, status_name="STATUS_SCHEDULED")
    local = pre.kickoff.astimezone(tz).strftime("%H:%M")
    text = render_day(date(2026, 10, 25), [(EPL, [pre])], tz)[0]
    assert f"<code>{local}</code> Ливерпуль — Брайтон" in text
