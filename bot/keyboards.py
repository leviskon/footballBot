"""Inline keyboards and their callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .catalog import CATEGORIES, OTHER
from .settings import BotSettings
from .sources import LANGUAGES


class Btn:
    """Texts of the main menu buttons (the bot reacts to these exact texts)."""

    LATEST = "📰 Последние новости"
    TODAY = "📅 Матчи сегодня"
    CHECK = "🔄 Проверить сейчас"
    STATUS = "📊 Статус"
    SETTINGS = "⚙️ Настройки"
    PAUSE = "⏸ Пауза"
    RESUME = "▶️ Продолжить"
    HELP = "❓ Помощь"


def main_menu(*, paused: bool) -> ReplyKeyboardMarkup:
    """Persistent keyboard under the input field. The pause button reflects the state."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.LATEST), KeyboardButton(text=Btn.TODAY)],
            [KeyboardButton(text=Btn.CHECK), KeyboardButton(text=Btn.STATUS)],
            [KeyboardButton(text=Btn.SETTINGS),
             KeyboardButton(text=Btn.RESUME if paused else Btn.PAUSE),
             KeyboardButton(text=Btn.HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выбери действие на кнопках ниже",
    )


REMOVE_MENU = ReplyKeyboardRemove()


class NewsAction(CallbackData, prefix="n"):
    # "take" | "del" | "noop" — news buttons;
    # "post" — write a post about news ``item``; "mpost" — about ESPN match ``item`` ("0" in
    # older messages: the result text of this message); "repost" / "rempost" — another
    # version of the post in this message.
    action: str
    item: str


POST_BUTTON = "✍️ Сделать пост"


class SettingsAction(CallbackData, prefix="s"):
    kind: str  # "cat" | "lang" | "flag" | "close"
    value: str


def news_keyboard(item_id: str, url: str, *, taken: bool = False,
                  post: bool = False) -> InlineKeyboardMarkup:
    # The source link must stay the very first button (handlers read the URL from it).
    rows = [[InlineKeyboardButton(text="🔗 Открыть источник", url=url)]]
    if post:
        rows.append([InlineKeyboardButton(
            text=POST_BUTTON, callback_data=NewsAction(action="post", item=item_id).pack())])
    if taken:
        rows.append([InlineKeyboardButton(
            text="✅ Взято", callback_data=NewsAction(action="noop", item=item_id).pack())])
    else:
        rows.append([
            InlineKeyboardButton(text="✅ Взял",
                                 callback_data=NewsAction(action="take", item=item_id).pack()),
            InlineKeyboardButton(text="🗑 Удалить",
                                 callback_data=NewsAction(action="del", item=item_id).pack()),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def match_keyboard(match_id: str) -> InlineKeyboardMarkup:
    """Under a match result: write a post (with the result card) about ESPN match ``match_id``."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=POST_BUTTON, callback_data=NewsAction(action="mpost", item=match_id).pack())]])


def post_keyboard(*, news_id: str | None = None,
                  match_id: str | None = None) -> InlineKeyboardMarkup:
    """Under a written post: another version of it. Without ids it is a post about a match
    from an older message, whose result text the post replies to."""
    if news_id:
        action = NewsAction(action="repost", item=news_id)
    else:
        action = NewsAction(action="rempost", item=match_id or "0")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🔄 Другой вариант", callback_data=action.pack())]])


def _mark(enabled: bool) -> str:
    return "✅" if enabled else "▫️"


def _pairs(count: int) -> list[int]:
    """Row sizes that lay ``count`` buttons out two per row."""
    return [2] * (count // 2) + ([1] if count % 2 else [])


def settings_keyboard(settings: BotSettings) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(
            text=f"{_mark(category.key in settings.categories)} {category.emoji} {category.short}",
            callback_data=SettingsAction(kind="cat", value=category.key),
        )
    builder.button(
        text=f"{_mark(OTHER.key in settings.categories)} {OTHER.emoji} Прочее (без фильтра)",
        callback_data=SettingsAction(kind="cat", value=OTHER.key),
    )
    for lang, label in LANGUAGES.items():
        builder.button(text=f"{_mark(lang in settings.langs)} {label}",
                       callback_data=SettingsAction(kind="lang", value=lang))
    builder.button(text=f"{_mark(settings.results)} 🏁 Итоги матчей",
                   callback_data=SettingsAction(kind="flag", value="results"))
    builder.button(text=f"{_mark(settings.digest)} 📅 Анонс дня",
                   callback_data=SettingsAction(kind="flag", value="digest"))
    builder.button(text="Готово", callback_data=SettingsAction(kind="close", value="-"))

    builder.adjust(*_pairs(len(CATEGORIES)), 1, *_pairs(len(LANGUAGES)), 2, 1)
    return builder.as_markup()
