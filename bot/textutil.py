"""Text helpers: HTML → plain text, accent folding, URL normalisation, title similarity."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# ---------------------------------------------------------------- HTML → plain text

_BLOCK_TAGS = frozenset({
    "p", "br", "div", "li", "ul", "ol", "tr", "table", "blockquote", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr",
})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "iframe", "figcaption"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


_INLINE_WS_RE = re.compile(r"[ \t\r\f\v    ​﻿]+")


def clean_whitespace(text: str) -> str:
    """Collapse runs of spaces, trim every line and drop empty lines."""
    lines = (_INLINE_WS_RE.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def html_to_text(raw: str | None) -> str:
    """Convert an HTML fragment from a feed into readable plain text."""
    if not raw:
        return ""
    if "<" not in raw and "&" not in raw:
        return clean_whitespace(raw)
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # pragma: no cover - HTMLParser is very lenient; this is a safety net
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return clean_whitespace(text)


_TRAILING_JUNK = (
    re.compile(r"\s*the post .{1,300}? appeared first on .{1,120}$", re.IGNORECASE | re.DOTALL),
    re.compile(
        r"\s*(читать (дальше|далее|полностью)|подробнее|read more|continue reading)"
        r"\s*(\.\.\.|…|→|»|>>)?\s*$",
        re.IGNORECASE,
    ),
)
_TRAILING_ELLIPSIS_RE = re.compile(r"\s*\[(…|\.\.\.)\]\s*$")


def strip_feed_junk(text: str) -> str:
    """Remove «Read more», «The post … appeared first on …» and similar feed tails."""
    previous = None
    while previous != text:
        previous = text
        for rx in _TRAILING_JUNK:
            text = rx.sub("", text).rstrip()
    return _TRAILING_ELLIPSIS_RE.sub("…", text)


_SENTENCE_END_RE = re.compile(r"[.!?…](?=\s)|\n")


def truncate(text: str, limit: int) -> str:
    """Shorten text to ``limit`` characters, preferring a sentence boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    floor = int(limit * 0.6)
    best = -1
    for match in _SENTENCE_END_RE.finditer(cut):
        if match.end() >= floor:
            best = match.end()
    if best != -1:
        return cut[:best].rstrip()
    space = cut.rfind(" ")
    if space >= floor:
        cut = cut[:space]
    return cut.rstrip(" ,;:—–-") + "…"


# ------------------------------------------------------------------- normalisation

def strip_accents(text: str) -> str:
    """Remove diacritics while keeping case: «Ёлкин» → «Елкин», «Atlético» → «Atletico»."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def fold(text: str) -> str:
    """Case- and accent-insensitive form of a string."""
    return strip_accents(text).casefold()


# ----------------------------------------------------------------------------- URLs

_TRACKING_PARAMS = frozenset({"from", "ref", "rss", "yclid", "gclid", "fbclid", "_openstat"})
_TRACKING_PREFIXES = ("utm_", "at_", "mc_")


def is_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def clean_url(url: str) -> str:
    """Drop tracking parameters and the fragment; percent-encode non-ASCII characters."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = quote(parts.path, safe="/%:@!$&'()*+,;=~-._")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def url_key(url: str) -> str:
    """Identity of an article URL: scheme, «www.», trailing slash and trackers ignored."""
    parts = urlsplit(clean_url(url))
    host = parts.netloc
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    path = parts.path.rstrip("/") or "/"
    return f"{host}{path}?{parts.query}" if parts.query else f"{host}{path}"


def make_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ title similarity

_TOKEN_RE = re.compile(r"\d+[:\-–]\d+|\w+")
_STOPWORDS = frozenset({
    # Russian
    "и", "в", "во", "на", "с", "со", "по", "за", "из", "от", "до", "для", "не", "ни", "что",
    "как", "это", "его", "ее", "их", "он", "она", "они", "мы", "вы", "я", "к", "ко", "о", "об",
    "у", "а", "но", "или", "же", "ли", "бы", "при", "про", "над", "под", "без", "после",
    "перед", "между", "уже", "еще", "также", "так", "все", "был", "была", "были", "будет",
    "может", "свой", "своих", "который", "которая", "которые", "этот", "эта", "этом", "чем",
    "заявил", "заявила", "заявили", "сообщил", "сообщила", "сообщает", "сообщили",
    "рассказал", "рассказала", "считает", "отметил", "назвал", "назвала", "оценил",
    "высказался", "прокомментировал", "ответил", "признался", "источник", "инсайдер",
    # English
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "from",
    "as", "is", "are", "was", "were", "be", "has", "have", "had", "after", "before", "over",
    "his", "her", "their", "its", "it", "that", "this", "says", "said", "will", "could",
    "would", "new", "latest", "how", "why", "what", "who", "into", "vs",
})


_NAME_MARK = "^"
_QUOTES = "«\"„“'"
# Common Russian inflection endings (in accent-stripped form: «ый» → «ыи»), longest first.
_RU_ENDINGS = tuple(sorted({strip_accents(ending) for ending in (
    "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "иях", "ах", "ях", "ой", "ей",
    "ом", "ем", "ам", "ям", "ую", "юю", "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий",
    "ов", "ев", "а", "я", "о", "е", "у", "ю", "ы", "и", "ь",
)}, key=len, reverse=True))


def _stem(token: str) -> str:
    if not token.isascii():
        for ending in _RU_ENDINGS:
            if token.endswith(ending) and len(token) - len(ending) >= 3:
                token = token[: -len(ending)]
                break
    return token[:5]


def title_fingerprint(title: str) -> frozenset[str]:
    """Normalised word stems of a title, used to spot one story told by different outlets.

    Stems of proper names (capitalised words that do not merely start the sentence) are
    added a second time with a ``^`` prefix: shared names are the strongest signal.
    """
    text = strip_accents(title)
    tokens: set[str] = set()
    for index, match in enumerate(_TOKEN_RE.finditer(text)):
        raw = match.group()
        token = raw.casefold()
        if token[0].isdigit() and any(sep in token for sep in ":-–"):
            tokens.add(re.sub(r"[\-–]", ":", token))  # score: 2-1 == 2:1
            continue
        if token in _STOPWORDS or len(token) < 3:
            continue
        stem = _stem(token)
        tokens.add(stem)
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end():match.end() + 1]
        if raw[0].isupper() and (index > 0 or before in _QUOTES or after == ":"):
            tokens.add(_NAME_MARK + stem)
    return frozenset(tokens)


def _split(fingerprint: frozenset[str]) -> tuple[set[str], set[str]]:
    words = {t for t in fingerprint if not t.startswith(_NAME_MARK)}
    names = {t[1:] for t in fingerprint if t.startswith(_NAME_MARK)}
    return words, names


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of the words of two fingerprints (0 when a title is too short)."""
    words_a, _ = _split(a)
    words_b, _ = _split(b)
    if len(words_a) < 3 or len(words_b) < 3:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def same_story(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether two titles most likely describe the same news."""
    words_a, names_a = _split(a)
    words_b, names_b = _split(b)
    if len(words_a) < 3 or len(words_b) < 3:
        return False
    common = words_a & words_b
    if len(common) / len(words_a | words_b) >= 0.5:
        return True
    return (len(names_a & names_b) >= 3
            and len(common) / min(len(words_a), len(words_b)) >= 0.45)


# ------------------------------------------------------------------- Telegram HTML

def esc(text: str) -> str:
    return html.escape(text, quote=False)


def hashtag(name: str) -> str | None:
    body = re.sub(r"[^\w]", "", name)
    if not body or body[0].isdigit():
        return None
    return "#" + body
