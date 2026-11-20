"""Channel posts as Telegram rich messages (Bot API 10.1+).

A rich message is one message with real structure: a photo block, a big heading, paragraphs,
quotes (long ones collapsed), a table, the channel signature as a footer and coloured
buttons. The LLM writes plain text; it is turned into rich HTML here.

News post:   photo (with the licence credit, if any) → heading → paragraphs/quotes → footer
             (→ «🔗 Источник» button, if switched on in .env).
Match post:  result card → heading → paragraphs → table of goals and red cards → date
             (shown in every reader's own time zone) and stadium → footer → «📊 Статистика».
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Sequence
from datetime import datetime, tzinfo

from aiogram.types import InputMediaPhoto, InputRichMessage, InputRichMessageMedia, Message

from .cards import CardEvent
from .catalog import Category
from .espn import Match
from .render import format_day, team_name
from .textutil import esc

PHOTO_ID = "hero"
LONG_QUOTE = 250  # quotes longer than this are collapsed («expandable»)
# Rich messages have no margins: an empty line is a paragraph with a no-break space (an
# empty <p> is dropped by Telegram).
SPACER = "<p>&nbsp;</p>"
ESPN_MATCH_URL = "https://www.espn.com/soccer/match/_/gameId/{id}"

# «Текст цитаты» — Автор   /   "Quote" - Author
_QUOTE_RE = re.compile(r"^[«\"“](?P<text>.+)[»\"”](?:\s*[—–-]\s*(?P<credit>.{2,80}))?$", re.DOTALL)


def _attr(value: str) -> str:
    return html_lib.escape(value, quote=True)


def _split(post: str) -> tuple[str, list[list[str]]]:
    """Headline and paragraphs (each a list of lines) of a plain-text post."""
    headline, _, body = post.strip().partition("\n")
    paragraphs = [[line.strip() for line in chunk.split("\n") if line.strip()]
                  for chunk in re.split(r"\n\s*\n", body.strip())]
    return headline.strip(), [p for p in paragraphs if p]


def _paragraph(lines: list[str]) -> str:
    text = " ".join(lines)
    quote = _QUOTE_RE.match(text)
    if quote:
        body = f"«{esc(quote['text'].strip())}»"
        credit = f"<cite>{esc(quote['credit'].strip())}</cite>" if quote["credit"] else ""
        if len(text) > LONG_QUOTE:  # a collapsed quote holds plain text, not paragraphs
            return f"<blockquote expandable>{body}{credit}</blockquote>"
        return f"<blockquote><p>{body}</p>{credit}</blockquote>"
    return "".join(f"<p>{esc(line)}</p>" for line in lines)


def _photo(with_photo: bool, credit: str) -> str:
    if not with_photo:
        return ""
    image = f'<img src="tg://photo?id={PHOTO_ID}">'
    if credit:
        return f"<figure>{image}<figcaption>{esc(credit)}</figcaption></figure>"
    return image


def _footer(signature: str) -> str:
    """The channel signature, set apart from the text by an empty line."""
    lines = [f"<footer>{esc(line)}</footer>" for line in signature.splitlines() if line.strip()]
    return SPACER + "".join(lines) if lines else ""


def _heading(headline: str) -> str:
    """The headline (Telegram's own margin under a heading is enough: the channel owner
    found any extra empty line too big)."""
    return f"<h2>{esc(headline)}</h2>"


def _buttons(buttons: Sequence[tuple[str, str, str | None]]) -> str:
    """``(text, url, style)`` → a centered row of link buttons."""
    if not buttons:
        return ""
    row = "".join(
        f'<tg-button url="{_attr(url)}"' + (f' style="{style}"' if style else "") + f">{esc(text)}"
        "</tg-button>" for text, url, style in buttons)
    return f'<tg-button-row align="center">{row}</tg-button-row>'


def news_html(post: str, *, signature: str = "", with_photo: bool = False, credit: str = "",
              source: tuple[str, str] | None = None) -> str:
    """A news post. ``source`` is ``(site name, article url)`` for the «🔗» button."""
    headline, paragraphs = _split(post)
    parts = [_photo(with_photo, credit), _heading(headline)]
    parts += [_paragraph(lines) for lines in paragraphs]
    parts.append(_footer(signature))
    if source:
        parts.append(_buttons([(f"🔗 Источник: {source[0]}", source[1], None)]))
    return "".join(parts)


def _event_row(event: CardEvent, team: str) -> str:
    mark = {"pen": " (пен.)", "og": " (авт.)"}.get(event.kind, "")
    icon = "🟥" if event.kind == "red" else "⚽"
    minute = event.minute.replace("'", "’")
    return (f'<tr><td align="center">{esc(minute)}</td><td>{icon} {esc(event.player + mark)}</td>'
            f"<td>{esc(team)}</td></tr>")


def match_html(post: str, match: Match, league: Category,
               events: tuple[list[CardEvent], list[CardEvent]], *, tz: tzinfo,
               signature: str = "", with_photo: bool = True) -> str:
    """A match-result post with a table of goals and red cards."""
    headline, paragraphs = _split(post)
    parts = [_photo(with_photo, ""), _heading(headline)]
    parts += [_paragraph(lines) for lines in paragraphs]

    home, away = team_name(match.home.name), team_name(match.away.name)
    rows = sorted([(e, home) for e in events[0]] + [(e, away) for e in events[1]],
                  key=lambda item: _minute_order(item[0].minute))
    if rows:
        parts.append(
            "<table bordered striped compact>"
            f"<caption>{esc(league.emoji)} {esc(league.name)} · {esc(home)} "
            f"{_score(match.home.score)}:{_score(match.away.score)} {esc(away)}</caption>"
            '<tr><th align="center">Мин</th><th>Событие</th><th>Команда</th></tr>'
            + "".join(_event_row(event, team) for event, team in rows) + "</table>")

    kickoff = int(match.kickoff.timestamp())
    local = datetime.fromtimestamp(kickoff, tz)
    fallback = f"{format_day(local.date())}, {local:%H:%M}"
    info = f'🗓 <tg-time unix="{kickoff}" format="wDt">{esc(fallback)}</tg-time>'
    if match.venue:
        info += f" · 🏟 {esc(match.venue)}"
    parts.append(f"<p>{info}</p>")
    parts.append(_footer(signature))
    parts.append(_buttons([("📊 Статистика матча", ESPN_MATCH_URL.format(id=match.id), "success")]))
    return "".join(parts)


def _score(value: int | None) -> str:
    return "–" if value is None else str(value)


def _minute_order(minute: str) -> float:
    digits = [int(p) for p in "".join(c if c.isdigit() else " " for c in minute).split()]
    return digits[0] + (digits[1] / 100 if len(digits) > 1 else 0) if digits else 999.0


def rich_message(html: str, photo: object | None) -> InputRichMessage:
    """``photo`` is a BufferedInputFile, a Telegram file id or None."""
    media = [InputRichMessageMedia(id=PHOTO_ID, media=InputMediaPhoto(media=photo))] \
        if photo is not None else None
    return InputRichMessage(html=html, media=media)


# ------------------------------------------------------------- reading back

def _plain(rich_text: object) -> str:
    """Text of a RichText value (a string, a list or a typed object with ``text``)."""
    if rich_text is None:
        return ""
    if isinstance(rich_text, str):
        return rich_text
    if isinstance(rich_text, (list, tuple)):
        return "".join(_plain(part) for part in rich_text)
    return _plain(getattr(rich_text, "text", ""))


def sent_photo(message: Message) -> tuple[str | None, str]:
    """``(file id, credit)`` of the photo block of a sent rich post, to keep them when the
    text is rewritten («🔄 Другой вариант»)."""
    rich = message.rich_message
    for block in (rich.blocks if rich else None) or []:
        if getattr(block, "type", None) == "photo" and getattr(block, "photo", None):
            caption = getattr(block, "caption", None)
            return block.photo[-1].file_id, _plain(getattr(caption, "text", None))
    return None, ""
