import asyncio
from dataclasses import replace

import pytest

from bot.keyboards import NewsAction, match_keyboard, news_keyboard, post_keyboard
from bot.net import FetchError
from bot.render import CAPTION_LIMIT, fit_post, plain_len, render_post
from bot.storage import NewsRecord, NewsStatus
from bot.writer import (
    LLMError, PostWriter, clean_post, extract_article, extract_image, news_prompt, parse_json_object,
)

RECORD = NewsRecord(
    id="abc123", source="sportsru", lang="ru", url="https://www.sports.ru/news/1",
    title="«Реал» продлил контракт с Вальверде", summary="До 2031 года.",
    published_at=0, fetched_at=0, status=NewsStatus.SENT,
)


class FakeHttp:
    def __init__(self, html: str = "", broken: bool = False) -> None:
        self.html, self.broken = html, broken

    async def fetch_text(self, url, *, timeout=None):
        if self.broken:
            raise FetchError("HTTP 403")
        return self.html


class FakeProvider:
    def __init__(self, name, answer=None, error=None):
        self.name, self.answer, self.error = name, answer, error
        self.prompts: list[str] = []

    async def generate(self, system, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise LLMError(self.error)
        return self.answer


def test_extract_article_prefers_article_paragraphs():
    html = """<html><body><nav><p>Главная · Новости · Матчи · Трансферы · Видео</p></nav>
    <p>Подпишитесь на рассылку, чтобы первыми узнавать все новости футбола!</p>
    <article><h1>Заголовок</h1>
    <p>«Реал» и Федерико Вальверде договорились о новом контракте до 2031 года.</p>
    <p>Коротко.</p>
    <p>Отступные составят миллиард евро, зарплата уругвайца вырастет до уровня лидеров.</p>
    <script>var x = "<p>не текст статьи, а код скрипта на странице</p>";</script>
    </article><footer><p>© Все права защищены. Перепечатка запрещена без разрешения.</p></footer>
    </body></html>"""
    assert extract_article(html) == (
        "«Реал» и Федерико Вальверде договорились о новом контракте до 2031 года.\n"
        "Отступные составят миллиард евро, зарплата уругвайца вырастет до уровня лидеров."
    )


def test_clean_post_strips_markdown_and_hashtags():
    raw = "```\n**🚨 ВАЛЬВЕРДЕ ОСТАЁТСЯ**\n\n\n\nТекст *поста*.\n\n#Реал #Вальверде\n```"
    assert clean_post(raw) == "🚨 ВАЛЬВЕРДЕ ОСТАЁТСЯ\n\nТекст поста."


def test_render_post_bold_headline_and_signature():
    text = render_post("🚨 «РЕАЛ» & ВАЛЬВЕРДЕ\n\nТекст <поста>.", "⚽️ FUTERA | Футбол")
    assert text == ("<b>🚨 «РЕАЛ» &amp; ВАЛЬВЕРДЕ</b>\n\nТекст &lt;поста&gt;."
                    "\n\n\n⚽️ FUTERA | Футбол")
    assert render_post("ЗАГОЛОВОК") == "<b>ЗАГОЛОВОК</b>"


def test_render_post_bold_and_quote():
    text = render_post("🚨 ЗАГОЛОВОК\n\n💰 Сумма — **100 млн €**.\n\n> «Цитата»\n> — Автор", "FUTERA")
    assert text == ("<b>🚨 ЗАГОЛОВОК</b>\n\n💰 Сумма — <b>100 млн €</b>.\n\n"
                    "<blockquote>«Цитата»\n— Автор</blockquote>\n\n\nFUTERA")


def test_clean_post_keeps_short_bold_and_quote_lines():
    raw = ("**🚨 ЗАГОЛОВОК**\n\n💰 Сумма — ** 100 млн € **, *курсив*.\n\n"
           "**" + "Целое предложение жирным, которое выделять не надо совсем никак. " + "**\n\n"
           "> «Цитата»\n> — Автор")
    assert clean_post(raw) == ("🚨 ЗАГОЛОВОК\n\n💰 Сумма — **100 млн €**, курсив.\n\n"
                               "Целое предложение жирным, которое выделять не надо совсем никак.\n\n"
                               "> «Цитата»\n> — Автор")


def test_falls_back_to_the_next_provider():
    busy = FakeProvider("Gemini", error="HTTP 503 overloaded")
    groq = FakeProvider("Groq", answer="❗️ ПОСТ\n\nТекст.")
    writer = PostWriter(FakeHttp("<article><p>" + "Текст статьи. " * 10 + "</p></article>"),
                        [busy, groq])
    assert asyncio.run(writer.news_post(RECORD)).text == "❗️ ПОСТ\n\nТекст."
    assert busy.prompts and groq.prompts
    assert "Текст статьи." in groq.prompts[0] and "Sports.ru" in groq.prompts[0]


def test_all_providers_failing_raises():
    writer = PostWriter(FakeHttp(broken=True),
                        [FakeProvider("A", error="HTTP 429"), FakeProvider("B", answer="  ")])
    with pytest.raises(LLMError, match="A: HTTP 429; B: пустой ответ"):
        asyncio.run(writer.news_post(RECORD))


def test_article_download_failure_uses_the_summary():
    provider = FakeProvider("A", answer="ПОСТ")
    asyncio.run(PostWriter(FakeHttp(broken=True), [provider]).news_post(RECORD))
    assert provider.prompts == [news_prompt(RECORD, "")]
    assert "До 2031 года." in provider.prompts[0]


def test_post_buttons():
    with_post = news_keyboard("abc123", "https://x.example", post=True).inline_keyboard
    assert with_post[0][0].url == "https://x.example"  # the source link stays first
    assert NewsAction.unpack(with_post[1][0].callback_data) == NewsAction(action="post",
                                                                           item="abc123")
    assert len(news_keyboard("abc123", "https://x.example").inline_keyboard) == 2
    assert NewsAction.unpack(match_keyboard("401861194").inline_keyboard[0][0].callback_data) == \
        NewsAction(action="mpost", item="401861194")

    def unpack(markup):
        return NewsAction.unpack(markup.inline_keyboard[0][0].callback_data)

    assert unpack(post_keyboard(news_id="abc123")) == NewsAction(action="repost", item="abc123")
    assert unpack(post_keyboard(match_id="401")) == NewsAction(action="rempost", item="401")
    assert unpack(post_keyboard()) == NewsAction(action="rempost", item="0")


def test_failed_provider_rests_and_goes_last():
    busy = FakeProvider("Busy", error="HTTP 503")
    ok = FakeProvider("Ok", answer="ПОСТ")
    writer = PostWriter(FakeHttp(), [busy, ok])
    asyncio.run(writer.news_post(RECORD))
    asyncio.run(writer.news_post(RECORD))
    assert len(busy.prompts) == 1 and len(ok.prompts) == 2  # the second post did not wait


def test_news_post_carries_the_article_photo():
    html = ('<html><head><meta content="/img/valverde.jpg" property="og:image">'
            '</head><body><p>' + "Текст статьи. " * 10 + "</p></body></html>")
    post = asyncio.run(PostWriter(FakeHttp(html), [FakeProvider("A", answer="ПОСТ")])
                       .news_post(RECORD))
    assert post.images == ("https://www.sports.ru/img/valverde.jpg",)


def test_extract_image_priorities():
    html = """<meta name="twitter:image" content="https://cdn.example/tw.jpg">
              <meta property='og:image' content='https://cdn.example/og.jpg?w=1200&amp;h=630'>"""
    assert extract_image(html, "https://site.example/a") == "https://cdn.example/og.jpg?w=1200&h=630"
    assert extract_image("<p>нет картинки</p>", "https://site.example/a") is None


def test_fit_post_respects_the_caption_limit():
    short = "🚨 ЗАГОЛОВОК\n\nПервый абзац.\n\nВторой абзац."
    assert fit_post(short, "⚽️ FUTERA") == render_post(short, "⚽️ FUTERA")

    long = "🚨 ЗАГОЛОВОК\n\n" + "\n\n".join(["Предложение номер один. " * 12] * 4)
    fitted = fit_post(long, "⚽️ FUTERA | Больше, чем просто футбол.")
    assert plain_len(fitted) <= CAPTION_LIMIT
    assert fitted.startswith("<b>🚨 ЗАГОЛОВОК</b>") and fitted.endswith("просто футбол.")

    one_huge_paragraph = "ЗАГОЛОВОК\n" + "Очень длинное предложение без конца " * 60
    assert plain_len(fit_post(one_huge_paragraph, "подпись")) <= CAPTION_LIMIT


def test_parse_json_object_from_a_fenced_answer():
    assert parse_json_object('```json\n{"Florian Wirtz": "Вирц"}\n```') == {"Florian Wirtz": "Вирц"}
    with pytest.raises(LLMError):
        parse_json_object("Не знаю")


def test_feed_picture_is_the_fallback():
    record = replace(RECORD, image="https://img.example/feed.jpg")
    post = asyncio.run(PostWriter(FakeHttp(broken=True), [FakeProvider("A", answer="ПОСТ")])
                       .news_post(record))
    assert post.images == ("https://img.example/feed.jpg",)


class SlowProvider(FakeProvider):
    def __init__(self, name, answer, delay):
        super().__init__(name, answer=answer)
        self.delay = delay
        self.cancelled = False

    async def generate(self, system, prompt):
        self.prompts.append(prompt)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.answer


def test_slow_provider_gets_company(monkeypatch):
    import time

    import bot.writer

    monkeypatch.setattr(bot.writer, "HEDGE_SEC", 0.1)
    slow = SlowProvider("Slow", "МЕДЛЕННЫЙ ПОСТ", delay=2)
    quick = SlowProvider("Quick", "БЫСТРЫЙ ПОСТ", delay=0.05)
    writer = PostWriter(FakeHttp(), [slow, quick])

    async def scenario():
        started = time.monotonic()
        post = await writer.news_post(RECORD)
        return post, time.monotonic() - started

    post, took = asyncio.run(scenario())
    assert post.text == "БЫСТРЫЙ ПОСТ" and took < 1
    assert slow.cancelled  # the loser of the race is not left running


def test_quick_answer_asks_nobody_else(monkeypatch):
    import bot.writer

    monkeypatch.setattr(bot.writer, "HEDGE_SEC", 0.5)
    first = SlowProvider("First", "ПОСТ", delay=0.01)
    second = SlowProvider("Second", "ДРУГОЙ", delay=0.01)
    assert asyncio.run(PostWriter(FakeHttp(), [first, second]).news_post(RECORD)).text == "ПОСТ"
    assert second.prompts == []


def test_render_post_quotes_bold_and_escaping():
    post = ("🚨 **МУСИАЛУ** МОГУТ ПРОДАТЬ\n\n"
            "💰 «Бавария» допускает продажу **Мусиалы** & <не только>.\n\n"
            "> «Это нельзя исключать. **Мы** подозреваем…»\n"
            "> — Карл-Хайнц Румменигге, Sky Sports\n"
            "🩺 Диагноз — **абсансы**.\n"
            "Строка с непарной ** звёздочкой.\n\n"
            "«Старая цитата» — Моуринью\n\n"
            "> «" + "Очень длинная цитата. " * 15 + "»")
    assert render_post(post) == (
        "<b>🚨 МУСИАЛУ МОГУТ ПРОДАТЬ</b>\n\n"
        "💰 «Бавария» допускает продажу <b>Мусиалы</b> &amp; &lt;не только&gt;.\n\n"
        "<blockquote>«Это нельзя исключать. Мы подозреваем…»\n"
        "— Карл-Хайнц Румменигге, Sky Sports</blockquote>\n\n"
        "🩺 Диагноз — <b>абсансы</b>.\nСтрока с непарной  звёздочкой.\n\n"
        "<blockquote>«Старая цитата»\n— Моуринью</blockquote>\n\n"
        "<blockquote expandable>«" + ("Очень длинная цитата. " * 15).strip() + "»</blockquote>")


def test_quote_credit_without_mark_and_inner_quotes():
    assert render_post("ЗАГОЛОВОК\n\n> «Реал» сильнее, чем «Барселона»\n— Анчелотти") == (
        "<b>ЗАГОЛОВОК</b>\n\n<blockquote>«„Реал“ сильнее, чем „Барселона“»\n— Анчелотти"
        "</blockquote>")
