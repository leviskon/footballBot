import asyncio

import pytest

from bot.keyboards import NewsAction, match_keyboard, news_keyboard, post_keyboard
from bot.net import FetchError
from bot.render import render_post
from bot.storage import NewsRecord, NewsStatus
from bot.writer import LLMError, PostWriter, clean_post, extract_article, news_prompt

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


def test_falls_back_to_the_next_provider():
    busy = FakeProvider("Gemini", error="HTTP 503 overloaded")
    groq = FakeProvider("Groq", answer="❗️ ПОСТ\n\nТекст.")
    writer = PostWriter(FakeHttp("<article><p>" + "Текст статьи. " * 10 + "</p></article>"),
                        [busy, groq])
    assert asyncio.run(writer.news_post(RECORD)) == "❗️ ПОСТ\n\nТекст."
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
    assert NewsAction.unpack(match_keyboard().inline_keyboard[0][0].callback_data).action == "mpost"
    assert NewsAction.unpack(post_keyboard("abc123").inline_keyboard[0][0].callback_data) == \
        NewsAction(action="repost", item="abc123")
    assert NewsAction.unpack(post_keyboard(None).inline_keyboard[0][0].callback_data).action == \
        "rempost"


def test_failed_provider_rests_and_goes_last():
    busy = FakeProvider("Busy", error="HTTP 503")
    ok = FakeProvider("Ok", answer="ПОСТ")
    writer = PostWriter(FakeHttp(), [busy, ok])
    asyncio.run(writer.news_post(RECORD))
    asyncio.run(writer.news_post(RECORD))
    assert len(busy.prompts) == 1 and len(ok.prompts) == 2  # the second post did not wait
