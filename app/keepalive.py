"""Keep the app awake on Render's free plan.

Render puts a free web service to sleep after about 15 minutes with no incoming traffic, and the
next visitor waits on a "waking up" screen. While the app is running, this background task requests
its own public /health URL every KEEP_ALIVE_MINUTES (default 10 on Render), which counts as traffic.

It cannot wake a service that is already asleep; the GitHub Actions cron job in
.github/workflows/keep-alive.yml covers that case (for example after a deploy or a Render restart).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from . import llm
from .config import settings

log = logging.getLogger("swish.keepalive")


def target() -> str | None:
    """The /health URL to ping, or None when keep-alive is off."""
    if settings.KEEP_ALIVE_MINUTES <= 0 or not settings.KEEP_ALIVE_URL:
        return None
    return settings.KEEP_ALIVE_URL + "/health"


async def ping(url: str) -> bool:
    try:
        async with httpx.AsyncClient(verify=llm.ssl_context(), timeout=30,
                                     headers={"User-Agent": "swish-assist-keepalive"}) as client:
            r = await client.get(url)
        return r.status_code == 200
    except Exception as err:  # noqa: BLE001 - a failed ping must never crash the app
        log.warning("keep-alive ping to %s failed: %s", url, err)
        return False


async def run(url: str, minutes: float, pinger: Callable[[str], Awaitable[bool]] = ping) -> None:
    log.info("keep-alive: pinging %s every %g min", url, minutes)
    while True:
        await asyncio.sleep(minutes * 60)
        ok = await pinger(url)
        log.debug("keep-alive ping %s", "ok" if ok else "failed")
