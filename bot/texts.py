"""Longer user-facing texts: welcome, help, status, settings."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from .catalog import CATEGORIES, CATEGORY_BY_KEY
from .news import CycleReport
from .sources import LANGUAGE_NAMES
from .storage import NewsStatus
from .textutil import esc

if TYPE_CHECKING:
    from .app import App

BUTTONS_HELP = (
    "<b>Кнопки внизу экрана</b>\n"
    "📰 Последние новости — до 5 свежих, которых ещё не присылал\n"
    "📅 Матчи сегодня — расписание и счёт\n"
    "🔄 Проверить сейчас — не ждать следующей проверки\n"
    "📊 Статус — работают ли источники\n"
    "⚙️ Настройки — турниры, источники, уведомления\n"
    "⏸ Пауза / ▶️ Продолжить — остановить и возобновить сбор\n\n"
    "Если кнопки пропали — нажми /start."
)

SETTINGS_INTRO = (
    "⚙️ <b>Настройки</b>\n\n"
    "Нажимай на кнопки, чтобы включить или выключить:\n"
    "• <b>турниры</b> — новости, итоги матчей и анонсы по ним;\n"
    "• <b>Звёзды</b> — новости о топ-игроках и тренерах, даже без упоминания клуба;\n"
    "• <b>Прочее</b> — остальные футбольные новости (кроме РПЛ, МЛС и т.п.);\n"
    "• <b>источники по языкам</b> — русские включены сразу; английские (BBC, The Athletic, "
    "Sky Sports…), испанские (Marca, AS…), итальянские (Gazzetta, Ди Марцио…), немецкие "
    "(Kicker, Bild) и французские (L'Équipe, Foot Mercato…) приходят на языке оригинала;\n"
    "• <b>итоги матчей</b> — финальный счёт, голы и удаления сразу после матча;\n"
    "• <b>анонс дня</b> — утром список матчей на сегодня."
)


def _tournaments() -> str:
    return ", ".join(c.name for c in CATEGORIES if c.espn_code)


def welcome(app: App, *, first_time: bool = False) -> str:
    digest = app.config.digest_time.strftime("%H:%M")
    intro = (
        "👋 <b>Готово, теперь ты владелец бота.</b>\n\n" if first_time else "👋 <b>Привет!</b>\n\n"
    )
    post_note = (", <b>✍️ Сделать пост</b> — нейросеть напишет готовый пост для канала"
                 if app.writer is not None else "")
    first_note = (
        "\n\nСейчас пришлю несколько свежих новостей для примера, дальше — по мере выхода."
        if first_time else ""
    )
    return (
        f"{intro}"
        "Я собираю футбольные новости из открытых источников и присылаю их сюда. "
        "Тебе остаётся выбрать, добавить фото или видео и опубликовать.\n\n"
        f"<b>Слежу за:</b> {esc(_tournaments())}, а также за новостями о звёздах.\n"
        f"<b>Ещё присылаю:</b> 🏁 итоги матчей и 📅 анонс матчей дня в {digest}.\n\n"
        "Под каждой новостью есть кнопки: <b>✅ Взял</b> — пометить использованное, "
        "<b>🗑 Удалить</b> — убрать из чата"
        f"{post_note}.\n\n"
        f"{BUTTONS_HELP}"
        f"{first_note}"
    )


def private_bot(user_id: int) -> str:
    return (
        "🔒 Это приватный бот.\n\n"
        f"Твой Telegram ID: <code>{user_id}</code>\n"
        "Чтобы получить доступ, владелец должен добавить этот ID в ADMIN_IDS в файле .env."
    )


def paused_notice() -> str:
    return "⏸ Бот на паузе. Нажми «▶️ Продолжить»."


def check_done(report: CycleReport, results: int) -> str:
    lines = ["✅ <b>Проверка завершена</b>", ""]
    lines.append(f"Источников ответило: {report.sources_ok} из "
                 f"{report.sources_ok + report.sources_failed}")
    lines.append(f"Новых материалов: {report.new}")
    lines.append(f"Отправлено новостей: {report.delivered}")
    if report.duplicates:
        lines.append(f"Отсеяно дублей: {report.duplicates}")
    if report.seeded:
        lines.append(f"Запомнено без отправки (первая загрузка источника): {report.seeded}")
    lines.append(f"Отправлено итогов матчей: {results}")
    if not report.delivered and not results:
        lines += ["", "Новых новостей по выбранным турнирам пока нет."]
    return "\n".join(lines)


def _ago(ts: float) -> str:
    seconds = max(0, int(time.time() - ts))
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    return f"{seconds // 3600} ч {seconds % 3600 // 60} мин назад"


def _uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


async def status(app: App) -> str:
    settings = await app.settings.get()
    tz = app.config.tz
    midnight = int(datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    sent_today = await app.db.count_news(status=NewsStatus.SENT, since=midnight)
    results_today = await app.db.count_matches_sent(midnight)
    pending = await app.db.count_news(status=NewsStatus.PENDING)

    active = [s for s in app.collector.sources if s.lang in settings.langs]
    health = app.collector.health
    ok = [s for s in active if health[s.key].failures == 0 and health[s.key].last_ok]
    failing = [s for s in active if health[s.key].failures]

    lines = ["📊 <b>Статус</b>", ""]
    lines.append(f"Режим: {'⏸ пауза' if settings.paused else '▶️ работает'} "
                 f"(запущен {_uptime(time.time() - app.started_at)} назад)")
    lines.append(f"Источники: {len(ok)} из {len(active)} в порядке")
    for source in failing:
        state = health[source.key]
        lines.append(f"  ⚠️ {esc(source.name)}: {esc(state.last_error or 'ошибка')} "
                     f"(подряд: {state.failures})")
    report = app.collector.last_report
    if report:
        lines.append(f"Последняя проверка: {_ago(report.started_at)}, новых материалов "
                     f"{report.new}, отправлено {report.delivered}")
    lines.append(f"Сегодня отправлено: новостей {sent_today}, итогов матчей {results_today}")
    if pending:
        lines.append(f"В очереди на отправку: {pending}")
    if app.matches.errors:
        names = ", ".join(CATEGORY_BY_KEY[k].name for k in app.matches.errors if k in CATEGORY_BY_KEY)
        lines.append(f"⚠️ Не загружаются матчи: {esc(names)}")

    enabled = [c.name for c in CATEGORY_BY_KEY.values() if c.key in settings.categories]
    langs = [name for lang, name in LANGUAGE_NAMES.items() if lang in settings.langs]
    lines += [
        "",
        f"Темы: {esc(', '.join(enabled)) or '—'}",
        f"Языки источников: {esc(', '.join(langs)) or 'все выключены'}",
        f"Посты пишет: {esc(' → '.join(app.writer.provider_names))}"
        if app.writer is not None else "Посты: нейросеть не подключена (нет ключа в .env)",
        f"Канал для публикации: {esc(app.channel.name)}" if app.channel is not None
        else "Канал для публикации: не подключён (CHANNEL_ID в .env)",
        f"Итоги матчей: {'вкл' if settings.results else 'выкл'} · "
        f"Анонс дня: {app.config.digest_time.strftime('%H:%M') if settings.digest else 'выкл'} "
        f"({esc(app.config.tz_name)})",
    ]
    return "\n".join(lines)
