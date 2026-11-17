"""Who may use the bot and receives the news.

If ADMIN_IDS is set in .env, only those users are admins. Otherwise the first person who
sends /start becomes the owner (stored in the database) and the bot is closed to others.
"""

from __future__ import annotations

import asyncio

from .storage import Storage

_OWNER_KEY = "owner_id"


class Access:
    def __init__(self, db: Storage, static_ids: frozenset[int]) -> None:
        self._db = db
        self._static = static_ids
        self._owner: int | None = None
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        owner = await self._db.get_value(_OWNER_KEY)
        self._owner = owner if isinstance(owner, int) else None

    @property
    def recipients(self) -> tuple[int, ...]:
        if self._static:
            return tuple(sorted(self._static))
        return (self._owner,) if self._owner is not None else ()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.recipients

    @property
    def can_claim(self) -> bool:
        return not self._static and self._owner is None

    async def claim(self, user_id: int) -> bool:
        """Make ``user_id`` the owner if nobody owns the bot yet."""
        async with self._lock:
            if not self.can_claim:
                return False
            await self._db.set_value(_OWNER_KEY, user_id)
            self._owner = user_id
            return True
