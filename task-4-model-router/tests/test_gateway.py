import hashlib
import json
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import Settings, create_app
from test_limiter import charges


KEY_A, KEY_B = "test-only-tenant-a", "test-only-tenant-b"


@asynccontextmanager
async def gateway_client(tmp_path, handler=None, limit=50_000, raise_exceptions=True):
    calls = []
    def respond(request):
        calls.append(request)
        if handler:
            return handler(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"text": "OK", "usage": {
            "input_tokens": len(body["prompt"].encode()), "output_tokens": 2,
        }})
    settings = Settings((KEY_A, KEY_B), str(tmp_path / "gateway.db"), token_limit=limit)
    app = create_app(settings, httpx.MockTransport(respond))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app, raise_app_exceptions=raise_exceptions),
                                     base_url="http://gateway") as client:
            yield client, calls, app, settings


def auth(key=KEY_A):
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.asyncio
async def test_default_limit_and_success_payload(tmp_path):
    async with gateway_client(tmp_path) as (client, calls, app, settings):
        response = await client.post("/completions", json={"prompt": "demo"}, headers=auth())
    assert settings.token_limit == 50_000
    assert response.status_code == 200
    assert response.json() == {"text": "OK", "provider": "primary", "usage": {"input_tokens": 4, "output_tokens": 2}}
    assert len(calls) == 1
    assert charges(settings.database_path) == 6


@pytest.mark.asyncio
async def test_limits_and_identity_are_per_api_key(tmp_path):
    async with gateway_client(tmp_path, limit=6) as (client, calls, app, settings):
        body = {"prompt": "demo", "max_output_tokens": 2}
        assert (await client.post("/completions", json=body, headers=auth())).status_code == 200
        denied = await client.post("/completions", json=body, headers=auth())
        assert denied.status_code == 429
        assert denied.json()["error"]["code"] == "token_limit_exceeded"
        assert 1 <= int(denied.headers["retry-after"]) <= 60
        assert (await client.post("/completions", json=body, headers=auth(KEY_B))).status_code == 200
        assert len(calls) == 2
    assert charges(settings.database_path, hashlib.sha256(KEY_A.encode()).hexdigest()) == 6


@pytest.mark.asyncio
async def test_keys_and_client_headers_are_not_stored_or_forwarded(tmp_path):
    async with gateway_client(tmp_path) as (client, calls, app, settings):
        response = await client.post("/completions", json={"prompt": "private-prompt"},
                                     headers={**auth(), "Cookie": "private-cookie", "X-Private": "private-header"})
    assert response.status_code == 200
    assert "authorization" not in calls[0].headers
    assert calls[0].headers.get("cookie", "") == ""
    assert "x-private" not in calls[0].headers
    for path in tmp_path.glob("gateway.db*"):
        data = path.read_bytes()
        assert KEY_A.encode() not in data
        assert b"private-prompt" not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer", "Bearer unknown", "Bearer wrong token"])
async def test_invalid_auth_never_calls_a_provider(tmp_path, header):
    async with gateway_client(tmp_path) as (client, calls, app, settings):
        response = await client.post("/completions", json={"prompt": "demo"},
                                     headers={} if header is None else {"Authorization": header})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert calls == [] and charges(settings.database_path) == 0


@pytest.mark.asyncio
async def test_duplicate_auth_is_rejected(tmp_path):
    async with gateway_client(tmp_path) as (client, calls, app, settings):
        response = await client.post("/completions", json={"prompt": "demo"}, headers=[
            ("Authorization", f"Bearer {KEY_A}"), ("Authorization", f"Bearer {KEY_B}"),
        ])
    assert response.status_code == 401 and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"prompt": ""}, {"prompt": 7}, {"prompt": "x" * 4097},
    {"prompt": "demo", "max_output_tokens": 0}, {"prompt": "demo", "max_output_tokens": True},
    {"prompt": "demo", "max_output_tokens": 4097}, {"prompt": "demo", "max_output_tokens": "10"},
    {"prompt": "demo", "tenant_id": "other"}])
async def test_invalid_inputs_are_safe_and_do_not_call_providers(tmp_path, body):
    async with gateway_client(tmp_path) as (client, calls, app, settings):
        response = await client.post("/completions", json=body, headers=auth())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "xxxx" not in response.text and calls == []


@pytest.mark.asyncio
async def test_backup_failure_returns_one_safe_error_shape(tmp_path):
    def respond(request):
        status = 429 if request.url.port == 8005 else 500
        return httpx.Response(status, text="private upstream stack trace")
    async with gateway_client(tmp_path, respond) as (client, calls, app, settings):
        response = await client.post("/completions", json={"prompt": "demo"}, headers=auth())
    assert response.status_code == 502
    assert response.json() == {"error": {"code": "providers_unavailable", "message": "Unable to complete the request"}}
    assert len(calls) == 2 and "private" not in response.text


@pytest.mark.asyncio
async def test_insufficient_backup_budget_is_a_tenant_limit_error(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("private-details")
    async with gateway_client(tmp_path, timeout, limit=14) as (client, calls, app, settings):
        response = await client.post("/completions", json={"prompt": "demo", "max_output_tokens": 10}, headers=auth())
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "token_limit_exceeded"
    assert len(calls) == 1 and charges(settings.database_path) == 14


@pytest.mark.asyncio
async def test_database_failure_fails_closed(tmp_path):
    async with gateway_client(tmp_path) as (client, calls, app, settings):
        async def unavailable(*args):
            raise sqlite3.OperationalError("private database path")
        app.state.router.limiter.reserve = unavailable
        response = await client.post("/completions", json={"prompt": "demo"}, headers=auth())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "accounting_unavailable"
    assert calls == [] and "private" not in response.text


@pytest.mark.asyncio
async def test_unexpected_error_is_sanitized(tmp_path):
    def broken(request):
        raise RuntimeError("private implementation detail")
    async with gateway_client(tmp_path, broken, raise_exceptions=False) as (client, calls, app, settings):
        response = await client.post("/completions", json={"prompt": "demo"}, headers=auth())
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Unable to complete the request"}}
    assert "private" not in response.text


@pytest.mark.parametrize("overrides", [
    {"tenant_keys": ()}, {"tenant_keys": (KEY_A, KEY_A)}, {"tenant_keys": ("has spaces",)},
    {"tenant_keys": ("tóken",)}, {"database_path": ":memory:"}, {"token_limit": 0},
    {"primary_url": "file:///tmp/provider"}, {"backup_url": "http://user:pass@host/model"},
])
def test_bad_settings_fail_at_startup(overrides):
    with pytest.raises(ValueError):
        Settings(**{"tenant_keys": (KEY_A,), **overrides})


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("TENANT_API_KEYS", f"{KEY_A}, {KEY_B}")
    monkeypatch.setenv("TOKENS_PER_MINUTE", "1234")
    monkeypatch.setenv("SQLITE_PATH", "data/custom.db")
    settings = Settings.from_env()
    assert settings.tenant_keys == (KEY_A, KEY_B)
    assert settings.token_limit == 1234 and settings.database_path == "data/custom.db"


def test_no_default_api_keys(monkeypatch):
    monkeypatch.delenv("TENANT_API_KEYS", raising=False)
    with pytest.raises(ValueError):
        create_app()
