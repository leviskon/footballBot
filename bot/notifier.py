"""Throttled, retrying delivery of messages to the bot's recipients."""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError,
    TelegramRetryAfter, TelegramServerError, TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

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
        recipients = self._access.recipients
        if not recipients:
            return False
        delivered = False
        unreachable: DeliveryError | None = None
        for chat_id in recipients:
            try:
                delivered |= await self.send(chat_id, text, keyboard=keyboard,
                                             preview_url=preview_url)
            except DeliveryError as exc:
                unreachable = exc
        if not delivered and unreachable is not None:
            raise unreachable
        return delivered

    async def send(self, chat_id: int, text: str, *,
                   keyboard: InlineKeyboardMarkup | None = None,
                   preview_url: str | None = None) -> bool:
        preview = (LinkPreviewOptions(url=preview_url, prefer_large_media=True)
                   if preview_url else LinkPreviewOptions(is_disabled=True))
        async with self._lock:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                await self._throttle()
                try:
                    await self._bot.send_message(chat_id, text, reply_markup=keyboard,
                                                 link_preview_options=preview)
                    return True
                except TelegramRetryAfter as exc:
                    log.warning("Telegram просит подождать %s с", exc.retry_after)
                    await asyncio.sleep(exc.retry_after + 1)
                except TelegramForbiddenError:
                    log.warning("Пользователь %s заблокировал бота — сообщение не доставлено",
                                chat_id)
                    return False
                except TelegramBadRequest as exc:
                    message = exc.message.lower()
                    if keyboard is not None and "button" in message:
                        log.warning("Telegram отклонил кнопки (%s) — отправляю без них",
                                    exc.message)
                        keyboard = None
                        continue
                    if not preview.is_disabled and ("preview" in message or "webpage" in message):
                        preview = LinkPreviewOptions(is_disabled=True)
                        continue
                    log.error("Telegram отклонил сообщение для %s: %s", chat_id, exc.message)
                    return False
                except TelegramUnauthorizedError as exc:
                    raise DeliveryError("токен бота отозван") from exc
                except (TelegramNetworkError, TelegramServerError) as exc:
                    if attempt == MAX_ATTEMPTS:
                        raise DeliveryError(str(exc)) from exc
                    await asyncio.sleep(2 * attempt)
                except TelegramAPIError as exc:
                    log.error("Ошибка Telegram для %s: %s", chat_id, exc)
                    return False
            raise DeliveryError("слишком много повторных попыток")

    async def _throttle(self) -> None:
        delay = self._last_sent + self._min_interval - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_sent = time.monotonic()
