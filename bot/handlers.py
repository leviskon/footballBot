"""Telegram handlers: the main menu (reply keyboard), inline buttons and slash commands.

The menu buttons are the main interface. Slash commands stay as hidden aliases
(e.g. ``/latest 10``) but are not shown in Telegram's command menu.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, Filter, or_f
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

from . import texts
from .catalog import ALL_CATEGORY_KEYS, CATEGORY_BY_KEY
from .keyboards import (
    REMOVE_MENU, Btn, NewsAction, SettingsAction, main_menu, news_keyboard, settings_keyboard,
)
from .notifier import DeliveryError
from .render import render_day, render_news
from .sources import LANGUAGE_NAMES, LANGUAGES
from .textutil import esc

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

LATEST_DEFAULT = 5
LATEST_MAX = 20


class AdminFilter(Filter):
    async def __call__(self, event: Message | CallbackQuery, app: App) -> bool:
        user = event.from_user
        return user is not None and app.access.is_admin(user.id)


# ------------------------------------------------------------------- helpers

async def _menu(app: App) -> ReplyKeyboardMarkup:
    return main_menu(paused=(await app.settings.get()).paused)


async def _edit_markup(message: Message, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await message.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in exc.message.lower():
            log.warning("Не удалось обновить кнопки: %s", exc.message)


# -------------------------------------------------------------- menu actions

async def cmd_start(message: Message, app: App) -> None:
    user = message.from_user
    if user is None:
        return
    if app.access.is_admin(user.id):
        await message.answer(texts.welcome(app), reply_markup=await _menu(app))
        return
    if await app.access.claim(user.id):
        log.info("Владелец бота: %s (id %s)", user.full_name, user.id)
        await message.answer(texts.welcome(app, first_time=True), reply_markup=await _menu(app))
        app.wake()
        return
    log.info("Отказано в доступе: %s (id %s)", user.full_name, user.id)
    await message.answer(texts.private_bot(user.id), reply_markup=REMOVE_MENU)


async def show_help(message: Message, app: App) -> None:
    await message.answer(texts.welcome(app), reply_markup=await _menu(app))


async def show_status(message: Message, app: App) -> None:
    await message.answer(await texts.status(app), reply_markup=await _menu(app))


async def show_settings(message: Message, app: App) -> None:
    settings = await app.settings.get()
    await message.answer(texts.SETTINGS_INTRO, reply_markup=settings_keyboard(settings))


async def run_check(message: Message, app: App) -> None:
    if (await app.settings.get()).paused:
        await message.answer(texts.paused_notice(), reply_markup=await _menu(app))
        return
    progress = await message.answer("🔄 Проверяю источники…")
    try:
        report = await app.news_cycle()
        results = await app.results_cycle(force=True)
    except Exception:
        log.exception("Ошибка при ручной проверке")
        await message.answer("⚠️ Проверка прервалась из-за ошибки. Подробности — в logs/bot.log",
                             reply_markup=await _menu(app))
        return
    finally:
        try:
            await progress.delete()
        except TelegramBadRequest:
            pass
    await message.answer(texts.check_done(report, results), reply_markup=await _menu(app))


async def _send_latest(message: Message, app: App, count: int) -> None:
    settings = await app.settings.get()
    records = await app.db.latest_news(count, settings.categories)
    if not records:
        await message.answer("Пока нечего показать — новости появятся после первой проверки. "
                             "Запустить её сейчас: «🔄 Проверить сейчас».",
                             reply_markup=await _menu(app))
        return
    try:
        for record in reversed(records):  # oldest first, so the newest ends up at the bottom
            await app.notifier.send(message.chat.id, render_news(record, app.config.tz),
                                    keyboard=news_keyboard(record.id, record.url),
                                    preview_url=record.url)
    except DeliveryError as exc:
        log.warning("Не удалось отправить последние новости: %s", exc)


async def show_latest(message: Message, app: App) -> None:
    await _send_latest(message, app, LATEST_DEFAULT)


async def cmd_latest(message: Message, command: CommandObject, app: App) -> None:
    """``/latest N`` — the same as the button, but with a custom number of news."""
    count = LATEST_DEFAULT
    if command.args:
        arg = command.args.strip()
        if not arg.isdigit() or not 1 <= int(arg) <= LATEST_MAX:
            await message.answer(f"Укажи число от 1 до {LATEST_MAX}, например: /latest 10",
                                 reply_markup=await _menu(app))
            return
        count = int(arg)
    await _send_latest(message, app, count)


async def show_today(message: Message, app: App) -> None:
    day, groups, failed = await app.matches.day_overview()
    if groups:
        chunks = render_day(day, groups, app.config.tz)
    elif failed:
        chunks = ["⚠️ Не удалось загрузить расписание. Попробуй чуть позже."]
    else:
        chunks = ["📅 Сегодня матчей отслеживаемых турниров нет."]
    if groups and failed:
        chunks.append(f"⚠️ Не загрузились: {esc(', '.join(failed))}")
    menu = await _menu(app)
    for chunk in chunks:
        await message.answer(chunk, reply_markup=menu)


async def do_pause(message: Message, app: App) -> None:
    if (await app.settings.get()).paused:
        await message.answer("⏸ Бот уже на паузе.", reply_markup=main_menu(paused=True))
        return
    await app.pause()
    await message.answer("⏸ Пауза. Новости и итоги матчей не собираются.\n"
                         "Продолжить — кнопка «▶️ Продолжить».",
                         reply_markup=main_menu(paused=True))


async def do_resume(message: Message, app: App) -> None:
    if not (await app.settings.get()).paused:
        await message.answer("▶️ Бот и так работает.", reply_markup=main_menu(paused=False))
        return
    await app.resume()
    await message.answer("▶️ Продолжаю. Всё, что вышло во время паузы, пропущу — "
                         "дальше только свежие новости.", reply_markup=main_menu(paused=False))


async def unknown_message(message: Message, app: App) -> None:
    await message.answer("Пользуйся кнопками внизу экрана 👇", reply_markup=await _menu(app))


# ----------------------------------------------------------- inline buttons

async def on_news_action(callback: CallbackQuery, callback_data: NewsAction, app: App) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer("Сообщение слишком старое", show_alert=True)
        return
    if callback_data.action == "take":
        record = await app.db.get_news(callback_data.item)
        url = record.url if record else _first_url(message)
        await _edit_markup(message, news_keyboard(callback_data.item, url, taken=True)
                           if url else None)
        await callback.answer("Отмечено ✅")
    elif callback_data.action == "del":
        try:
            await message.delete()
            await callback.answer("Удалено")
        except TelegramBadRequest:
            await _edit_markup(message, None)
            await callback.answer("Telegram не даёт боту удалять сообщения старше 48 часов. "
                                  "Кнопки убрал — удали вручную.", show_alert=True)
    else:
        await callback.answer()


def _first_url(message: Message) -> str | None:
    markup = message.reply_markup
    if markup and markup.inline_keyboard and markup.inline_keyboard[0]:
        return markup.inline_keyboard[0][0].url
    return None


async def on_settings_action(callback: CallbackQuery, callback_data: SettingsAction,
                             app: App) -> None:
    message = callback.message
    kind, value = callback_data.kind, callback_data.value
    if kind == "close":
        if isinstance(message, Message):
            await _edit_markup(message, None)
        await callback.answer("Сохранено")
        return

    if kind == "cat" and value in ALL_CATEGORY_KEYS:
        settings = await app.settings.update(
            lambda s: s.categories.symmetric_difference_update({value}))
        enabled = value in settings.categories
        if enabled:
            await app.matches.forget_league(value)
        label = CATEGORY_BY_KEY[value].name
    elif kind == "lang" and value in LANGUAGES:
        settings = await app.settings.update(lambda s: s.langs.symmetric_difference_update({value}))
        enabled = value in settings.langs
        if enabled:
            await app.collector.forget_sources(lang=value)
        label = f"{LANGUAGE_NAMES[value].capitalize()} источники"
    elif kind == "flag" and value in ("results", "digest"):
        settings = await app.settings.update(lambda s: setattr(s, value, not getattr(s, value)))
        enabled = getattr(settings, value)
        label = "Итоги матчей" if value == "results" else "Анонс дня"
    else:
        await callback.answer()
        return

    if isinstance(message, Message):
        await _edit_markup(message, settings_keyboard(settings))
    await callback.answer(f"{label}: {'включено' if enabled else 'выключено'}")


# -------------------------------------------------------------------- router

def build_router() -> Router:
    router = Router(name="public")
    router.message.register(cmd_start, CommandStart())

    admin = Router(name="admin")
    admin.message.filter(AdminFilter())
    admin.callback_query.filter(AdminFilter())

    # (handler, menu button, hidden slash-command alias)
    menu = (
        (show_today, Btn.TODAY, "today"),
        (run_check, Btn.CHECK, "check"),
        (show_status, Btn.STATUS, "status"),
        (show_settings, Btn.SETTINGS, "settings"),
        (do_pause, Btn.PAUSE, "pause"),
        (do_resume, Btn.RESUME, "resume"),
        (show_help, Btn.HELP, "help"),
    )
    admin.message.register(show_latest, F.text == Btn.LATEST)
    admin.message.register(cmd_latest, Command("latest"))
    for handler, button, command in menu:
        admin.message.register(handler, or_f(F.text == button, Command(command)))
    admin.message.register(unknown_message, F.text)

    admin.callback_query.register(on_news_action, NewsAction.filter())
    admin.callback_query.register(on_settings_action, SettingsAction.filter())

    router.include_router(admin)
    return router
