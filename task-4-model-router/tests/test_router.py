import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import router as router_module
from limiter import LimitExceeded, TokenLimiter
from router import GatewayError, ModelRouter, PROVIDER_TIMEOUT_SECONDS
from test_limiter import charges


def success(input_tokens=4, output_tokens=2):
    return httpx.Response(200, json={"text": "OK", "usage": {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
    }})


async def setup(tmp_path, handler, limit=50_000):
    limiter = TokenLimiter(tmp_path / "router.db", limit=limit)
    await limiter.initialize()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    router = ModelRouter(client, limiter, "http://primary/completions", "http://backup/completions")
    return router, limiter, client


@pytest.mark.asyncio
async def test_primary_success_records_actual_usage(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return success()
    router, limiter, client = await setup(tmp_path, respond)
    async with client:
        result = await router.complete("tenant", "demo", 10)
    assert result["provider"] == "primary"
    assert len(calls) == 1
    assert charges(limiter.path) == 6
    assert json.loads(calls[0].content) == {"prompt": "demo", "max_output_tokens": 10}


@pytest.mark.asyncio
async def test_429_falls_back_with_the_same_payload_and_refunds_primary(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, text="private-rate-limit") if request.url.host == "primary" else success()
    router, limiter, client = await setup(tmp_path, respond, limit=14)
    async with client:
        result = await router.complete("tenant", "demo", 10)
    assert result["provider"] == "backup"
    assert [request.url.host for request in calls] == ["primary", "backup"]
    assert calls[0].content == calls[1].content
    assert charges(limiter.path) == 6


@pytest.mark.asyncio
async def test_real_3000ms_deadline_cancels_primary_and_keeps_unknown_charge(tmp_path):
    cancelled = asyncio.Event()
    calls = []
    async def respond(request):
        calls.append(request)
        if request.url.host == "primary":
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        return success()
    router, limiter, client = await setup(tmp_path, respond)
    started = time.monotonic()
    async with client:
        result = await router.complete("tenant", "demo", 10)
    elapsed = time.monotonic() - started
    assert PROVIDER_TIMEOUT_SECONDS == 3.0
    assert 2.9 <= elapsed < 6
    assert cancelled.is_set()
    assert result["provider"] == "backup" and len(calls) == 2
    assert charges(limiter.path) == 14 + 6


@pytest.mark.asyncio
async def test_backup_needs_its_own_budget_after_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(router_module, "PROVIDER_TIMEOUT_SECONDS", 0.01)
    calls = []
    async def respond(request):
        calls.append(request)
        await asyncio.sleep(10)
    router, limiter, client = await setup(tmp_path, respond, limit=14)
    async with client:
        with pytest.raises(LimitExceeded):
            await router.complete("tenant", "demo", 10)
    assert len(calls) == 1
    assert charges(limiter.path) == 14


@pytest.mark.asyncio
async def test_two_timeouts_stop_after_one_backup_and_keep_both_charges(tmp_path, monkeypatch):
    monkeypatch.setattr(router_module, "PROVIDER_TIMEOUT_SECONDS", 0.01)
    calls = []
    async def respond(request):
        calls.append(request)
        await asyncio.sleep(10)
    router, limiter, client = await setup(tmp_path, respond)
    async with client:
        with pytest.raises(GatewayError) as error:
            await router.complete("tenant", "demo", 10)
    assert error.value.status == 504
    assert len(calls) == 2
    assert charges(limiter.path) == 28


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 500, 503, 302])
async def test_other_http_failures_do_not_trigger_fallback(tmp_path, status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="private-host stack trace", headers={"Location": "http://private-host"})
    router, limiter, client = await setup(tmp_path, respond)
    async with client:
        with pytest.raises(GatewayError) as error:
            await router.complete("tenant", "demo", 10)
    assert len(calls) == 1 and charges(limiter.path) == 14
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_backup_429_is_terminal_and_both_rejections_cost_zero(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, text="private")
    router, limiter, client = await setup(tmp_path, respond)
    async with client:
        with pytest.raises(GatewayError):
            await router.complete("tenant", "demo", 10)
    assert len(calls) == 2 and charges(limiter.path) == 0


@pytest.mark.asyncio
async def test_unknown_network_failure_keeps_reservation_without_retry(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ReadError("private-network-details")
    router, limiter, client = await setup(tmp_path, respond)
    async with client:
        with pytest.raises(GatewayError):
            await router.complete("tenant", "demo", 10)
    assert len(calls) == 1 and charges(limiter.path) == 14


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"private-invalid-json", b'{"text":"private"}',
    b'{"text":"OK","usage":{"input_tokens":true,"output_tokens":2}}',
    b'{"text":"OK","usage":{"input_tokens":-1,"output_tokens":2}}'])
async def test_invalid_usage_keeps_the_full_charge(tmp_path, body):
    router, limiter, client = await setup(tmp_path, lambda request: httpx.Response(200, content=body))
    async with client:
        with pytest.raises(GatewayError) as error:
            await router.complete("tenant", "demo", 10)
    assert error.value.code == "invalid_provider_response"
    assert charges(limiter.path) == 14


@pytest.mark.asyncio
async def test_reported_usage_overrun_is_recorded_and_response_rejected(tmp_path):
    router, limiter, client = await setup(tmp_path, lambda request: success(100, 100))
    async with client:
        with pytest.raises(GatewayError):
            await router.complete("tenant", "demo", 10)
    assert charges(limiter.path) == 200


@pytest.mark.asyncio
async def test_external_cancellation_does_not_start_backup_or_refund(tmp_path):
    started = asyncio.Event()
    calls = []
    async def respond(request):
        calls.append(request)
        started.set()
        await asyncio.Event().wait()
    router, limiter, client = await setup(tmp_path, respond)
    async with client:
        task = asyncio.create_task(router.complete("tenant", "demo", 10))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) == 1 and charges(limiter.path) == 14


@pytest.mark.asyncio
async def test_utf8_input_budget_is_not_a_word_count(tmp_path):
    router, limiter, client = await setup(tmp_path, lambda request: success(4, 2))
    async with client:
        result = await router.complete("tenant", "🙂", 2)
    assert result["usage"]["input_tokens"] == 4
    assert charges(limiter.path) == 6
