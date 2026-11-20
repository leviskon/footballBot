"""Application wiring: services, background loops and the polling entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.types import BotCommand, InlineKeyboardMarkup, User

from .access import Access
from .classifier import Classifier
from .config import Config
from .espn import EspnClient
from .matches import MatchService
from .net import HttpClient
from .news import MAX_DELIVERY_ATTEMPTS, CycleReport, NewsCollector
from .notifier import DeliveryError, Notifier
from .render import render_day, render_news, render_result
from .keyboards import match_keyboard, news_keyboard
from .settings import SettingsStore
from .storage import NewsRecord, Storage
from .writer import GeminiProvider, GroqProvider, PostWriter, Provider

log = logging.getLogger(__name__)

MINUTE_LOOP_SEC = 60
# Exit code when Telegram rejects the token; the systemd unit does not restart on it.
EXIT_BAD_TOKEN = 3
NEWS_RETENTION_DAYS = 14
MATCH_RETENTION_DAYS = 30
# «📰 Последние новости» offers unsent stories no older than this.
LATEST_WINDOW_SEC = 24 * 3600
_PRUNED_KEY = "pruned_on"

# Everything else lives on the reply-keyboard menu; /start brings the menu back.
BOT_COMMANDS = (
    BotCommand(command="start", description="Открыть меню"),
)


class App:
    def __init__(self, config: Config, bot: Bot, db: Storage, http: HttpClient) -> None:
        self.config = config
        self.bot = bot
        self.db = db
        self.settings = SettingsStore(db)
        self.access = Access(db, config.admin_ids)
        self.notifier = Notifier(bot, self.access)
        self.collector = NewsCollector(http, db, self.settings, Classifier(),
                                       max_age_hours=config.max_news_age_hours,
                                       interval=config.news_interval)
        self.matches = MatchService(EspnClient(http), db, self.settings, tz=config.tz,
                                    interval=config.matches_interval,
                                    digest_time=config.digest_time)
        self.writer = make_writer(config, http)
        self.started_at = time.time()
        self.bot_username = ""
        self._news_lock = asyncio.Lock()
        self._results_lock = asyncio.Lock()
        self._wake_news = asyncio.Event()
        self._wake_minute = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._waiting_logged = False

    # -------------------------------------------------------------------- sending

    async def _send_record(self, record: NewsRecord) -> bool:
        return await self.notifier.broadcast(
            render_news(record, self.config.tz),
            keyboard=self.news_keyboard(record.id, record.url),
            preview_url=record.url,
        )

    def news_keyboard(self, item_id: str, url: str, *,
                      taken: bool = False) -> InlineKeyboardMarkup:
        return news_keyboard(item_id, url, taken=taken, post=self.writer is not None)

    async def _send_text(self, text: str) -> bool:
        return await self.notifier.broadcast(text)

    async def _send_result(self, text: str) -> bool:
        keyboard = match_keyboard() if self.writer is not None else None
        return await self.notifier.broadcast(text, keyboard=keyboard)

    async def send_latest(self, chat_id: int, count: int) -> int:
        """Send up to ``count`` of the freshest stories that were never sent before.

        Every story shown here is marked as sent, so neither the button nor the regular
        delivery repeats it. Runs under the news lock: the background cycle cannot deliver
        the same queued story at the same moment. Raises ``DeliveryError`` when Telegram is
        unreachable. Returns how many stories were sent.
        """
        async with self._news_lock:
            settings = await self.settings.get()
            records = await self.db.latest_news(
                count, settings.categories, since=int(time.time()) - LATEST_WINDOW_SEC)
            sent = 0
            for record in reversed(records):  # oldest first, so the newest ends up at the bottom
                delivered = await self.notifier.send(
                    chat_id, render_news(record, self.config.tz),
                    keyboard=self.news_keyboard(record.id, record.url), preview_url=record.url)
                if delivered:
                    await self.db.mark_news_sent(record.id, int(time.time()))
                    sent += 1
                else:
                    await self.db.register_news_failure(record.id, MAX_DELIVERY_ATTEMPTS)
            return sent

    async def _is_paused(self) -> bool:
        return (await self.settings.get()).paused

    # --------------------------------------------------------------------- cycles

    async def news_cycle(self) -> CycleReport:
        async with self._news_lock:
            report = await self.collector.collect()
            try:
                report.delivered = await self.collector.deliver_pending(
                    self._send_record, is_paused=self._is_paused)
            except DeliveryError as exc:
                log.warning("Telegram недоступен (%s) — доставлю позже", exc)
            log.info(
                "Новости: источников %d/%d, новых %d → в очередь %d, дублей %d, "
                "не по теме %d, запомнено %d, отправлено %d (%.1f с)",
                report.sources_ok, report.sources_ok + report.sources_failed, report.new,
                report.queued, report.duplicates, report.filtered, report.seeded,
                report.delivered, report.duration,
            )
            return report

    async def results_cycle(self, *, force: bool = False) -> int:
        async with self._results_lock:
            try:
                sent = await self.matches.poll_results(self._send_result, render_result, force=force)
            except DeliveryError as exc:
                log.warning("Telegram недоступен (%s) — результаты отправлю позже", exc)
                return 0
            if sent:
                log.info("Отправлено результатов матчей: %d", sent)
            return sent

    async def daily_cycle(self) -> None:
        try:
            await self.matches.maybe_send_digest(
                self._send_text, lambda day, groups: render_day(day, groups, self.config.tz))
        except DeliveryError as exc:
            log.warning("Telegram недоступен (%s) — анонс отправлю позже", exc)
        today = datetime.now(self.config.tz).date().isoformat()
        if await self.db.get_value(_PRUNED_KEY) != today:
            now = int(time.time())
            news, matches = await self.db.prune(now - NEWS_RETENTION_DAYS * 86400,
                                                now - MATCH_RETENTION_DAYS * 86400)
            await self.db.set_value(_PRUNED_KEY, today)
            if news or matches:
                log.info("Очистка базы: удалено новостей %d, матчей %d", news, matches)

    # ------------------------------------------------------------------- controls

    async def pause(self) -> None:
        await self.settings.update(lambda s: setattr(s, "paused", True))

    async def resume(self) -> None:
        # Whatever appeared during the pause is remembered silently instead of flooding.
        await self.collector.reset_seeding()
        await self.matches.reset_seeding()
        await self.settings.update(lambda s: setattr(s, "paused", False))
        self.wake()

    def wake(self) -> None:
        self._wake_news.set()
        self._wake_minute.set()

    # ----------------------------------------------------------- background loops

    async def _ready(self) -> bool:
        if not self.access.recipients:
            if not self._waiting_logged:
                log.info("Жду владельца: откройте https://t.me/%s и нажмите /start",
                         self.bot_username)
                self._waiting_logged = True
            return False
        return not await self._is_paused()

    @staticmethod
    async def _sleep(event: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
        except TimeoutError:
            pass
        event.clear()

    async def _news_loop(self) -> None:
        while True:
            try:
                if await self._ready():
                    await self.news_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка в цикле новостей")
            await self._sleep(self._wake_news, self.config.news_interval)

    async def _minute_loop(self) -> None:
        while True:
            try:
                if await self._ready():
                    await self.results_cycle()
                    await self.daily_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка в цикле матчей")
            await self._sleep(self._wake_minute, MINUTE_LOOP_SEC)

    def start_background(self) -> None:
        if not self._tasks:
            self._tasks = [
                asyncio.create_task(self._news_loop(), name="news-loop"),
                asyncio.create_task(self._minute_loop(), name="matches-loop"),
            ]

    async def stop_background(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # ------------------------------------------------------------- aiogram hooks

    async def on_startup(self, bot: Bot) -> None:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await bot.set_my_commands(list(BOT_COMMANDS))
        except TelegramAPIError as exc:
            log.warning("Не удалось обновить меню команд: %s", exc)
        self.start_background()
        if self.access.recipients:
            log.info("Получатели: %s", ", ".join(map(str, self.access.recipients)))
        if self.writer is not None:
            log.info("Посты пишет: %s", " → ".join(self.writer.provider_names))
        else:
            log.info("Кнопка «Сделать пост» выключена: в .env нет GEMINI_TOKEN или GROQ_TOKEN")

    async def on_shutdown(self) -> None:
        await self.stop_background()
        log.info("Бот остановлен")


def make_writer(config: Config, http: HttpClient) -> PostWriter | None:
    """The post writer, or None when no LLM key is set in .env (the button is then hidden)."""
    providers: list[Provider] = []
    if config.gemini_token:
        providers += [GeminiProvider(http, config.gemini_token, model)
                      for model in config.gemini_models]
    if config.groq_token:
        providers += [GroqProvider(http, config.groq_token, model) for model in config.groq_models]
    return PostWriter(http, providers) if providers else None


def create_bot(config: Config) -> Bot:
    session_kwargs = {}
    if config.telegram_api_url:
        session_kwargs["api"] = TelegramAPIServer.from_base(config.telegram_api_url)
    session = AiohttpSession(proxy=config.telegram_proxy, **session_kwargs)
    return Bot(config.bot_token, session=session,
               default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def connect(bot: Bot, config: Config) -> User | None:
    """Check the token. Retries forever on network errors; None if the token is rejected."""
    delay = 10
    while True:
        try:
            return await bot.get_me(request_timeout=30)
        except TelegramUnauthorizedError:
            log.error("Telegram не принял токен. Проверьте BOT_TOKEN (или BOT_API) в .env — "
                      "его выдаёт @BotFather")
            return None
        except Exception as exc:  # network, proxy or TLS problems
            hint = ("Проверьте, что прокси из TELEGRAM_PROXY работает."
                    if config.telegram_proxy else
                    "Если провайдер блокирует Telegram, укажите TELEGRAM_PROXY в .env "
                    "(см. README) или запустите бота на сервере за рубежом.")
            log.error("Нет связи с Telegram (%s: %s). %s Повтор через %d с",
                      type(exc).__name__, exc, hint, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


def install_stop_handler(dispatcher: Dispatcher) -> None:
    """Graceful Ctrl+C on Windows, where aiogram cannot install its own signal handlers.

    The first Ctrl+C asks polling to stop cleanly, a second one aborts immediately. On
    Linux/macOS aiogram replaces this handler with its own, which behaves the same way.
    """
    loop = asyncio.get_running_loop()
    stopping = False

    async def stop() -> None:
        with contextlib.suppress(RuntimeError):  # polling has not started yet / already stopped
            await dispatcher.stop_polling()

    def handler(signum: int, frame: object) -> None:
        nonlocal stopping
        if stopping:
            raise KeyboardInterrupt
        stopping = True
        log.info("Останавливаюсь… (повторное Ctrl+C — немедленно)")
        loop.call_soon_threadsafe(lambda: loop.create_task(stop()))

    for name in ("SIGINT", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), handler)


async def run(config: Config) -> int:
    from .handlers import build_router  # imported here: handlers depend on App

    db = await Storage.open(config.db_path)
    http = HttpClient(config.request_timeout)
    await http.start()
    bot = create_bot(config)
    try:
        if config.telegram_proxy:
            log.info("Telegram через прокси %s", config.telegram_proxy.split("@")[-1])
        me = await connect(bot, config)
        if me is None:
            return EXIT_BAD_TOKEN

        app = App(config, bot, db, http)
        app.bot_username = me.username or ""
        await app.access.load()

        dispatcher = Dispatcher()
        dispatcher["app"] = app
        dispatcher.include_router(build_router())
        dispatcher.startup.register(app.on_startup)
        dispatcher.shutdown.register(app.on_shutdown)

        log.info("Бот @%s запущен. Часовой пояс: %s. Остановить — Ctrl+C",
                 app.bot_username, config.tz_name)
        install_stop_handler(dispatcher)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
        return 0
    finally:
        await http.close()
        await db.close()
        await bot.session.close()
