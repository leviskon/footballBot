import asyncio
from types import SimpleNamespace

import pytest

from bot.channel import Channel
from bot.config import ConfigError, _parse_channel


@pytest.mark.parametrize("raw, value", [
    ("@futera", "@futera"), ("futera_news", "@futera_news"), ("https://t.me/futera/", "@futera"),
    ("t.me/futera", "@futera"), ("-1001234567890", -1001234567890), ("", None),
])
def test_channel_id_formats(raw, value):
    assert _parse_channel(raw) == value


def test_bad_channel_id():
    with pytest.raises(ConfigError):
        _parse_channel("канал футера")


class FakeBot:
    def __init__(self, status="administrator", can_post=True):
        self.status, self.can_post = status, can_post

    async def get_chat(self, chat_id):
        return SimpleNamespace(id=-1001234567890, title="FUTERA", username=None)

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.status, can_post_messages=self.can_post)


def test_check_rights_and_links():
    channel = Channel(FakeBot(), -1001234567890)
    assert asyncio.run(channel.check()) is None
    assert channel.name == "«FUTERA»" and channel.link(5) == "https://t.me/c/1234567890/5"
    assert "нет права" in asyncio.run(Channel(FakeBot(can_post=False), "@futera").check())
    assert "не администратор" in asyncio.run(Channel(FakeBot(status="member"), "@futera").check())
    assert Channel(FakeBot(), "@futera").link(9) == "https://t.me/futera/9"
