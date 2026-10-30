import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from limiter import LimitExceeded, TokenLimiter


def charges(path, tenant=None):
    with sqlite3.connect(path) as connection:
        if tenant:
            return connection.execute("SELECT COALESCE(SUM(tokens), 0) FROM reservations WHERE tenant_hash=?",
                                      (tenant,)).fetchone()[0]
        return connection.execute("SELECT COALESCE(SUM(tokens), 0) FROM reservations").fetchone()[0]


@pytest.mark.asyncio
async def test_exact_limit_and_tenant_isolation(tmp_path):
    limiter = TokenLimiter(tmp_path / "limit.db", limit=100)
    await limiter.initialize()
    await limiter.reserve("tenant-a", 100)
    with pytest.raises(LimitExceeded):
        await limiter.reserve("tenant-a", 1)
    await limiter.reserve("tenant-b", 100)
    assert charges(limiter.path, "tenant-a") == 100
    assert charges(limiter.path, "tenant-b") == 100


@pytest.mark.asyncio
async def test_sliding_window_boundary_and_disk_eviction(tmp_path):
    now = [100.0]
    limiter = TokenLimiter(tmp_path / "limit.db", limit=10, clock=lambda: now[0])
    await limiter.initialize()
    await limiter.reserve("a", 10)
    now[0] = 159.999
    with pytest.raises(LimitExceeded):
        await limiter.reserve("a", 1)
    now[0] = 160.0
    await limiter.reserve("a", 10)
    with sqlite3.connect(limiter.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 1
        assert connection.execute("SELECT created_at FROM reservations").fetchone()[0] == 160


@pytest.mark.asyncio
async def test_retry_after_waits_until_enough_tokens_expire(tmp_path):
    now = [100.0]
    limiter = TokenLimiter(tmp_path / "limit.db", limit=100, clock=lambda: now[0])
    await limiter.initialize()
    await limiter.reserve("a", 30)
    now[0] = 110
    await limiter.reserve("a", 70)
    now[0] = 120
    with pytest.raises(LimitExceeded) as error:
        await limiter.reserve("a", 50)
    assert error.value.retry_after == 50
    with pytest.raises(LimitExceeded) as error:
        await limiter.reserve("a", 101)
    assert error.value.retry_after is None


@pytest.mark.asyncio
async def test_usage_settlement_refund_and_no_double_settlement(tmp_path):
    limiter = TokenLimiter(tmp_path / "limit.db", limit=100)
    await limiter.initialize()
    first = await limiter.reserve("a", 80)
    await limiter.settle(first, 20)
    await limiter.settle(first, 20)
    assert charges(limiter.path) == 20
    second = await limiter.reserve("a", 80)
    await limiter.settle(second, 0)
    await limiter.settle(second, 0)
    assert charges(limiter.path) == 20


@pytest.mark.asyncio
async def test_settlement_keeps_original_admission_timestamp(tmp_path):
    now = [100.0]
    limiter = TokenLimiter(tmp_path / "limit.db", clock=lambda: now[0])
    await limiter.initialize()
    reservation = await limiter.reserve("a", 100)
    now[0] = 110
    await limiter.settle(reservation, 50)
    with sqlite3.connect(limiter.path) as connection:
        assert connection.execute("SELECT created_at, tokens FROM reservations").fetchone() == (100, 50)


@pytest.mark.asyncio
async def test_concurrent_connections_cannot_overbook(tmp_path):
    path = tmp_path / "limit.db"
    workers = [TokenLimiter(path, limit=100) for _ in range(4)]
    await workers[0].initialize()
    async def reserve(index):
        try:
            await workers[index % 4].reserve("a", 10)
            return True
        except LimitExceeded:
            return False
    results = await asyncio.gather(*(reserve(index) for index in range(40)))
    assert sum(results) == 10
    assert charges(path) == 100


@pytest.mark.asyncio
async def test_new_limiter_instance_preserves_recent_usage(tmp_path):
    path = tmp_path / "limit.db"
    old = TokenLimiter(path, limit=100)
    await old.initialize()
    await old.reserve("a", 100)
    restarted = TokenLimiter(path, limit=100)
    await restarted.initialize()
    with pytest.raises(LimitExceeded):
        await restarted.reserve("a", 1)


@pytest.mark.asyncio
async def test_sqlite_work_does_not_block_the_event_loop(tmp_path):
    def slow_clock():
        time.sleep(0.1)
        return time.time()
    limiter = TokenLimiter(tmp_path / "limit.db", clock=slow_clock)
    await limiter.initialize()
    task = asyncio.create_task(limiter.reserve("a", 1))
    ticks = 0
    while not task.done():
        ticks += 1
        await asyncio.sleep(0.005)
    await task
    assert ticks >= 5


@pytest.mark.asyncio
async def test_two_processes_share_the_same_budget(tmp_path):
    path = tmp_path / "limit.db"
    limiter = TokenLimiter(path, limit=100)
    await limiter.initialize()
    worker = """
import asyncio, json, sys
from limiter import LimitExceeded, TokenLimiter
async def main():
    limiter = TokenLimiter(sys.argv[1], limit=100)
    accepted = 0
    for _ in range(8):
        try:
            await limiter.reserve('a', 10)
            accepted += 1
        except LimitExceeded:
            pass
    print(json.dumps(accepted))
asyncio.run(main())
"""
    processes = [subprocess.Popen([sys.executable, "-c", worker, str(path)], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        accepted = 0
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            accepted += json.loads(stdout)
        assert accepted == 10
        assert charges(path) == 100
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("amount", [0, -1, True, 1.5, "10"])
@pytest.mark.asyncio
async def test_invalid_reservations_are_rejected(tmp_path, amount):
    limiter = TokenLimiter(tmp_path / "limit.db")
    await limiter.initialize()
    with pytest.raises(ValueError):
        await limiter.reserve("a", amount)


def test_memory_database_is_not_allowed():
    with pytest.raises(ValueError):
        TokenLimiter(":memory:")
