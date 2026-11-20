"""Publishing ready posts to the channel («📢 В канал» under a written post).

The post message is copied as it is (photo, caption, formatting) without the bot's buttons.
The bot must be an administrator of the channel with the «Post messages» right.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

log = logging.getLogger(__name__)


class Channel:
    def __init__(self, bot: Bot, chat_id: int | str) -> None:
        self._bot = bot
        self.chat_id = chat_id  # «@username» or the numeric id «-100…»
        self.username: str | None = chat_id[1:] if isinstance(chat_id, str) else None
        self.title: str | None = None

    @property
    def name(self) -> str:
        if self.title:
            return f"«{self.title}»" + (f" (@{self.username})" if self.username else "")
        return f"@{self.username}" if self.username else str(self.chat_id)

    async def check(self) -> str | None:
        """Look the channel up and check the bot's rights. Returns a problem or None."""
        try:
            chat = await self._bot.get_chat(self.chat_id)
            self.chat_id, self.title = chat.id, chat.title
            self.username = chat.username or self.username
            me = await self._bot.get_me()
            member = await self._bot.get_chat_member(chat.id, me.id)
        except TelegramAPIError as exc:
            return (f"канал {self.chat_id} не найден или бот в нём не состоит ({exc}). "
                    "Проверь CHANNEL_ID в .env и что бот добавлен в администраторы")
        if member.status == "creator":
            return None
        if member.status != "administrator":
            return "бот не администратор канала — добавь его в администраторы"
        if not getattr(member, "can_post_messages", False):
            return "у бота нет права «Публикация сообщений» — включи его в настройках канала"
        return None

    async def publish(self, from_chat_id: int, message_id: int) -> int:
        """Copy a post into the channel. Returns the id of the channel message."""
        copied = await self._bot.copy_message(self.chat_id, from_chat_id, message_id)
        return copied.message_id

    def link(self, message_id: int) -> str | None:
        """A link to a channel message (for private channels it opens for members only)."""
        if self.username:
            return f"https://t.me/{self.username}/{message_id}"
        chat_id = str(self.chat_id)
        if chat_id.startswith("-100"):
            return f"https://t.me/c/{chat_id[4:]}/{message_id}"
        return None
