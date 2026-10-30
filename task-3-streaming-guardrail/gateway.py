"""Stream checked text from a fixed provider endpoint to the client."""

import math
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

import anyio
import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from redactor import BufferLimitError, Redactor
from sse import MAX_DELTA_CHARS, StreamFormatError, encode_event, event_data, read_events


class GenerateInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]


@dataclass(frozen=True)
class Settings:
    provider_url: str = "http://127.0.0.1:8003/generate"
    timeout_seconds: float = 5.0

    def __post_init__(self):
        url = httpx.URL(self.provider_url)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.fragment:
            raise ValueError("PROVIDER_URL must be an HTTP(S) URL without credentials or fragments")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("PROVIDER_TIMEOUT_SECONDS must be positive and finite")

    @classmethod
    def from_env(cls):
        return cls(os.getenv("PROVIDER_URL", "http://127.0.0.1:8003/generate"),
                   float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "5")))


class ProviderResponse(StreamingResponse):
    def __init__(self, content, upstream):
        super().__init__(content, media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })
        self.upstream = upstream

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also close if sending headers fails before the generator starts.
            with anyio.CancelScope(shield=True):
                await self.upstream.aclose()


async def checked_text(upstream):
    redactor = Redactor()
    try:
        async for name, data in read_events(upstream.aiter_bytes()):
            payload = event_data(data)
            if name == "delta":
                text = payload.get("text")
                if not isinstance(text, str) or len(text) > MAX_DELTA_CHARS:
                    raise StreamFormatError("Invalid text delta")
                safe = redactor.feed(text)
                if safe:
                    yield encode_event("delta", {"text": safe})
            elif name == "done" and payload == {"status": "complete"}:
                safe = redactor.finish()
                if safe:
                    yield encode_event("delta", {"text": safe})
                yield encode_event("done", {"status": "complete"})
                return
            else:
                raise StreamFormatError("Unexpected provider event")
        raise StreamFormatError("Provider ended without done")
    except BufferLimitError:
        yield encode_event("error", {"code": "buffer_limit", "message": "Cannot safely process this text"})
    except httpx.TimeoutException:
        yield encode_event("error", {"code": "provider_timeout", "message": "Provider timed out"})
    except (StreamFormatError, httpx.RequestError):
        yield encode_event("error", {"code": "provider_stream_error", "message": "Provider stream failed"})
    except Exception:
        yield encode_event("error", {"code": "internal_error", "message": "Generation failed"})
    # On failure, never flush unfinished text. Cancellation is not caught above.


def create_app(settings: Settings | None = None,
               provider_transport: httpx.AsyncBaseTransport | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=settings.timeout_seconds, transport=provider_transport,
                                     trust_env=False, follow_redirects=False) as client:
            app.state.provider = client
            yield

    app = FastAPI(title="Streaming PII gateway", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=400, content={"error": {
            "code": "invalid_request", "message": "Provide a prompt between 1 and 4096 characters",
        }})

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/generate")
    async def generate(payload: GenerateInput):
        try:
            request = app.state.provider.build_request("POST", settings.provider_url,
                                                       json={"prompt": payload.prompt},
                                                       headers={"Accept-Encoding": "identity"})
            upstream = await app.state.provider.send(request, stream=True)
        except httpx.TimeoutException:
            return JSONResponse(status_code=504, content={"error": {
                "code": "provider_timeout", "message": "Provider timed out",
            }})
        except httpx.RequestError:
            return JSONResponse(status_code=502, content={"error": {
                "code": "provider_unavailable", "message": "Provider unavailable",
            }})
        except Exception:
            return JSONResponse(status_code=500, content={"error": {
                "code": "internal_error", "message": "Generation failed",
            }})
        media_type = upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        encoding = upstream.headers.get("content-encoding", "identity").lower()
        if upstream.status_code != 200 or media_type != "text/event-stream" or encoding != "identity":
            await upstream.aclose()
            return JSONResponse(status_code=502, content={"error": {
                "code": "provider_unavailable", "message": "Provider unavailable",
            }})
        return ProviderResponse(checked_text(upstream), upstream)

    return app
