"""Telegram message texts (HTML parse mode). Every dynamic value is escaped here."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

from .catalog import CATEGORY_BY_KEY, ENTITY_BY_NAME, EXTRA_TEAM_NAMES, CATEGORIES, Category
from .espn import Match, TeamScore
from .sources import SOURCE_BY_KEY
from .storage import NewsRecord
from .textutil import esc, fold, hashtag, truncate

MESSAGE_LIMIT = 4000  # Telegram allows 4096; keep a margin for entities
CAPTION_LIMIT = 1024  # photo captions, counted in UTF-16 code units after HTML parsing
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


# ------------------------------------------------------------------------ posts

#
# The model writes plain text with two marks (see ``writer.STYLE``):
#   **ключевой факт**          — bold;
#   > «Цитата»  /  > — Автор    — a quote and who said it.
# A whole paragraph in «quotes» followed by «— Автор» is a quote too (older prompt).

LONG_QUOTE = 250  # quotes longer than this are collapsed («expandable»)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_DASHES = "—–-"
_OPEN_QUOTES, _CLOSE_QUOTES = "«\"“„", "»\"”"
# «Текст цитаты» — Автор   /   "Quote" - Author
_QUOTE_RE = re.compile(r"^[«\"“](?P<text>.+)[»\"”](?:\s*[—–-]\s*(?P<credit>.{2,80}))?$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Block:
    """A paragraph of a post (each line is shown on its own line) or a quote."""

    lines: tuple[str, ...]
    quote: bool = False
    credit: str = ""

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def strip_marks(text: str) -> str:
    return text.replace("**", "")


def inline_html(text: str) -> str:
    """Escape a line and turn ``**…**`` into bold (unpaired marks are dropped)."""
    return strip_marks(_BOLD_RE.sub(r"<b>\1</b>", esc(text)))


def _unquote(text: str) -> str:
    """«Цитата» → Цитата (only when the outer marks are a pair: not «Реал» … «Барсы»)."""
    if len(text) > 2 and text[0] in _OPEN_QUOTES and text[-1] in _CLOSE_QUOTES:
        inner = text[1:-1].strip()
        depth = 0
        for char in inner:
            depth += (char == "«") - (char == "»")
            if depth < 0:
                return text
        if depth == 0 and inner.count('"') % 2 == 0:
            return inner
    return text


def _quote_block(lines: list[str]) -> Block:
    credit = ""
    if len(lines) > 1 and lines[-1][0] in _DASHES:
        credit = lines.pop().lstrip(_DASHES + " ").strip()
    text = _unquote(strip_marks(" ".join(lines)).strip())
    text = text.replace("«", "„").replace("»", "“")  # quotes inside the «quote»
    return Block((text,), quote=True, credit=strip_marks(credit))


def _text_block(lines: list[str]) -> Block:
    old_style = _QUOTE_RE.match(" ".join(lines))
    if old_style:
        return Block((strip_marks(old_style["text"].strip()),), quote=True,
                     credit=strip_marks((old_style["credit"] or "").strip()))
    return Block(tuple(lines))


def parse_post(post: str) -> tuple[str, list[Block]]:
    """Headline (without marks) and blocks of a written post."""
    headline, _, body = post.strip().partition("\n")
    blocks: list[Block] = []
    lines: list[str] = []
    quote: list[str] | None = None

    def close() -> None:
        nonlocal lines, quote
        if quote:
            blocks.append(_quote_block(quote))
        elif lines:
            blocks.append(_text_block(lines))
        lines, quote = [], None

    for raw in body.split("\n"):
        line = raw.strip()
        if not line:
            close()
        elif line.startswith(">"):
            if quote is None:
                close()
                quote = []
            text = line.lstrip("> ").strip()
            if text:
                quote.append(text)
        elif quote and line[0] in _DASHES and quote[-1][0] not in _DASHES:
            quote.append(line)  # «— Автор» written right under the quote without «>»
        else:
            if quote is not None:
                close()
            lines.append(line)
    close()
    return strip_marks(headline).strip(), blocks


def _block_html(block: Block) -> str:
    """A block in Telegram's ordinary HTML (photo captions, text messages)."""
    if block.quote:
        tag = "blockquote expandable" if len(block.text) > LONG_QUOTE else "blockquote"
        credit = f"\n— {esc(block.credit)}" if block.credit else ""
        return f"<{tag}>«{esc(block.text)}»{credit}</blockquote>"
    return "\n".join(inline_html(line) for line in block.lines)


def render_post(post: str, signature: str = "") -> str:
    """A written channel post: the headline in bold, bold facts and quotes, the channel
    signature at the end."""
    headline, blocks = parse_post(post)
    text = f"<b>{esc(headline)}</b>"
    if blocks:
        text += "\n\n" + "\n\n".join(_block_html(block) for block in blocks)
    if signature.strip():
        text += f"\n\n\n{esc(signature.strip())}"
    return truncate(text, MESSAGE_LIMIT) if len(text) > MESSAGE_LIMIT else text


def tg_len(text: str) -> int:
    """Length as Telegram counts it (UTF-16 code units: most emoji take two)."""
    return len(text.encode("utf-16-le")) // 2


def plain_len(html_text: str) -> int:
    return tg_len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


def fit_post(post: str, signature: str = "", limit: int = CAPTION_LIMIT) -> str:
    """:func:`render_post` that fits a photo caption: trailing paragraphs are dropped first,
    then the remaining text is shortened at a sentence boundary."""
    headline, _, body = post.strip().partition("\n")
    blank = body.startswith("\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body.strip("\n")) if p.strip()]
    tail = f"\n\n\n{signature.strip()}" if signature.strip() else ""

    def assemble(parts: list[str]) -> str:
        if not parts:
            return headline
        return headline + ("\n\n" if blank else "\n") + "\n\n".join(parts)

    while len(paragraphs) > 1 and tg_len(assemble(paragraphs) + tail) > limit:
        paragraphs.pop()
    if paragraphs and tg_len(assemble(paragraphs) + tail) > limit:
        room = limit - tg_len(assemble([]) + tail) - 4
        while room > 20:
            shortened = truncate(paragraphs[0], room)
            if tg_len(assemble([shortened]) + tail) <= limit:
                paragraphs = [shortened]
                break
            room -= 20
        else:
            paragraphs = []
    return render_post(assemble(paragraphs), signature)
