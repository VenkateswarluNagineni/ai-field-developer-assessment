"""Atomic token reservations in an on-disk SQLite sliding window."""

import asyncio
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4


class LimitExceeded(Exception):
    def __init__(self, retry_after=None):
        super().__init__("Tenant token limit exceeded")
        self.retry_after = retry_after


class TokenLimiter:
    def __init__(self, path, limit=50_000, window_seconds=60, clock=time.time):
        if str(path) == ":memory:":
            raise ValueError("Use an on-disk SQLite path")
        if type(limit) is not int or limit <= 0 or window_seconds <= 0:
            raise ValueError("Token limit and window must be positive")
        self.path = Path(path).resolve()
        self.limit = limit
        self.window = window_seconds
        self.clock = clock

    async def initialize(self):
        await asyncio.to_thread(self._initialize)

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS reservations (
                id TEXT PRIMARY KEY,
                tenant_hash TEXT NOT NULL,
                tokens INTEGER NOT NULL CHECK(tokens >= 0),
                created_at REAL NOT NULL
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS tenant_window ON reservations(tenant_hash, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS expiry ON reservations(created_at)")
            connection.commit()

    async def reserve(self, tenant_hash: str, tokens: int) -> str:
        if type(tokens) is not int or tokens <= 0:
            raise ValueError("Reserve a positive integer number of tokens")
        return await asyncio.to_thread(self._reserve, tenant_hash, tokens)

    def _reserve(self, tenant_hash, tokens):
        # Each worker owns its connection. SQLite protects the shared file.
        with closing(sqlite3.connect(self.path, timeout=2)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self.clock()
            connection.execute("DELETE FROM reservations WHERE created_at <= ?", (now - self.window,))
            used = connection.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM reservations WHERE tenant_hash = ?",
                (tenant_hash,),
            ).fetchone()[0]
            if used + tokens > self.limit:
                retry_after = None
                if tokens <= self.limit:
                    needed = used + tokens - self.limit
                    released = 0
                    rows = connection.execute(
                        "SELECT tokens, created_at FROM reservations WHERE tenant_hash = ? ORDER BY created_at",
                        (tenant_hash,),
                    )
                    for amount, timestamp in rows:
                        released += amount
                        if released >= needed:
                            retry_after = max(1, math.ceil(timestamp + self.window - now))
                            break
                # Commit eviction even when the new request is rejected.
                connection.commit()
                raise LimitExceeded(retry_after)
            reservation_id = uuid4().hex
            connection.execute("INSERT INTO reservations VALUES (?, ?, ?, ?)",
                               (reservation_id, tenant_hash, tokens, now))
            return reservation_id

    async def settle(self, reservation_id: str, tokens: int):
        if type(tokens) is not int or tokens < 0:
            raise ValueError("Usage must be a nonnegative integer")
        await asyncio.to_thread(self._settle, reservation_id, tokens)

    def _settle(self, reservation_id, tokens):
        with closing(sqlite3.connect(self.path, timeout=2)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if tokens == 0:
                connection.execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
            else:
                connection.execute("UPDATE reservations SET tokens = ? WHERE id = ?", (tokens, reservation_id))
