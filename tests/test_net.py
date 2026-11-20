import asyncio

import pytest
from aiohttp import web

from bot.net import FetchError, HttpClient


async def _serve(handler):
    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/"


def test_slow_page_returns_what_arrived():
    async def slow(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/html; charset=windows-1251"})
        await resp.prepare(request)
        await resp.write('<head><meta property="og:image" content="/p.jpg"></head><p>Мурнинью'
                         .encode("windows-1251"))
        await asyncio.sleep(1.5)  # the rest of the page takes forever
        await resp.write(b"<p>end</p>")
        return resp

    async def scenario():
        runner, url = await _serve(slow)
        http = HttpClient(5)
        await http.start()
        try:
            text = await http.fetch_text(url, timeout=0.5)
            assert 'content="/p.jpg"' in text and "Мурнинью" in text and "end" not in text
        finally:
            await http.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_silent_page_is_a_timeout():
    async def silent(request):
        await asyncio.sleep(1.5)
        return web.Response(text="late")

    async def scenario():
        runner, url = await _serve(silent)
        http = HttpClient(5)
        await http.start()
        try:
            with pytest.raises(FetchError, match="таймаут"):
                await http.fetch_text(url, timeout=0.5)
        finally:
            await http.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_stalled_page_is_cut_early(monkeypatch):
    import time

    import bot.net

    monkeypatch.setattr(bot.net, "STALL_SEC", 0.3)

    async def stalled(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/html"})
        await resp.prepare(request)
        await resp.write(b'<meta property="og:image" content="/p.jpg">')
        await asyncio.sleep(1.5)
        return resp

    async def scenario():
        runner, url = await _serve(stalled)
        http = HttpClient(5)
        await http.start()
        try:
            started = time.monotonic()
            text = await http.fetch_text(url, timeout=10)
            assert "og:image" in text and time.monotonic() - started < 1.2
        finally:
            await http.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_trickling_page_is_cut_early(monkeypatch):
    import time

    import bot.net

    monkeypatch.setattr(bot.net, "STALL_SEC", 0.3)

    async def trickle(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/html"})
        await resp.prepare(request)
        await resp.write(b'<meta property="og:image" content="/p.jpg">')
        for _ in range(30):  # a few bytes every 50 ms — never silent, just very slow
            await asyncio.sleep(0.05)
            await resp.write(b"<!-- -->")
        return resp

    async def scenario():
        runner, url = await _serve(trickle)
        http = HttpClient(5)
        await http.start()
        try:
            started = time.monotonic()
            text = await http.fetch_text(url, timeout=10)
            assert "og:image" in text and time.monotonic() - started < 1.2
        finally:
            await http.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_fast_page_is_read_whole(monkeypatch):
    import bot.net

    monkeypatch.setattr(bot.net, "STALL_SEC", 0.3)
    body = "<p>" + "Текст статьи. " * 50_000 + "</p><p>конец</p>"

    async def fast(request):
        return web.Response(text=body, content_type="text/html")

    async def scenario():
        runner, url = await _serve(fast)
        http = HttpClient(5)
        await http.start()
        try:
            assert (await http.fetch_text(url, timeout=10)).endswith("конец</p>")
        finally:
            await http.close()
            await runner.cleanup()

    asyncio.run(scenario())
