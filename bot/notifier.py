"""Throttled, retrying delivery of messages to the bot's recipients."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError,
    TelegramRetryAfter, TelegramServerError, TelegramUnauthorizedError,
)
from aiogram.types import (
    BufferedInputFile, InlineKeyboardMarkup, InputFile, LinkPreviewOptions, Message,
)

from .access import Access

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 4


class DeliveryError(Exception):
    """Telegram is unreachable right now — try again later."""


class Notifier:
    def __init__(self, bot: Bot, access: Access, *, min_interval: float = 1.0) -> None:
        self._bot = bot
        self._access = access
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_sent = 0.0

    async def broadcast(self, text: str, *, keyboard: InlineKeyboardMarkup | None = None,
                        preview_url: str | None = None) -> bool:
        """Send to every recipient. True if at least one received it.

        Returns False when Telegram rejected the message for everyone; raises
        ``DeliveryError`` when it could not be delivered because Telegram is unreachable.
        """
        async def deliver(chat_id: int) -> bool:
            return await self.send(chat_id, text, keyboard=keyboard, preview_url=preview_url)

        return await self._broadcast(deliver)

    async def broadcast_photo(self, photo: bytes, caption: str | None = None, *,
                              keyboard: InlineKeyboardMarkup | None = None) -> bool:
        """Send a picture to every recipient (uploaded once, then reused by its file id)."""
        uploaded: list[str] = []

        async def deliver(chat_id: int) -> bool:
            source = uploaded[0] if uploaded else BufferedInputFile(photo, "card.jpg")
            message = await self.send_photo(chat_id, source, caption, keyboard=keyboard)
            if message is not None and message.photo and not uploaded:
                uploaded.append(message.photo[-1].file_id)
            return message is not None

        return await self._broadcast(deliver)

    async def _broadcast(self, deliver: Callable[[int], Awaitable[bool]]) -> bool:
        recipients = self._access.recipients
        if not recipients:
            return False
        delivered = False
        unreachable: DeliveryError | None = None
        for chat_id in recipients:
            try:
                delivered |= await deliver(chat_id)
            except DeliveryError as exc:
                unreachable = exc
        if not delivered and unreachable is not None:
            raise unreachable
        return delivered

    async def send(self, chat_id: int, text: str, *,
                   keyboard: InlineKeyboardMarkup | None = None,
                   preview_url: str | None = None) -> bool:
        state = {"preview": (LinkPreviewOptions(url=preview_url, prefer_large_media=True)
                             if preview_url else LinkPreviewOptions(is_disabled=True))}

        async def call(markup: InlineKeyboardMarkup | None) -> Message:
            return await self._bot.send_message(chat_id, text, reply_markup=markup,
                                                link_preview_options=state["preview"])

        def on_bad_request(message: str) -> bool:
            preview = state["preview"]
            if not preview.is_disabled and ("preview" in message or "webpage" in message):
                state["preview"] = LinkPreviewOptions(is_disabled=True)
                return True
            return False

        return await self._deliver(chat_id, call, keyboard, on_bad_request) is not None

    async def send_photo(self, chat_id: int, photo: InputFile | str, caption: str | None = None,
                         *, keyboard: InlineKeyboardMarkup | None = None) -> Message | None:
        async def call(markup: InlineKeyboardMarkup | None) -> Message:
            return await self._bot.send_photo(chat_id, photo, caption=caption,
                                              reply_markup=markup)

        return await self._deliver(chat_id, call, keyboard)

    async def _deliver(self, chat_id: int,
                       call: Callable[[InlineKeyboardMarkup | None], Awaitable[Message]],
                       keyboard: InlineKeyboardMarkup | None,
                       on_bad_request: Callable[[str], bool] | None = None) -> Message | None:
        """Run one Telegram call with throttling and retries.

        Returns the sent message, None when Telegram rejected it for good; raises
        ``DeliveryError`` when Telegram is unreachable.
        """
        async with self._lock:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                await self._throttle()
                try:
                    return await call(keyboard)
                except TelegramRetryAfter as exc:
                    log.warning("Telegram просит подождать %s с", exc.retry_after)
                    await asyncio.sleep(exc.retry_after + 1)
                except TelegramForbiddenError:
                    log.warning("Пользователь %s заблокировал бота — сообщение не доставлено",
                                chat_id)
                    return None
                except TelegramBadRequest as exc:
                    message = exc.message.lower()
                    if keyboard is not None and "button" in message:
                        log.warning("Telegram отклонил кнопки (%s) — отправляю без них",
                                    exc.message)
                        keyboard = None
                        continue
                    if on_bad_request is not None and on_bad_request(message):
                        continue
                    log.error("Telegram отклонил сообщение для %s: %s", chat_id, exc.message)
                    return None
                except TelegramUnauthorizedError as exc:
                    raise DeliveryError("токен бота отозван") from exc
                except (TelegramNetworkError, TelegramServerError) as exc:
                    if attempt == MAX_ATTEMPTS:
                        raise DeliveryError(str(exc)) from exc
                    await asyncio.sleep(2 * attempt)
                except TelegramAPIError as exc:
                    log.error("Ошибка Telegram для %s: %s", chat_id, exc)
                    return None
            raise DeliveryError("слишком много повторных попыток")

    async def _throttle(self) -> None:
        delay = self._last_sent + self._min_interval - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_sent = time.monotonic()
