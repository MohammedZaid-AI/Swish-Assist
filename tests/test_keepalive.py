import asyncio

from app import keepalive
from app.config import settings


def test_off_by_default_locally(monkeypatch):
    monkeypatch.setattr(settings, "KEEP_ALIVE_MINUTES", 0)
    monkeypatch.setattr(settings, "KEEP_ALIVE_URL", "https://x.onrender.com")
    assert keepalive.target() is None


def test_off_without_url(monkeypatch):
    monkeypatch.setattr(settings, "KEEP_ALIVE_MINUTES", 10)
    monkeypatch.setattr(settings, "KEEP_ALIVE_URL", "")
    assert keepalive.target() is None


def test_render_defaults(monkeypatch):
    monkeypatch.delenv("KEEP_ALIVE_MINUTES", raising=False)
    monkeypatch.delenv("KEEP_ALIVE_URL", raising=False)
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://swish-assist.onrender.com/")
    try:
        settings.reload()
        assert settings.KEEP_ALIVE_MINUTES == 10
        assert keepalive.target() == "https://swish-assist.onrender.com/health"
    finally:
        monkeypatch.undo()
        settings.reload()


def test_run_pings_repeatedly_and_survives_failures():
    calls = []

    async def fake_ping(url):
        calls.append(url)
        return len(calls) % 2 == 0  # every other ping "fails"

    async def go():
        task = asyncio.create_task(keepalive.run("https://x/health", minutes=0.0005, pinger=fake_ping))
        await asyncio.sleep(0.25)
        task.cancel()

    asyncio.run(go())
    assert len(calls) >= 3 and set(calls) == {"https://x/health"}
