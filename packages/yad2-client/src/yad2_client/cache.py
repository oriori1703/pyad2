"""Bounded SQLite cache and cross-process request budget. All I/O is off-loop."""

import asyncio
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from yad2_client.errors import RateLimited
from yad2_client.models import PageSnapshot


class Cache:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "cache.sqlite3"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, saved REAL, data TEXT)")
                connection.execute("CREATE TABLE IF NOT EXISTS budget (key TEXT PRIMARY KEY, value REAL)")
                yield connection
        finally:
            connection.close()

    def _get(self, url: str, ttl: float) -> PageSnapshot | None:
        with self._connect() as connection:
            row = connection.execute("SELECT saved, data FROM pages WHERE url = ?", (url,)).fetchone()
        if row is None or ttl <= 0 or time.time() - row[0] >= ttl:
            return None
        try:
            snapshot = PageSnapshot.model_validate_json(row[1])
        except ValidationError:
            return None
        return snapshot.model_copy(update={"cached": True})

    async def get(self, url: str, ttl: float) -> PageSnapshot | None:
        return await asyncio.to_thread(self._get, url, ttl)

    def _put(self, url: str, snapshot: PageSnapshot) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO pages VALUES (?, ?, ?)",
                (url, time.time(), snapshot.model_dump_json()),
            )
            connection.execute(
                "DELETE FROM pages WHERE url NOT IN (SELECT url FROM pages ORDER BY saved DESC LIMIT 1000)"
            )

    async def put(self, url: str, snapshot: PageSnapshot) -> None:
        await asyncio.to_thread(self._put, url, snapshot)

    def _reserve(self, interval: float) -> float:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            values = {str(row[0]): float(row[1]) for row in connection.execute("SELECT key, value FROM budget")}
            now = time.time()
            blocked = values.get("blocked_until", 0)
            if blocked > now:
                raise RateLimited(
                    f"Yad2 requests are cooling down for {blocked - now:.0f}s; cached results remain available"
                )
            scheduled = max(now, values.get("next_request", 0))
            connection.execute("INSERT OR REPLACE INTO budget VALUES ('next_request', ?)", (scheduled + interval,))
            return scheduled - now

    async def wait_turn(self, interval: float) -> None:
        delay = await asyncio.to_thread(self._reserve, interval)
        if delay > 0:
            await asyncio.sleep(delay)

    def _block(self, seconds: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO budget VALUES ('blocked_until', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = MAX(value, excluded.value)",
                (time.time() + seconds,),
            )

    async def block(self, seconds: float) -> None:
        await asyncio.to_thread(self._block, seconds)

    def _clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM pages")

    async def clear(self) -> None:
        """Clear cached pages without resetting the request budget."""
        await asyncio.to_thread(self._clear)

    def _reset_cooldown(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM budget WHERE key = 'blocked_until'")

    async def reset_cooldown(self) -> None:
        """Called only after a successful interactive browser session."""
        await asyncio.to_thread(self._reset_cooldown)
