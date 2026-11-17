"""Telegram message texts (HTML parse mode). Every dynamic value is escaped here."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, tzinfo

from .catalog import CATEGORY_BY_KEY, ENTITY_BY_NAME, EXTRA_TEAM_NAMES, CATEGORIES, Category
from .espn import Match, TeamScore
from .sources import SOURCE_BY_KEY
from .storage import NewsRecord
from .textutil import esc, fold, hashtag, truncate

MESSAGE_LIMIT = 4000  # Telegram allows 4096; keep a margin for entities
NEWS_SUMMARY_LIMIT = 700

_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря")


def _team_key(name: str) -> str:
    return "".join(ch for ch in fold(name) if ch.isalnum())


_TEAM_NAMES: dict[str, str] = {_team_key(en): ru for en, ru in EXTRA_TEAM_NAMES.items()}
for _category in CATEGORIES:
    for _entity in _category.entities:
        for _espn_name in _entity.espn:
            _TEAM_NAMES[_team_key(_espn_name)] = _entity.name


def team_name(name: str) -> str:
    """Russian name of a team reported by ESPN (falls back to the original)."""
    return _TEAM_NAMES.get(_team_key(name), name)


def format_day(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]}"


def format_time(ts: float, tz: tzinfo, *, now: datetime | None = None) -> str:
    moment = datetime.fromtimestamp(ts, tz)
    now = now or datetime.now(tz)
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    return moment.strftime("%d.%m %H:%M")


def _unique(items: Iterable[str | None]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split on blank lines, then on lines, so that HTML tags are never cut in half."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        pieces = [block] if len(block) <= limit else block.split("\n")
        for index, piece in enumerate(pieces):
            if len(piece) > limit:  # a single gigantic line — never produced by our renderers
                piece = piece[:limit - 1] + "…"
            sep = "\n\n" if index == 0 else "\n"
            candidate = f"{current}{sep}{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


# ------------------------------------------------------------------------- news

def render_news(record: NewsRecord, tz: tzinfo) -> str:
    categories = [CATEGORY_BY_KEY[k] for k in record.categories if k in CATEGORY_BY_KEY]
    source = SOURCE_BY_KEY.get(record.source)
    meta = [f"{c.emoji} {c.name}" for c in categories[:2]]
    meta.append(source.name if source else record.source)
    meta.append(format_time(record.published_at, tz))

    lines = [f"<i>{esc(' · '.join(meta))}</i>", "", f"<b>{esc(record.title)}</b>"]
    if record.summary:
        lines += ["", esc(truncate(record.summary, NEWS_SUMMARY_LIMIT))]

    tags = _unique([hashtag(c.tag) if c.tag else None for c in categories[:2]] + [
        hashtag(ENTITY_BY_NAME[name].tag or name) if name in ENTITY_BY_NAME else None
        for name in record.entities[:4]
    ])
    if tags:
        lines += ["", " ".join(tags)]
    return "\n".join(lines)


# ---------------------------------------------------------------------- matches

def _score(team: TeamScore) -> str:
    return "–" if team.score is None else str(team.score)


def render_result(match: Match, league: Category) -> str:
    home, away = team_name(match.home.name), team_name(match.away.name)
    lines = [
        f"🏁 <b>{esc(league.name)}</b> · финальный счёт",
        "",
        f"<b>{esc(home)} {_score(match.home)}:{_score(match.away)} {esc(away)}</b>",
    ]
    if match.on_penalties and match.home.shootout is not None and match.away.shootout is not None:
        winner = home if match.home.shootout > match.away.shootout else away
        lines.append(f"Серия пенальти {match.home.shootout}:{match.away.shootout} — "
                     f"побеждает {esc(winner)}")
    elif match.after_extra_time:
        lines.append("После дополнительного времени")
    if match.note:
        lines.append(f"<i>{esc(match.note)}</i>")

    def side(team_id: str) -> str:
        team = match.team(team_id)
        return team_name(team.name) if team else ""

    if match.goals:
        lines += ["", "⚽ <b>Голы</b>"]
        for goal in match.goals:
            mark = " (пен.)" if goal.penalty else " (автогол)" if goal.own_goal else ""
            team = side(goal.team_id)
            suffix = f" — {esc(team)}" if team else ""
            lines.append(f"{esc(goal.minute)} {esc(goal.player)}{mark}{suffix}".strip())
    if match.red_cards:
        lines += ["", "🟥 <b>Удаления</b>"]
        for card in match.red_cards:
            team = side(card.team_id)
            suffix = f" — {esc(team)}" if team else ""
            lines.append(f"{esc(card.minute)} {esc(card.player)}{suffix}".strip())
    if match.venue:
        lines += ["", f"🏟 {esc(match.venue)}"]

    tags = _unique([hashtag(league.tag) if league.tag else None, hashtag(home), hashtag(away)])
    if tags:
        lines += ["", " ".join(tags)]
    return "\n".join(lines)


def _match_line(match: Match, tz: tzinfo) -> str:
    home, away = esc(team_name(match.home.name)), esc(team_name(match.away.name))
    if match.is_final:
        return f"✅ {home} {_score(match.home)}:{_score(match.away)} {away}"
    if match.is_live:
        clock = esc(match.clock) if match.clock else "live"
        return f"🔴 <code>{clock}</code> {home} {_score(match.home)}:{_score(match.away)} {away}"
    if match.is_cancelled:
        return f"⏸ {home} — {away} (перенесён)"
    kickoff = match.kickoff.astimezone(tz).strftime("%H:%M")
    return f"<code>{kickoff}</code> {home} — {away}"


def render_day(day: date, groups: Sequence[tuple[Category, Sequence[Match]]], tz: tzinfo,
               *, title: str = "Матчи") -> list[str]:
    blocks = [f"📅 <b>{esc(title)} {format_day(day)}</b>"]
    for league, matches in groups:
        rows = [f"{league.emoji} <b>{esc(league.name)}</b>"]
        rows += [_match_line(m, tz) for m in matches]
        blocks.append("\n".join(rows))
    return split_message("\n\n".join(blocks))
