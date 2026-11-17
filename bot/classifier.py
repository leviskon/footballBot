"""Keyword classifier: maps a news item to tournaments, clubs and star players."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .catalog import CATEGORIES, EXCLUDE_ALIASES, HARD_EXCLUDE_ALIASES, Category, Entity
from .textutil import strip_accents

_OPENING_QUOTES = "«\"„“"
_LITERAL_SPLIT_RE = re.compile(r"([\s\-]+|\*)")
_SEPARATOR_RE = re.compile(r"[\s\-]+")


def compile_alias(alias: str) -> str:
    """Translate one alias of the catalogue mini-language into a regex fragment.

    The fragment carries its own flags, so fragments with different case rules can be
    joined into one pattern with ``|``.
    """
    case_sensitive = quoted = raw = False
    rest = alias
    while True:
        if rest.startswith("="):
            case_sensitive, rest = True, rest[1:]
        elif rest.startswith("q:"):
            quoted, rest = True, rest[2:]
        elif rest.startswith("re:"):
            raw, rest = True, rest[3:]
            break
        else:
            break
    if not rest:
        raise ValueError(f"empty alias: {alias!r}")

    if raw:
        body = strip_accents(rest)
    else:
        pieces = []
        for chunk in _LITERAL_SPLIT_RE.split(strip_accents(rest)):
            if not chunk:
                continue
            if chunk == "*":
                pieces.append(r"\w*")
            elif _SEPARATOR_RE.fullmatch(chunk):
                pieces.append(r"[\s\-]+")
            else:
                pieces.append(re.escape(chunk))
        body = "".join(pieces)

    left = f"(?<=[{_OPENING_QUOTES}])" if quoted else r"(?<!\w)"
    fragment = f"{left}(?:{body})(?!\\w)"
    return f"(?:{fragment})" if case_sensitive else f"(?i:{fragment})"


def compile_aliases(aliases: Iterable[str]) -> re.Pattern[str] | None:
    fragments = [compile_alias(alias) for alias in aliases]
    return re.compile("|".join(fragments)) if fragments else None


@dataclass(frozen=True, slots=True)
class Classification:
    categories: tuple[str, ...]  # ordered: the most prominent mention first
    entities: tuple[str, ...]  # matched club/player names, in order of appearance
    excluded: bool  # mentions Russian football, MLS, etc. (matters only for «Прочее»)
    hard_excluded: bool  # women's / youth football — never sent


@dataclass(frozen=True, slots=True)
class _CompiledEntity:
    entity: Entity
    category: str
    pattern: re.Pattern[str]
    unless: re.Pattern[str] | None


class Classifier:
    def __init__(self, categories: Iterable[Category] = CATEGORIES) -> None:
        self._keywords: list[tuple[str, re.Pattern[str]]] = []
        self._entities: list[_CompiledEntity] = []
        for category in categories:
            keywords = compile_aliases(category.keywords)
            if keywords is not None:
                self._keywords.append((category.key, keywords))
            for entity in category.entities:
                pattern = compile_aliases(entity.aliases)
                if pattern is None:
                    raise ValueError(f"entity without aliases: {entity.name}")
                self._entities.append(
                    _CompiledEntity(entity, category.key, pattern, compile_aliases(entity.unless))
                )
        self._exclude = compile_aliases(EXCLUDE_ALIASES)
        self._hard_exclude = compile_aliases(HARD_EXCLUDE_ALIASES)

    def classify(self, title: str, body: str = "", *, preset: str | None = None) -> Classification:
        """Classify a news item. ``preset`` is a category forced by the source itself."""
        title_n = strip_accents(title)
        text = f"{title_n}\n{strip_accents(body)}"

        first_hit: dict[str, int] = {}
        if preset:
            first_hit[preset] = -1
        for key, pattern in self._keywords:
            match = pattern.search(text)
            if match and match.start() < first_hit.get(key, len(text) + 1):
                first_hit[key] = match.start()

        found: list[tuple[int, str]] = []
        for compiled in self._entities:
            match = compiled.pattern.search(text)
            if not match:
                continue
            if compiled.unless is not None and compiled.unless.search(text):
                continue
            found.append((match.start(), compiled.entity.name))
            if match.start() < first_hit.get(compiled.category, len(text) + 1):
                first_hit[compiled.category] = match.start()

        found.sort()
        entities = tuple(dict.fromkeys(name for _, name in found))
        categories = tuple(sorted(first_hit, key=lambda key: first_hit[key]))
        excluded = bool(self._exclude and self._exclude.search(text))
        hard_excluded = bool(self._hard_exclude and self._hard_exclude.search(title_n))
        return Classification(categories, entities, excluded, hard_excluded)
