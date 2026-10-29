"""Check gateway decisions and observe every request that reaches downstream."""

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import Settings, create_app
from mock_server import app as mock_app


ADMIN = "test-only-admin-token"
VIEWER = "test-only-viewer-token"
SETTINGS = Settings(ADMIN, VIEWER, downstream_url="http://downstream/mcp")


def rpc(method="tools/list", params=None, request_id=7):
    frame = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def auth(token=VIEWER):
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def gateway_client(handler, settings=SETTINGS):
    app = create_app(settings, httpx.MockTransport(handler))
    # HTTPX's ASGI transport does not run application startup/shutdown itself.
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
            yield client


@pytest_asyncio.fixture
async def system():
    calls = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(mock_app)) as downstream:
        async def forward(request):
            calls.append(request)
            return await downstream.send(request)
        async with gateway_client(forward) as client:
            yield client, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [ADMIN, VIEWER])
async def test_listing_is_forwarded_unchanged(system, token):
    client, calls = system
    body = b'{ "jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {"_meta":{"trace":"demo"}} }'
    response = await client.post("/mcp", content=body, headers=auth(token))
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].content == body
    assert calls[0].url == "http://downstream/mcp"
    assert response.json()["id"] == "list-1"
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == {
        "get_status", "admin_reset_key",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("token,name,allowed", [
    (VIEWER, "get_status", True), (ADMIN, "get_status", True),
    (ADMIN, "admin_reset_key", True), (VIEWER, "admin_reset_key", False),
    (VIEWER, "admin_any_future_tool", False), (VIEWER, "admin_", False),
])
async def test_role_and_tool_rules(system, token, name, allowed):
    client, calls = system
    response = await client.post("/mcp", json=rpc("tools/call", {"name": name, "arguments": {}}),
                                 headers=auth(token))
    assert response.status_code == 200
    if allowed:
        assert len(calls) == 1
        assert response.json()["result"]["isError"] is False
    else:
        assert calls == []
        assert response.json() == {"jsonrpc": "2.0", "id": 7,
                                   "error": {"code": -32001, "message": "Unauthorized Tool Call"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "Bearer unknown",
                                          "Bearer wrong token", "Bearer tóken"])
async def test_missing_or_invalid_tokens_never_forward(system, header):
    client, calls = system
    headers = {} if header is None else {"Authorization": header}
    # HTTP headers are Latin-1 bytes; this also checks non-ASCII token handling.
    headers = [(key.encode(), value.encode("latin-1")) for key, value in headers.items()]
    response = await client.post("/mcp", content=b"not even JSON", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert calls == []


@pytest.mark.asyncio
async def test_duplicate_auth_headers_are_rejected(system):
    client, calls = system
    response = await client.post("/mcp", json=rpc(), headers=[
        ("Authorization", f"Bearer {ADMIN}"), ("Authorization", f"Bearer {VIEWER}"),
    ])
    assert response.status_code == 401
    assert calls == []


@pytest.mark.asyncio
async def test_bearer_scheme_is_case_insensitive(system):
    client, calls = system
    response = await client.post("/mcp", json=rpc(), headers={"Authorization": f"bearer {VIEWER}"})
    assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_client_credentials_and_headers_are_not_forwarded(system):
    client, calls = system
    response = await client.post("/mcp", json=rpc(), headers={
        **auth(), "Cookie": "session=private", "X-Private": "private-value",
    })
    assert response.status_code == 200
    assert "authorization" not in calls[0].headers
    assert calls[0].headers.get("cookie", "") == ""
    assert "x-private" not in calls[0].headers
    assert calls[0].headers["host"] == "downstream"


@pytest.mark.asyncio
@pytest.mark.parametrize("body,code", [
    (b'{"jsonrpc":', -32700), (b'\xff', -32700), (b'NaN', -32700),
    (b'[]', -32600), (b'null', -32600), (b'{}', -32600),
    (b'{"jsonrpc":"1.0","id":7,"method":"tools/list"}', -32600),
    (b'{"jsonrpc":"2.0","id":true,"method":"tools/list"}', -32600),
    (b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":[]}', -32602),
    (b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":123}}', -32602),
    (b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":""}}', -32602),
    (b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"get_status","arguments":[]}}', -32602),
    (b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"admin_reset_key","name":"get_status"}}', -32600),
    (b'{"jsonrpc":"2.0","id":7,"method":"tools/call","method":"tools/list"}', -32600),
])
async def test_bad_requests_never_forward(system, body, code):
    client, calls = system
    response = await client.post("/mcp", content=body, headers=auth())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert calls == []


@pytest.mark.asyncio
async def test_escaped_admin_name_is_still_blocked(system):
    client, calls = system
    response = await client.post("/mcp", headers=auth(), content=(
        b'{"jsonrpc":"2.0","id":7,"method":"tools/call",'
        b'"params":{"name":"\\u0061dmin_reset_key"}}'
    ))
    assert response.json()["error"]["code"] == -32001
    assert calls == []


@pytest.mark.asyncio
async def test_claiming_admin_in_the_body_does_not_change_the_token_role(system):
    client, calls = system
    frame = rpc("tools/call", {"name": "admin_reset_key", "role": "admin"})
    frame["role"] = "admin"
    response = await client.post("/mcp", json=frame, headers=auth(VIEWER))
    assert response.json()["error"]["code"] == -32001
    assert calls == []


@pytest.mark.asyncio
async def test_missing_request_id_cannot_execute_a_tool(system):
    client, calls = system
    frame = rpc("tools/call", {"name": "admin_reset_key"})
    del frame["id"]
    response = await client.post("/mcp", json=frame, headers=auth(ADMIN))
    assert response.status_code == 400
    assert calls == []


@pytest.mark.asyncio
async def test_notifications_have_no_jsonrpc_reply(system):
    client, calls = system
    frame = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    response = await client.post("/mcp", json=frame, headers=auth())
    assert len(calls) == 1
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", [0, "call-1"])
async def test_request_ids_survive_blocking(system, request_id):
    client, calls = system
    response = await client.post("/mcp", json=rpc("tools/call", {"name": "admin_reset_key"}, request_id),
                                 headers=auth())
    assert response.json()["id"] == request_id
    assert calls == []


@pytest.mark.asyncio
async def test_other_methods_and_downstream_errors_are_forwarded(system):
    client, calls = system
    for method, expected in [("ping", None), ("unknown_method", -32601)]:
        response = await client.post("/mcp", json=rpc(method), headers=auth())
        if expected is None:
            assert response.json()["result"] == {}
        else:
            assert response.json()["error"]["code"] == expected
    response = await client.post("/mcp", json=rpc("tools/call", {"name": "missing_tool"}), headers=auth())
    assert response.json()["error"]["code"] == -32602
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_downstream_response_bytes_and_http_status_are_preserved():
    body = b'{ "jsonrpc":"2.0", "id":7, "error":{"code":-32602,"message":"Invalid params"} }'
    def respond(request):
        return httpx.Response(400, content=body, headers={"Set-Cookie": "private=session"})
    async with gateway_client(respond) as client:
        response = await client.post("/mcp", json=rpc(), headers=auth())
    assert response.status_code == 400
    assert response.content == body
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status,code", [
    (httpx.ConnectError("private-host"), 502, -32003),
    (httpx.ReadTimeout("private-host"), 504, -32002),
])
async def test_network_errors_are_sanitized(failure, status, code):
    def fail(request):
        raise failure
    async with gateway_client(fail) as client:
        response = await client.post("/mcp", json=rpc(), headers=auth())
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["id"] == 7
    assert "private-host" not in response.text


@pytest.mark.asyncio
async def test_total_deadline_cancels_downstream_without_retry():
    calls = []
    cancelled = asyncio.Event()
    async def slow(request):
        calls.append(request)
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    settings = Settings(ADMIN, VIEWER, timeout_seconds=0.02)
    async with gateway_client(slow, settings) as client:
        response = await client.post("/mcp", json=rpc(), headers=auth())
    assert response.status_code == 504
    assert response.json()["error"]["code"] == -32002
    assert cancelled.is_set()
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [
    (500, b"Traceback: private-host"), (302, b"private-host"),
    (200, b"not JSON"), (200, b'{"jsonrpc":"2.0","id":999,"result":{}}'),
    (200, b'{"jsonrpc":"2.0","id":7,"result":{},"error":{}}'),
    (200, b'{"jsonrpc":"2.0","id":7,"error":{"code":"bad","message":"bad"}}'),
    (200, b'{"jsonrpc":"2.0","id":7,"result":[]}'),
    (204, b""),
])
async def test_bad_upstream_responses_are_sanitized(status, body):
    def respond(request):
        return httpx.Response(status, content=body, headers={"Location": "http://private-host"})
    async with gateway_client(respond) as client:
        response = await client.post("/mcp", json=rpc(), headers=auth())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == -32003
    assert "private-host" not in response.text
    assert "location" not in response.headers


@pytest.mark.asyncio
async def test_parallel_requests_keep_their_ids_and_roles(system):
    client, calls = system
    async def call(request_id):
        blocked = request_id % 2 == 0
        name = "admin_reset_key" if blocked else "get_status"
        response = await client.post("/mcp", json=rpc("tools/call", {"name": name}, request_id), headers=auth())
        assert response.json()["id"] == request_id
        assert ("error" in response.json()) == blocked
    await asyncio.gather(*(call(i) for i in range(20)))
    assert len(calls) == 10


@pytest.mark.asyncio
async def test_tokens_do_not_appear_in_gateway_logs(system, caplog):
    client, calls = system
    with caplog.at_level(logging.WARNING, logger="gateway"):
        await client.post("/mcp", json=rpc("tools/call", {"name": "admin_reset_key"}), headers=auth())
    assert "Blocked admin tool call" in caplog.text
    assert VIEWER not in caplog.text
    assert ADMIN not in caplog.text


@pytest.mark.parametrize("overrides", [
    {"admin_token": ""}, {"viewer_token": ""}, {"viewer_token": ADMIN},
    {"admin_token": "has spaces"}, {"viewer_token": "tóken"},
    {"downstream_url": "file:///tmp/server"}, {"downstream_url": "http://user:password@host/mcp"},
    {"timeout_seconds": 0}, {"timeout_seconds": float("inf")}, {"timeout_seconds": float("nan")},
])
def test_invalid_configuration_fails_at_startup(overrides):
    values = {"admin_token": ADMIN, "viewer_token": VIEWER, **overrides}
    with pytest.raises(ValueError):
        Settings(**values)


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("VIEWER_TOKEN", VIEWER)
    monkeypatch.setenv("DOWNSTREAM_URL", "http://localhost:8001/mcp")
    monkeypatch.setenv("DOWNSTREAM_TIMEOUT_SECONDS", "2.5")
    settings = Settings.from_env()
    assert settings.admin_token == ADMIN
    assert settings.viewer_token == VIEWER
    assert settings.downstream_url == "http://localhost:8001/mcp"
    assert settings.timeout_seconds == 2.5


def test_no_default_tokens(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("VIEWER_TOKEN", raising=False)
    with pytest.raises(ValueError):
        create_app()
