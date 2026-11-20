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
        self.rich_message = None
        self.rich_html = None  # what the bot sent as a rich post
        self.rich_photo = None
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

    async def reply_rich(self, rich_message, reply_markup=None, **kwargs):
        message = FakeMessage(reply_to=self)
        message.markup = reply_markup
        message.rich_html = rich_message.html
        message.rich_photo = rich_message.media[0].media.media if rich_message.media else None
        photo = [SimpleNamespace(file_id="photo-file-id")] if rich_message.media else None
        caption = None
        if 'figcaption>' in rich_message.html:
            caption = SimpleNamespace(text=rich_message.html.split("<figcaption>")[1]
                                      .split("</figcaption>")[0])
        blocks = [SimpleNamespace(type="photo", photo=photo, caption=caption)] if photo else []
        message.rich_message = SimpleNamespace(blocks=blocks)
        self.replies.append(message)
        return message

    async def edit_text(self, text=None, reply_markup=None, rich_message=None, **kwargs):
        if rich_message is not None:
            self.rich_html = rich_message.html
            self.rich_photo = rich_message.media[0].media.media if rich_message.media else None
        else:
            self.text = text
        self.markup = reply_markup

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.markup = reply_markup

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
                           config=SimpleNamespace(post_signature="⚽️ FUTERA", post_source_button=True,
                                                  tz=None))


def test_news_post_is_a_rich_message_rewritten_in_place():
    app = make_app()
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    placeholder, post = news.replies
    assert placeholder.deleted
    assert post.rich_photo.filename == "photo.jpg"
    assert post.rich_html.startswith('<img src="tg://photo?id=hero"><h2>🚨 ВАРИАНТ 1</h2>')
    assert "<footer>⚽️ FUTERA</footer>" in post.rich_html
    assert 'url="https://ru.example/1"' in post.rich_html  # «🔗 Источник» button
    assert NewsAction.unpack(post.markup.inline_keyboard[0][0].callback_data) == \
        NewsAction(action="repost", item="n1")

    asyncio.run(_write_post(FakeCallback(), post, NewsAction(action="repost", item="n1"), app))
    assert "<h2>🚨 ВАРИАНТ 2</h2>" in post.rich_html
    assert post.rich_photo == "photo-file-id"  # the same photo, reused by its file id


def test_without_a_photo_the_post_is_rich_text():
    app = make_app(photo=None)
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    placeholder, post = news.replies
    assert placeholder.deleted and post.rich_html.startswith("<h2>🚨 ВАРИАНТ 1</h2>")
    assert post.rich_photo is None


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
    assert placeholder.deleted and post.rich_photo.data == b"\xff\xd8other"


def test_wikipedia_credit_is_kept_in_every_version():
    app = make_app(credit=True)
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    post = news.replies[1]
    credit = "<figcaption>📷 Zafer / Wikimedia Commons, CC BY 4.0</figcaption>"
    assert credit in post.rich_html
    asyncio.run(_write_post(FakeCallback(), post, NewsAction(action="repost", item="n1"), app))
    assert "<h2>🚨 ВАРИАНТ 2</h2>" in post.rich_html and credit in post.rich_html


def test_rich_post_rewrite_failure_keeps_the_post():
    app = make_app()
    news = FakeMessage("новость")
    asyncio.run(_write_post(FakeCallback(), news, NewsAction(action="post", item="n1"), app))
    post = news.replies[1]
    html = post.rich_html
    app.writer.news_post = make_app(fail=True).writer.news_post
    asyncio.run(_write_post(FakeCallback(), post, NewsAction(action="repost", item="n1"), app))
    assert post.rich_html == html  # the post and its photo are untouched
    assert post.replies[0].text.startswith("⚠️ Не получилось")
    assert post.markup.inline_keyboard[0][0].text == "🔄 Другой вариант"


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
