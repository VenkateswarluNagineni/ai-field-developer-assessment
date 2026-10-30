import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from starlette.requests import ClientDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import ProviderResponse, Settings, checked_text, create_app
from redactor import MAX_PENDING
from sse import MAX_DELTA_CHARS, MAX_EVENT_BYTES, SSEParser, encode_event


class ProviderStream(httpx.AsyncByteStream):
    def __init__(self, parts=(), failure=None):
        self.parts = parts
        self.failure = failure
        self.closed = False
        self.reads = 0

    async def __aiter__(self):
        for part in self.parts:
            self.reads += 1
            yield part
        if self.failure:
            raise self.failure

    async def aclose(self):
        self.closed = True


def delta(text):
    return encode_event("delta", {"text": text})


DONE = encode_event("done", {"status": "complete"})


def decode(body):
    return [(name, json.loads(data)) for name, data in SSEParser().feed(body.decode())]


@asynccontextmanager
async def gateway_client(stream=None, status=200, headers=None, failure=None):
    calls = []
    def respond(request):
        calls.append(request)
        if failure:
            raise failure
        return httpx.Response(status, headers=headers or {"Content-Type": "text/event-stream"}, stream=stream)
    app = create_app(Settings("http://provider/generate"), httpx.MockTransport(respond))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
            yield client, calls


@pytest.mark.asyncio
async def test_redaction_end_to_end_and_safe_final_flush():
    stream = ProviderStream([delta("Hello! alex@"), delta("example.com; 123-"),
                             delta("45-6789; 4111 "), delta("1111 1111 1111"), DONE])
    async with gateway_client(stream) as (client, calls):
        response = await client.post("/generate", json={"prompt": "demo"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = decode(response.content)
    text = "".join(data["text"] for name, data in events if name == "delta")
    assert text == "Hello! [REDACTED]; [REDACTED]; [REDACTED]"
    assert events[-1] == ("done", {"status": "complete"})
    assert "alex" not in response.text and "4111" not in response.text
    assert stream.closed
    assert json.loads(calls[0].content) == {"prompt": "demo"}
    assert calls[0].url == "http://provider/generate"
    assert calls[0].headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
async def test_headers_are_not_forwarded_from_the_client():
    stream = ProviderStream([delta("Safe!"), DONE])
    async with gateway_client(stream) as (client, calls):
        response = await client.post("/generate", json={"prompt": "demo"},
                                     headers={"Authorization": "Bearer private", "Cookie": "private=value"})
    assert response.status_code == 200
    assert "authorization" not in calls[0].headers
    assert "cookie" not in calls[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"prompt": ""}, {"prompt": " "}, {"prompt": 123},
                                     {"prompt": "x" * 4097}, {"prompt": "demo", "provider_url": "http://other"}])
async def test_invalid_input_is_rejected_without_echoing_or_forwarding(payload):
    async with gateway_client() as (client, calls):
        response = await client.post("/generate", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert calls == []
    assert "xxxxxxxx" not in response.text


@pytest.mark.asyncio
async def test_malformed_request_json_is_safe():
    async with gateway_client() as (client, calls):
        response = await client.post("/generate", content=b'{"prompt":"private',
                                     headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert "private" not in response.text
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status,code", [
    (httpx.ConnectError("private-host"), 502, "provider_unavailable"),
    (httpx.ConnectTimeout("private-host"), 504, "provider_timeout"),
    (RuntimeError("private-details"), 500, "internal_error"),
])
async def test_failures_before_headers_are_normal_http_errors(failure, status, code):
    async with gateway_client(failure=failure) as (client, calls):
        response = await client.post("/generate", json={"prompt": "demo"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers", [
    (429, {"Content-Type": "text/event-stream"}),
    (500, {"Content-Type": "text/event-stream"}),
    (302, {"Content-Type": "text/event-stream", "Location": "http://private-host"}),
    (200, {"Content-Type": "application/json"}),
    (200, {"Content-Type": "text/event-stream", "Content-Encoding": "gzip"}),
])
async def test_unsuitable_upstream_responses_are_closed_without_reading(status, headers):
    stream = ProviderStream([b"private-provider-body"])
    async with gateway_client(stream, status, headers) as (client, calls):
        response = await client.post("/generate", json={"prompt": "demo"})
    assert response.status_code == 502
    assert "private" not in response.text
    assert stream.closed and stream.reads == 0
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code", [
    (httpx.ReadTimeout("private-timeout"), "provider_timeout"),
    (httpx.ReadError("private-error"), "provider_stream_error"),
    (RuntimeError("private-runtime"), "internal_error"),
])
async def test_midstream_errors_do_not_flush_unchecked_text(failure, code):
    stream = ProviderStream([delta("Hello! alex@")], failure)
    async with gateway_client(stream) as (client, calls):
        response = await client.post("/generate", json={"prompt": "demo"})
    assert response.status_code == 200
    events = decode(response.content)
    assert events[0] == ("delta", {"text": "Hello! "})
    assert events[-1][0] == "error" and events[-1][1]["code"] == code
    assert not any(name == "done" for name, _ in events)
    assert "alex" not in response.text and "private" not in response.text
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("parts,code", [
    ([delta("alex@")], "provider_stream_error"),
    ([b'event: delta\ndata: {bad}\n\n'], "provider_stream_error"),
    ([b'event: delta\ndata: {"text":123}\n\n'], "provider_stream_error"),
    ([b'event: done\ndata: {"status":"failed"}\n\n'], "provider_stream_error"),
    ([encode_event("error", {"stack": "private-details"})], "provider_stream_error"),
    ([b'event: delta\ndata: {"text":"alex@"}\n'], "provider_stream_error"),
    ([b"\xff"], "provider_stream_error"),
    ([b"x" * (MAX_EVENT_BYTES + 1)], "provider_stream_error"),
    ([delta("x" * (MAX_DELTA_CHARS + 1))], "provider_stream_error"),
    ([delta("x" * (MAX_PENDING + 1))], "buffer_limit"),
])
async def test_bad_or_unbounded_streams_stop_safely(parts, code):
    stream = ProviderStream(parts)
    async with gateway_client(stream) as (client, calls):
        response = await client.post("/generate", json={"prompt": "demo"})
    assert decode(response.content)[-1][1]["code"] == code
    assert "alex" not in response.text and "private" not in response.text
    assert not any(name == "done" for name, _ in decode(response.content))
    assert stream.closed


@pytest.mark.asyncio
async def test_safe_output_does_not_wait_for_generation_to_finish():
    release_done = asyncio.Event()
    class GatedStream(ProviderStream):
        async def __aiter__(self):
            yield delta("Safe first words! ")
            await release_done.wait()
            yield DONE
    upstream = httpx.Response(200, stream=GatedStream())
    iterator = checked_text(upstream)
    first = await asyncio.wait_for(anext(iterator), timeout=1)
    assert decode(first) == [("delta", {"text": "Safe first words! "})]
    assert not release_done.is_set()
    release_done.set()
    assert decode(await anext(iterator))[0][0] == "done"
    await iterator.aclose()
    await upstream.aclose()


def scope(version="2.3"):
    return {"type": "http", "method": "POST", "path": "/generate", "headers": [],
            "asgi": {"version": "3.0", "spec_version": version}}


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_provider_read_and_closes_connection():
    waiting = asyncio.Event()
    cancelled = asyncio.Event()
    class WaitingStream(ProviderStream):
        async def __aiter__(self):
            yield delta("Hello! ")
            waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    stream = WaitingStream()
    upstream = httpx.Response(200, stream=stream)
    response = ProviderResponse(checked_text(upstream), upstream)
    async def receive():
        await waiting.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        pass
    await asyncio.wait_for(response(scope(), receive, send), timeout=1)
    assert cancelled.is_set() and stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on", ["http.response.start", "http.response.body"])
async def test_send_failure_also_closes_upstream(fail_on):
    stream = ProviderStream([delta("Hello! "), DONE])
    upstream = httpx.Response(200, stream=stream)
    response = ProviderResponse(checked_text(upstream), upstream)
    async def send(message):
        if message["type"] == fail_on:
            raise OSError("client closed")
    async def receive():
        await asyncio.Event().wait()
    with pytest.raises(ClientDisconnect):
        await response(scope("2.4"), receive, send)
    assert stream.closed


@pytest.mark.asyncio
async def test_slow_client_applies_backpressure_to_provider_reads():
    sending = asyncio.Event()
    release = asyncio.Event()
    stream = ProviderStream([delta("First! "), delta("Second! "), DONE])
    upstream = httpx.Response(200, stream=stream)
    response = ProviderResponse(checked_text(upstream), upstream)
    async def send(message):
        if message["type"] == "http.response.body" and message.get("body") and not sending.is_set():
            sending.set()
            await release.wait()
    async def receive():
        await asyncio.Event().wait()
    task = asyncio.create_task(response(scope("2.4"), receive, send))
    try:
        await asyncio.wait_for(sending.wait(), timeout=1)
        assert stream.reads == 1
        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert stream.reads == 3 and stream.closed
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_parallel_generations_do_not_share_redaction_state():
    async def generate(index):
        text = f"Stream {index}! user{index}@example.com; 123-45-6789."
        stream = ProviderStream([delta(char) for char in text] + [DONE])
        async with gateway_client(stream) as (client, calls):
            response = await client.post("/generate", json={"prompt": "demo"})
        result = "".join(data["text"] for name, data in decode(response.content) if name == "delta")
        assert result == f"Stream {index}! [REDACTED]; [REDACTED]."
        assert stream.closed
    await asyncio.gather(*(generate(index) for index in range(25)))


@pytest.mark.parametrize("url", ["file:///tmp/model", "http://user:password@host/generate", "http://host/#fragment"])
def test_invalid_provider_urls_are_rejected(url):
    with pytest.raises(ValueError):
        Settings(url)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeouts_are_rejected(timeout):
    with pytest.raises(ValueError):
        Settings(timeout_seconds=timeout)


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("PROVIDER_URL", "http://localhost:8003/generate")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "2")
    assert Settings.from_env() == Settings("http://localhost:8003/generate", 2)
