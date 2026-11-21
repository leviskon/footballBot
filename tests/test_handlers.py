"""The «✍️ Сделать пост» flow with fake Telegram objects."""

import asyncio
from types import SimpleNamespace

from bot.handlers import _publish, _write_post
from bot.keyboards import NewsAction
from bot.storage import NewsRecord, NewsStatus
from bot.photos import Picture
from bot.writer import LLMError, Post


class FakeMessage:
    _next_id = 100

    def __init__(self, text="", photo=None, reply_to=None):
        FakeMessage._next_id += 1
        self.message_id = FakeMessage._next_id
        self.chat = SimpleNamespace(id=1)
        self.text, self.caption, self.photo = text, None, photo
        self.reply_to_message = reply_to
        self.replies: list[FakeMessage] = []
        self.deleted = False
        self.markup = None

    async def reply(self, text, **kwargs):
        message = FakeMessage(text, reply_to=self)
        self.replies.append(message)
        return message

    async def reply_photo(self, photo, caption=None, reply_markup=None, **kwargs):
        message = FakeMessage(photo=[SimpleNamespace(file_id="f")], reply_to=self)
        message.caption, message.markup, message.sent_photo = caption, reply_markup, photo
        self.replies.append(message)
        return message

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.text, self.markup = text, reply_markup

    async def edit_caption(self, caption=None, reply_markup=None, **kwargs):
        self.caption, self.markup = caption, reply_markup

    async def delete(self):
        self.deleted = True

    async def answer(self, text, **kwargs):
        self.replies.append(FakeMessage(text))


class FakeCallback:
    def __init__(self):
        self.answers: list[str] = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text or "")


RECORD = NewsRecord(id="n1", source="sportsru", lang="ru", url="https://ru.example/1",
                    title="«Реал» продлил контракт", summary="", published_at=0, fetched_at=0,
                    status=NewsStatus.SENT, image="https://img.example/feed.jpg")


def make_app(*, fail=False, photo=b"\xff\xd8jpeg", related=None, credit=False, channel=None):
    class Writer:
        calls = 0

        async def news_post(self, record):
            Writer.calls += 1
            if fail:
                raise LLMError("HTTP 429")
            return Post(f"🚨 ВАРИАНТ {Writer.calls}\n\nТекст.", ("https://img.example/og.jpg",))

    class Db:
        async def get_news(self, news_id):
            return RECORD if news_id == "n1" else None

    class Photos:
        async def for_news(self, record, images=None):
            data = photo or related
            return Picture(data, "📷 Zafer / Wikimedia Commons, CC BY 4.0" if credit else "") \
                if data else None

    return SimpleNamespace(writer=Writer(), db=Db(), photos=Photos(), cards=None, matches=None,
                           channel=channel,
                           config=SimpleNamespace(post_signature="⚽️ FUTERA"))


def test_news_post_is_sent_as_a_photo_and_rewritten_in_place():
    app = make_app()
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    placeholder, post = news.replies
    assert placeholder.deleted
    assert post.sent_photo.filename == "photo.jpg"
    assert post.caption.startswith("<b>🚨 ВАРИАНТ 1</b>") and post.caption.endswith("FUTERA")
    assert NewsAction.unpack(post.markup.inline_keyboard[0][0].callback_data) == \
        NewsAction(action="repost", item="n1")

    asyncio.run(_write_post(FakeCallback(), post, NewsAction(action="repost", item="n1"), app))
    assert post.caption.startswith("<b>🚨 ВАРИАНТ 2</b>")  # same photo, new text


def test_without_a_photo_the_post_is_text():
    app = make_app(photo=None)
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    [placeholder] = news.replies
    assert not placeholder.deleted and placeholder.text.startswith("<b>🚨 ВАРИАНТ 1</b>")


def test_llm_failure_is_reported():
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"),
                            make_app(fail=True)))
    assert news.replies[0].text.startswith("⚠️ Не получилось написать пост")


def test_missing_news_and_missing_writer():
    callback = FakeCallback()
    asyncio.run(_write_post(callback, FakeMessage(), NewsAction(action="post", item="gone"),
                            make_app()))
    assert "удалена из базы" in callback.answers[0]
    app = make_app()
    app.writer = None
    callback = FakeCallback()
    asyncio.run(_write_post(callback, FakeMessage(), NewsAction(action="post", item="n1"), app))
    assert "Нейросеть не подключена" in callback.answers[0]


def test_photo_of_the_same_story_from_another_site():
    app = make_app(photo=None, related=b"\xff\xd8other")
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    placeholder, post = news.replies
    assert placeholder.deleted and post.sent_photo.data == b"\xff\xd8other"


def test_wikipedia_credit_is_kept_in_every_version():
    app = make_app(credit=True)
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    post = news.replies[1]
    assert post.caption.endswith("FUTERA\n📷 Zafer / Wikimedia Commons, CC BY 4.0")
    post.caption = "🚨 ВАРИАНТ 1\n\nТекст.\n\n\n⚽️ FUTERA\n📷 Zafer / Wikimedia Commons, CC BY 4.0"
    asyncio.run(_write_post(FakeCallback(), post, NewsAction(action="repost", item="n1"), app))
    assert post.caption.startswith("<b>🚨 ВАРИАНТ 2</b>")
    assert post.caption.endswith("📷 Zafer / Wikimedia Commons, CC BY 4.0")


class FakeChannel:
    name = "@futera"

    def __init__(self, error=None):
        self.error = error
        self.copied: list[tuple[int, int]] = []

    async def publish(self, from_chat_id, message_id):
        if self.error:
            raise self.error
        self.copied.append((from_chat_id, message_id))
        return 777

    def link(self, message_id):
        return f"https://t.me/futera/{message_id}"


def test_post_has_the_channel_button_and_publishes_once():
    channel = FakeChannel()
    app = make_app(channel=channel)
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    post = news.replies[1]
    buttons = post.markup.inline_keyboard[0]
    assert [b.text for b in buttons] == ["🔄 Другой вариант", "📢 В канал"]

    async def edit_reply_markup(reply_markup=None):
        post.markup = reply_markup

    post.edit_reply_markup = edit_reply_markup
    callback = FakeCallback()
    asyncio.run(_publish(callback, post, app))
    assert channel.copied == [(1, post.message_id)]
    assert callback.answers == ["Опубликовано в канале ✅"]
    [[done]] = post.markup.inline_keyboard
    assert done.url == "https://t.me/futera/777"  # the buttons now point to the channel post


def test_without_a_channel_there_is_no_button_and_a_hint():
    news = FakeMessage("новость")
    app = make_app()
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    assert [b.text for b in news.replies[1].markup.inline_keyboard[0]] == ["🔄 Другой вариант"]
    callback = FakeCallback()
    asyncio.run(_publish(callback, news.replies[1], app))
    assert "CHANNEL_ID" in callback.answers[0]


def test_publishing_error_is_explained():
    from aiogram.exceptions import TelegramForbiddenError

    app = make_app(channel=FakeChannel(TelegramForbiddenError(method=None, message="kicked")))
    callback = FakeCallback()
    asyncio.run(_publish(callback, FakeMessage(), app))
    assert "администратор" in callback.answers[0]


def test_error_message_has_no_channel_button():
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"),
                            make_app(fail=True, channel=FakeChannel())))
    assert [b.text for b in news.replies[0].markup.inline_keyboard[0]] == ["🔄 Другой вариант"]
