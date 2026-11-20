"""Telegram handlers: the main menu (reply keyboard), inline buttons and slash commands.

The menu buttons are the main interface. Slash commands stay as hidden aliases
(e.g. ``/latest 10``) but are not shown in Telegram's command menu.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart, Filter, or_f
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions, Message,
    ReplyKeyboardMarkup,
)

from . import texts
from .catalog import ALL_CATEGORY_KEYS, CATEGORY_BY_KEY
from .keyboards import (
    REMOVE_MENU, Btn, NewsAction, SettingsAction, main_menu, post_keyboard, published_keyboard,
    settings_keyboard,
)
from .notifier import DeliveryError
from .render import fit_post, render_day, render_post, render_result
from .sources import LANGUAGE_NAMES, LANGUAGES
from .storage import NewsRecord
from .textutil import esc, html_to_text
from .writer import LLMError, Post

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

LATEST_DEFAULT = 5
LATEST_MAX = 20
POST_ACTIONS = frozenset({"post", "mpost", "repost", "rempost"})
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Messages a post is being written for right now (a second tap must not start another one).
_writing: set[tuple[int, int]] = set()
# Posts being copied to the channel right now (a double tap must not publish twice).
_publishing: set[tuple[int, int]] = set()


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
    try:
        sent = await app.send_latest(message.chat.id, count)
    except DeliveryError as exc:
        log.warning("Не удалось отправить последние новости: %s", exc)
        return
    if not sent:
        await message.answer("Новых новостей нет — всё свежее я уже присылал. "
                             "Следующие придут сами, как только выйдут.",
                             reply_markup=await _menu(app))


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
    origin = message.forward_origin
    if origin is not None and getattr(origin, "type", None) == "channel":
        # a post forwarded from a channel: tell its id, needed for CHANNEL_ID
        chat = origin.chat
        value = f"@{chat.username}" if chat.username else str(chat.id)
        await message.answer(
            f"📢 Канал «{esc(chat.title or '')}»\nID: <code>{chat.id}</code>\n\n"
            f"Чтобы бот публиковал в него посты, добавь в .env строку:\n"
            f"<code>CHANNEL_ID={esc(value)}</code>", reply_markup=await _menu(app))
        return
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
        await _edit_markup(message, app.news_keyboard(callback_data.item, url, taken=True)
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
    elif callback_data.action in POST_ACTIONS:
        await _write_post(callback, message, callback_data, app)
    elif callback_data.action == "pub":
        await _publish(callback, message, app)
    else:
        await callback.answer()


async def _write_post(callback: CallbackQuery, message: Message, data: NewsAction,
                      app: App) -> None:
    """«✍️ Сделать пост» / «🔄 Другой вариант»: a ready channel post with a picture.

    News posts get the article's photo, match posts the branded result card. The first tap
    answers the source message with a placeholder that is replaced by the post; «another
    version» rewrites the text of the post in place and keeps its picture.
    """
    writer = app.writer
    if writer is None:
        await callback.answer("Нейросеть не подключена: добавь GEMINI_TOKEN или GROQ_TOKEN "
                              "в .env и перезапусти бота.", show_alert=True)
        return
    key = (message.chat.id, message.message_id)
    if key in _writing:
        await callback.answer("Уже пишу, подожди немного ✍️")
        return

    action, item = data.action, data.item
    regenerate = action in ("repost", "rempost")
    news_id = match_id = None
    match_info = None
    record: NewsRecord | None = None
    if action in ("post", "repost"):
        news_id = item
        record = await app.db.get_news(item)
        if record is None:
            await callback.answer("Эта новость уже удалена из базы (старше 14 дней).",
                                  show_alert=True)
            return

        async def write() -> Post:
            return await writer.news_post(record)
    elif item != "0":
        match_id = item
        await callback.answer("✍️ Пишу пост…")
        match_info = await app.matches.find_match(item)
        if match_info is None:
            await message.answer("⚠️ Не нашёл этот матч у ESPN — попробуй позже.")
            return
        result_text = html_to_text(render_result(*match_info))

        async def write() -> Post:
            return Post(await writer.match_post(result_text))
    else:  # a result message from an older version of the bot: only its text is known
        source = message if action == "mpost" else message.reply_to_message
        result_text = (source.text or source.caption or "") if source else ""
        if not result_text:
            await callback.answer("Не нашёл сообщение с итогом матча.", show_alert=True)
            return

        async def write() -> Post:
            return Post(await writer.match_post(result_text))

    keyboard = post_keyboard(news_id=news_id, match_id=match_id,
                             publish=app.channel is not None)
    retry = post_keyboard(news_id=news_id, match_id=match_id)  # for error messages
    _writing.add(key)
    try:
        if match_id is None:
            await callback.answer("✍️ Пишу пост…")
        # the photo credit of the first version (read before the placeholder replaces it)
        kept_credit = _photo_credit(message.caption or "") if regenerate else ""
        if regenerate:
            target = message
            await _set_text(target, "✍️ Пишу другой вариант…")
        else:
            target = await message.reply("✍️ Пишу пост, это займёт до минуты…")
        # the picture is looked for while the text is being written
        photo_task = None
        if record is not None and not regenerate:
            photo_task = asyncio.create_task(app.photos.for_news(record))
        try:
            post = await write()
        except LLMError as exc:
            if photo_task is not None:
                photo_task.cancel()
            log.warning("Не удалось написать пост: %s", exc)
            await _set_text(target, "⚠️ Не получилось написать пост: нейросети сейчас "
                            "перегружены или закончился бесплатный лимит. Попробуй через "
                            "пару минут.", retry)
            return
        except Exception:
            if photo_task is not None:
                photo_task.cancel()
            log.exception("Ошибка при написании поста")
            await _set_text(target, "⚠️ Не получилось написать пост из-за ошибки. "
                            "Подробности — в logs/bot.log", retry)
            return

        signature = app.config.post_signature
        if regenerate:
            if target.photo:
                text = fit_post(post.text, _with_credit(signature, kept_credit))
            else:
                text = render_post(post.text, signature)
            await _set_text(target, text, keyboard)
            return

        photo, credit = await _post_picture(app, match_info, message, photo_task)
        if photo is not None:
            try:
                await message.reply_photo(
                    photo, caption=fit_post(post.text, _with_credit(signature, credit)),
                    reply_markup=keyboard)
            except TelegramBadRequest as exc:
                log.warning("Не удалось отправить пост с фото: %s", exc.message)
            else:
                with contextlib.suppress(TelegramBadRequest):
                    await target.delete()
                return
        await _set_text(target, render_post(post.text, signature), keyboard)
    finally:
        _writing.discard(key)


async def _publish(callback: CallbackQuery, message: Message, app: App) -> None:
    """«📢 В канал»: copy the written post (photo and caption, no buttons) to the channel."""
    channel = app.channel
    if channel is None:
        await callback.answer("Канал не подключён: добавь CHANNEL_ID в .env и перезапусти бота.",
                              show_alert=True)
        return
    key = (message.chat.id, message.message_id)
    if key in _publishing:
        await callback.answer("Уже публикую…")
        return
    _publishing.add(key)
    try:
        try:
            channel_message = await channel.publish(message.chat.id, message.message_id)
        except TelegramForbiddenError:
            await callback.answer("Бот не может писать в канал: проверь, что он администратор "
                                  "с правом «Публикация сообщений».", show_alert=True)
            return
        except TelegramAPIError as exc:
            log.warning("Не удалось опубликовать в канал %s: %s", channel.name, exc)
            hint = (" Проверь CHANNEL_ID в .env." if "chat not found" in str(exc).lower()
                    else "")
            await callback.answer(f"Telegram не дал опубликовать: {exc}.{hint}"[:190],
                                  show_alert=True)
            return
        link = channel.link(channel_message)
        log.info("Пост опубликован в канале %s: %s", channel.name, link or channel_message)
        await _edit_markup(message, published_keyboard(link))
        await callback.answer("Опубликовано в канале ✅")
    finally:
        _publishing.discard(key)


async def _post_picture(app: App, match_info, message: Message,
                        photo_task: asyncio.Task | None
                        ) -> tuple[BufferedInputFile | str | None, str]:
    """``(picture, credit)``: the photo found for a news post (see :mod:`bot.photos`), the
    result card for a match post. The credit is the attribution a free licence requires."""
    try:
        if match_info is not None:
            return BufferedInputFile(await app.cards.make(*match_info), "result.jpg"), ""
        if photo_task is not None:
            picture = await photo_task
            if picture is None:
                return None, ""
            return BufferedInputFile(picture.data, "photo.jpg"), picture.credit
    except Exception:
        log.exception("Не удалось подготовить картинку для поста")
        return None, ""
    if message.photo:  # an older result message that already carries a picture
        return message.photo[-1].file_id, ""
    return None, ""


def _photo_credit(caption: str) -> str:
    return next((line for line in caption.splitlines() if line.startswith("📷")), "")


def _with_credit(signature: str, credit: str) -> str:
    return "\n".join(part for part in (signature.strip(), credit) if part)


async def _set_text(message: Message, text: str,
                    markup: InlineKeyboardMarkup | None = None) -> None:
    """Replace the text of a message, or its caption when it is a photo."""
    try:
        if message.photo:
            await message.edit_caption(caption=text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup, link_preview_options=NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in exc.message.lower():
            log.warning("Не удалось обновить сообщение: %s", exc.message)


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
