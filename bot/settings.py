"""User-adjustable settings (changed via the «⚙️ Настройки» menu, persisted in SQLite)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .catalog import ALL_CATEGORY_KEYS, DEFAULT_CATEGORY_KEYS
from .sources import LANGUAGES
from .storage import Storage

_SETTINGS_KEY = "settings"


@dataclass(slots=True)
class BotSettings:
    categories: set[str] = field(default_factory=lambda: set(DEFAULT_CATEGORY_KEYS))
    langs: set[str] = field(default_factory=lambda: {"ru"})
    results: bool = True
    digest: bool = True
    paused: bool = False

    def copy(self) -> BotSettings:
        return BotSettings(set(self.categories), set(self.langs), self.results, self.digest,
                           self.paused)

    def to_json(self) -> dict[str, Any]:
        return {
            "categories": sorted(self.categories),
            "langs": sorted(self.langs),
            "results": self.results,
            "digest": self.digest,
            "paused": self.paused,
        }

    @classmethod
    def from_json(cls, data: Any) -> BotSettings:
        settings = cls()
        if not isinstance(data, dict):
            return settings
        if isinstance(data.get("categories"), list):
            settings.categories = {k for k in data["categories"] if k in ALL_CATEGORY_KEYS}
        if isinstance(data.get("langs"), list):
            settings.langs = {k for k in data["langs"] if k in LANGUAGES}
        for name in ("results", "digest", "paused"):
            if isinstance(data.get(name), bool):
                setattr(settings, name, data[name])
        return settings


class SettingsStore:
    def __init__(self, db: Storage) -> None:
        self._db = db
        self._cache: BotSettings | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> BotSettings:
        if self._cache is None:
            self._cache = BotSettings.from_json(await self._db.get_value(_SETTINGS_KEY))
        return self._cache.copy()

    async def update(self, change: Callable[[BotSettings], None]) -> BotSettings:
        async with self._lock:
            settings = await self.get()
            change(settings)
            await self._db.set_value(_SETTINGS_KEY, settings.to_json())
            self._cache = settings
            return settings.copy()
