"""Tiny in-process pub/sub used for server-sent events (dashboard + chat live updates).

publish() is safe to call from any thread (tools run in FastAPI's threadpool).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

log = logging.getLogger("swish.events")

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _subscribers.discard(q)


def _put_all(msg: str) -> None:
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def publish(kind: str, **data) -> None:
    msg = json.dumps({"type": kind, **data}, default=str)
    if _loop is None or _loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _put_all(msg)
    else:
        _loop.call_soon_threadsafe(_put_all, msg)
