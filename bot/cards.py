"""Branded match-result cards (Pillow) and photo helpers.

The card follows the channel's style: a photo of the match on top fading into a dark
background, the brand in the top-left corner, the tournament logo in the top-right one,
team logos with a big score in the middle, scorers with green minutes below and diagonal
stripes in the winner's colours in the bottom-right corner.

Everything here is synchronous and CPU-bound: call it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

FONTS = Path(__file__).resolve().parent / "assets" / "fonts"

W, H = 1200, 1320
HERO_H = 760
BG = (14, 14, 14)
WHITE = (255, 255, 255)
GREY = (170, 170, 170)
GREEN = (86, 224, 58)
SS = 3  # supersampling factor for shapes Pillow would otherwise draw without antialiasing

MAX_ROWS = 6


# ----------------------------------------------------------------------------- data

@dataclass(slots=True)
class CardTeam:
    name: str
    logo: bytes | None = None
    color: str | None = None  # hex «d02a3e»
    alt_color: str | None = None


@dataclass(slots=True)
class CardEvent:
    minute: str  # «41'», «90'+2'»
    player: str
    kind: str = "goal"  # "goal" | "pen" | "og" | "red"


@dataclass(slots=True)
class ResultCard:
    home: CardTeam
    away: CardTeam
    home_score: int | None
    away_score: int | None
    home_events: list[CardEvent] = field(default_factory=list)
    away_events: list[CardEvent] = field(default_factory=list)
    league_logo: bytes | None = None
    hero: bytes | None = None
    note: str = ""  # «пен. 4:3», «после доп. времени»
    brand: str = ""
    brand_logo: bytes | None = None

    @property
    def winner(self) -> CardTeam:
        if self.home_score is not None and self.away_score is not None \
                and self.away_score > self.home_score:
            return self.away
        return self.home


# ---------------------------------------------------------------------------- fonts

@lru_cache(maxsize=64)
def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONTS / name), size)
    try:
        font.set_variation_by_axes([weight])
    except (OSError, ValueError):  # pragma: no cover - static font or FreeType without variations
        pass
    return font


def score_font(size: int) -> ImageFont.FreeTypeFont:
    return _font("Montserrat.ttf", size, 900)


def brand_font(size: int) -> ImageFont.FreeTypeFont:
    return _font("Montserrat-Italic.ttf", size, 900)


def condensed(size: int, weight: int = 500) -> ImageFont.FreeTypeFont:
    return _font("RobotoCondensed.ttf", size, weight)


def _fit_font(text: str, max_width: int, size: int, minimum: int,
              make=condensed, **kwargs) -> ImageFont.FreeTypeFont:
    while size > minimum:
        font = make(size, **kwargs)
        if font.getlength(text) <= max_width:
            return font
        size -= 2
    return make(minimum, **kwargs)


# --------------------------------------------------------------------------- images

def _open(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:  # broken or unsupported file — the card is drawn without it
        return None
    image = ImageOps.exif_transpose(image)
    return image.convert("RGBA")


def to_jpeg(data: bytes, *, min_side: int = 300, max_side: int = 2048) -> bytes | None:
    """Re-encode any downloaded picture (webp, png…) as a JPEG Telegram accepts as a photo."""
    image = _open(data)
    if image is None or min(image.size) < min_side:
        return None
    image.thumbnail((max_side, max_side), Image.LANCZOS)
    background = Image.new("RGB", image.size, WHITE)
    background.paste(image, mask=image.getchannel("A"))
    out = io.BytesIO()
    background.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def _rgb(hex_color: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if not hex_color or len(hex_color) != 6:
        return default
    try:
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return default


def _too_dark(color: tuple[int, int, int]) -> bool:
    return sum(color) < 120


def _contain(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    image = image.copy()
    bbox = image.getchannel("A").getbbox()  # trim transparent padding around logos
    if bbox:
        image = image.crop(bbox)
    image.thumbnail(box, Image.LANCZOS)
    if image.width < box[0] and image.height < box[1]:
        scale = min(box[0] / image.width, box[1] / image.height)
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                             Image.LANCZOS)
    return image


def _cover(image: Image.Image, size: tuple[int, int], focus_y: float = 0.3) -> Image.Image:
    scale = max(size[0] / image.width, size[1] / image.height)
    resized = image.resize((int(image.width * scale) + 1, int(image.height * scale) + 1),
                           Image.LANCZOS)
    left = (resized.width - size[0]) // 2
    top = int((resized.height - size[1]) * focus_y)
    return resized.crop((left, top, left + size[0], top + size[1]))


def _with_opacity(image: Image.Image, opacity: float) -> Image.Image:
    image = image.copy()
    alpha = image.getchannel("A").point(lambda a: int(a * opacity))
    image.putalpha(alpha)
    return image


def _paste_center(canvas: Image.Image, image: Image.Image, center: tuple[int, int]) -> None:
    x, y = center[0] - image.width // 2, center[1] - image.height // 2
    canvas.alpha_composite(image, (max(0, x), max(0, y)),
                           (max(0, -x), max(0, -y)))


def _shadow(image: Image.Image, radius: int = 14, opacity: float = 0.75) -> Image.Image:
    """``image`` on a transparent canvas with a soft dark shadow behind it."""
    pad = radius * 3
    out = Image.new("RGBA", (image.width + pad * 2, image.height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    mask = image.getchannel("A").point(lambda a: int(a * opacity))
    shadow.paste((0, 0, 0, 255), (pad, pad + radius // 2), mask)
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius)))
    out.alpha_composite(image, (pad, pad))
    return out


# ----------------------------------------------------------------------- drawing

def _vertical_fade(size: tuple[int, int], start: int, end: int, color: tuple[int, int, int],
                   reverse: bool = False, max_alpha: int = 255) -> Image.Image:
    """A layer that goes from transparent at ``start`` to ``color`` at ``end``."""
    layer = Image.new("RGBA", size, color + (0,))
    mask = Image.new("L", (1, size[1]), 0)
    for y in range(size[1]):
        t = (y - start) / max(1, end - start)
        t = min(1.0, max(0.0, t))
        if reverse:
            t = 1 - t
        mask.putpixel((0, y), int(max_alpha * (t * t * (3 - 2 * t))))
    layer.putalpha(mask.resize(size))
    return layer


def _draw_hero(canvas: Image.Image, card: ResultCard) -> None:
    hero = _open(card.hero)
    winner = card.winner
    color = _rgb(winner.color, (60, 60, 60))
    if _too_dark(color):
        color = _rgb(winner.alt_color, (60, 60, 60))
    if hero is not None:
        photo = _cover(hero, (W, HERO_H)).convert("RGB")
        photo = ImageEnhance.Brightness(photo).enhance(0.88)
        canvas.paste(photo, (0, 0))
    else:
        glow = Image.new("RGBA", (W, HERO_H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((150, 40, 1050, 760), fill=color + (110,))
        canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(160)))
        logo = _open(winner.logo)
        if logo is not None:
            if _is_flag(logo):  # a big flag with soft edges, like fabric fading into the dark
                flag = _contain(logo, (760, 500))
                feather = Image.new("L", flag.size, 0)
                inset = min(flag.size) // 6
                ImageDraw.Draw(feather).rectangle(
                    (inset, inset, flag.width - inset, flag.height - inset), fill=255)
                feather = feather.filter(ImageFilter.GaussianBlur(inset * 0.8))
                flag.putalpha(Image.composite(flag.getchannel("A"), Image.new("L", flag.size, 0),
                                              feather))
                _paste_center(canvas, _with_opacity(flag, 0.30), (W // 2, 390))
            else:
                _paste_center(canvas, _with_opacity(_contain(logo, (560, 560)), 0.22),
                              (W // 2, 400))
    canvas.alpha_composite(_vertical_fade((W, HERO_H + 1), HERO_H - 380, HERO_H, BG))
    canvas.alpha_composite(_vertical_fade((W, 260), 0, 260, (0, 0, 0), reverse=True,
                                          max_alpha=150))


def _is_flag(image: Image.Image) -> bool:
    """National teams come as rectangular flags, clubs as crests."""
    bbox = image.getchannel("A").getbbox() or (0, 0, *image.size)
    return (bbox[2] - bbox[0]) > (bbox[3] - bbox[1]) * 1.2


def _stripe_colors(team: CardTeam) -> list[tuple[int, int, int]]:
    colors = [_rgb(team.color, (200, 200, 200)), _rgb(team.alt_color, (90, 90, 90))]
    if colors[1] == colors[0]:
        colors[1] = tuple(max(0, v - 90) for v in colors[0])  # type: ignore[assignment]
    return [(22, 22, 22)] + colors  # a dark first band, like the black of a flag


def _draw_stripes(canvas: Image.Image, card: ResultCard) -> None:
    """Brush-like diagonal stripes and a faded crest in the bottom-right corner."""
    winner = card.winner
    busy = bool(card.away_events)  # the right column of scorers runs over this corner
    logo = _open(winner.logo)
    if logo is not None and not _is_flag(logo) and not busy:
        crest = _contain(logo, (420, 420))
        gray = ImageOps.grayscale(crest.convert("RGB")).convert("RGBA")
        gray.putalpha(crest.getchannel("A").point(lambda a: int(a * 0.09)))
        _paste_center(canvas, gray, (960, 1140))

    rw, rh = 560, 470  # the corner region
    layer = Image.new("RGBA", (rw * SS, rh * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    start, end = (40.0, rh + 40.0), (rw + 40.0, 30.0)  # the first band's axis
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length  # unit normal pointing to the bottom-right
    if nx < 0:
        nx, ny = -nx, -ny
    band = 44.0
    for index, color in enumerate(_stripe_colors(winner)):
        a, b = index * band, (index + 1) * band
        points = [(start[0] + nx * a, start[1] + ny * a), (end[0] + nx * a, end[1] + ny * a),
                  (end[0] + nx * b, end[1] + ny * b), (start[0] + nx * b, start[1] + ny * b)]
        draw.polygon([(x * SS, y * SS) for x, y in points], fill=color + (240,))
    layer = layer.resize((rw, rh), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.8))
    if busy:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * 0.35)))
    canvas.alpha_composite(layer, (W - rw, H - rh))


def _draw_brand(canvas: Image.Image, card: ResultCard) -> None:
    logo = _open(card.brand_logo)
    if logo is not None:
        canvas.alpha_composite(_shadow(_contain(logo, (300, 80)), 10), (50 - 30, 40 - 30))
        return
    if not card.brand:
        return
    font = brand_font(64)
    text = card.brand.upper()
    layer = Image.new("RGBA", (W // 2, 160), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.text((50, 30), text, font=font, fill=WHITE)
    right = 50 + int(font.getlength(text))
    swoosh = Image.new("RGBA", (W // 2 * SS, 160 * SS), (0, 0, 0, 0))
    ImageDraw.Draw(swoosh).polygon(
        [(58 * SS, 112 * SS), (right * SS, 99 * SS), (right * SS, 103 * SS),
         (58 * SS, 117 * SS)], fill=GREEN + (255,))
    layer.alpha_composite(swoosh.resize(layer.size, Image.LANCZOS))
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 160), (0, 3), layer.getchannel("A"))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    canvas.alpha_composite(layer)


def _draw_league(canvas: Image.Image, card: ResultCard) -> None:
    logo = _open(card.league_logo)
    if logo is None:
        return
    image = _shadow(_contain(logo, (220, 150)), 10, 0.6)
    canvas.alpha_composite(image, (W - 50 - image.width + 30, 40 - 30))


def _team_logo(team: CardTeam, center: tuple[int, int], canvas: Image.Image) -> None:
    logo = _open(team.logo)
    if logo is None:
        size = 150 * SS
        badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        color = _rgb(team.color, (70, 70, 70))
        ImageDraw.Draw(badge).ellipse((0, 0, size - 1, size - 1), fill=color + (255,))
        badge = badge.resize((150, 150), Image.LANCZOS)
        initials = "".join(word[0] for word in team.name.split()[:2]).upper() or "?"
        font = condensed(64, 800)
        ImageDraw.Draw(badge).text((75, 75), initials, font=font, fill=WHITE, anchor="mm")
        logo = badge
    image = _shadow(_contain(logo, (250, 170)), 16, 0.8)
    _paste_center(canvas, image, center)


def _shadowed_text(canvas: Image.Image, xy: tuple[int, int], text: str,
                   font: ImageFont.FreeTypeFont, fill=WHITE, anchor: str = "la",
                   blur: int = 8, opacity: int = 170) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((xy[0], xy[1] + 4), text, font=font, fill=(0, 0, 0, opacity),
                               anchor=anchor)
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))
    ImageDraw.Draw(canvas).text(xy, text, font=font, fill=fill, anchor=anchor)


def _draw_score(canvas: Image.Image, card: ResultCard) -> None:
    home = "–" if card.home_score is None else str(card.home_score)
    away = "–" if card.away_score is None else str(card.away_score)
    _team_logo(card.home, (225, 840), canvas)
    _team_logo(card.away, (975, 840), canvas)
    font = _fit_font(f"{home}:{away}", 470, 220, 120, make=score_font)
    _shadowed_text(canvas, (W // 2, 845), f"{home}:{away}", font, anchor="mm", blur=14)

    for team, x in ((card.home, 225), (card.away, 975)):
        name = team.name.upper()
        font = _fit_font(name, 360, 48, 26, weight=800)
        _shadowed_text(canvas, (x, 975), name, font, anchor="mm", blur=6)

    line_w = 360
    line = Image.new("RGBA", (line_w * SS, 12 * SS), (0, 0, 0, 0))
    ImageDraw.Draw(line).polygon([(0, 8 * SS), (line_w * SS, 2 * SS), (line_w * SS, 5 * SS),
                                  (0, 11 * SS)], fill=GREEN + (255,))
    line = line.resize((line_w, 12), Image.LANCZOS)
    ramp = Image.linear_gradient("L").rotate(90).resize((line_w, 12))  # 0 → 255 left to right
    ends = ramp.point(lambda v: min(255, int(min(v, 255 - v) * 3)))
    line.putalpha(Image.composite(line.getchannel("A"), Image.new("L", line.size, 0), ends))
    canvas.alpha_composite(line, (W // 2 - line_w // 2, 952))
    if card.note:
        ImageDraw.Draw(canvas).text((W // 2, 990), card.note, font=condensed(30, 500),
                                    fill=GREY, anchor="mm")


@lru_cache(maxsize=4)
def _ball(size: int) -> Image.Image:
    big = size * 4
    ball = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ball)
    draw.ellipse((2, 2, big - 3, big - 3), fill=(245, 245, 245, 255), outline=(30, 30, 30, 255),
                 width=max(2, big // 18))
    c, r = big / 2, big * 0.17
    pentagon = [(c + r * math.cos(math.radians(-90 + 72 * i)),
                 c + r * math.sin(math.radians(-90 + 72 * i))) for i in range(5)]
    draw.polygon(pentagon, fill=(25, 25, 25, 255))
    for x, y in pentagon:  # seams from the central patch to the edge patches
        ex, ey = c + (x - c) * 2.5, c + (y - c) * 2.5
        draw.line((x, y, ex, ey), fill=(25, 25, 25, 255), width=max(2, big // 22))
        draw.ellipse((ex - r * 0.55, ey - r * 0.55, ex + r * 0.55, ey + r * 0.55),
                     fill=(25, 25, 25, 255))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, big - 3, big - 3), fill=255)
    ball.putalpha(Image.composite(ball.getchannel("A"), Image.new("L", (big, big), 0), mask))
    return ball.resize((size, size), Image.LANCZOS)


def _red_card(size: int) -> Image.Image:
    big = size * 4
    card = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle((big * 0.22, big * 0.05, big * 0.78, big * 0.95),
                                           radius=big // 10, fill=(225, 30, 40, 255))
    return card.resize((size, size), Image.LANCZOS)


def _rows(events: list[CardEvent]) -> list[tuple[str, str, str]]:
    """(icon kind, name, minutes); a player's goals are merged when the list is too long."""
    def label(event: CardEvent) -> str:
        suffix = {"pen": " (пен.)", "og": " (авт.)"}.get(event.kind, "")
        return event.player + suffix

    def minute(event: CardEvent) -> str:
        return event.minute.replace("'", "’")

    rows = [("red" if e.kind == "red" else "goal", label(e), minute(e)) for e in events]
    if len(rows) <= MAX_ROWS:
        return rows
    merged: dict[tuple[str, str], list[str]] = {}
    for kind, name, minutes in rows:
        merged.setdefault((kind, name), []).append(minutes)
    rows = [(kind, name, " ".join(minutes)) for (kind, name), minutes in merged.items()]
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS - 1] + [("none", f"и ещё {len(rows) - MAX_ROWS + 1}", "")]
    return rows


def _draw_events(canvas: Image.Image, card: ResultCard) -> None:
    top = 1065
    for events, left, limit in ((card.home_events, 90, 560), (card.away_events, 690, 1110)):
        rows = _rows(events)
        if not rows:
            continue
        step = min(48, (H - 40 - top) // len(rows))
        size = 38 if step >= 44 else 32
        name_x = left + size + 26
        minute_font = condensed(size, 800)
        widest_minutes = max(int(minute_font.getlength(m)) for _, _, m in rows)
        name_room = limit - name_x - widest_minutes - 30
        fonts = [_fit_font(name, name_room, size, 22) for _, name, _ in rows]
        widest_name = max(int(f.getlength(name)) for f, (_, name, _) in zip(fonts, rows))
        right = min(limit, name_x + widest_name + 40 + widest_minutes)  # minutes near the names
        for index, ((kind, name, minutes), font) in enumerate(zip(rows, fonts)):
            y = top + index * step
            if kind == "goal":
                canvas.alpha_composite(_ball(size), (left, y - size // 2))
            elif kind == "red":
                canvas.alpha_composite(_red_card(size), (left, y - size // 2))
            _shadowed_text(canvas, (name_x, y), name, font, anchor="lm", blur=4, opacity=120)
            if minutes:
                ImageDraw.Draw(canvas).text((right, y), minutes, font=minute_font, fill=GREEN,
                                            anchor="rm")


def render_card(card: ResultCard) -> bytes:
    canvas = Image.new("RGBA", (W, H), BG + (255,))
    _draw_hero(canvas, card)
    _draw_stripes(canvas, card)
    _draw_brand(canvas, card)
    _draw_league(canvas, card)
    _draw_score(canvas, card)
    _draw_events(canvas, card)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=92, optimize=True)
    return out.getvalue()
