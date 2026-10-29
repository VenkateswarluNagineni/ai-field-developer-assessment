"""Authenticate, check admin tool access, then forward the original request."""

import asyncio
import hmac
import logging
import math
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from protocol import RpcError, error_response, load_json, parse_request


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    admin_token: str
    viewer_token: str
    downstream_url: str = "http://127.0.0.1:8001/mcp"
    timeout_seconds: float = 5.0

    def __post_init__(self):
        for token in (self.admin_token, self.viewer_token):
            if not token or not token.isascii() or any(c.isspace() for c in token):
                raise ValueError("ADMIN_TOKEN and VIEWER_TOKEN must be nonempty ASCII tokens without spaces")
        if self.admin_token == self.viewer_token:
            raise ValueError("ADMIN_TOKEN and VIEWER_TOKEN must be different")
        url = urlsplit(self.downstream_url)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.fragment):
            raise ValueError("DOWNSTREAM_URL must be an HTTP(S) URL without credentials or fragments")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("DOWNSTREAM_TIMEOUT_SECONDS must be positive and finite")

    @classmethod
    def from_env(cls):
        return cls(
            admin_token=os.getenv("ADMIN_TOKEN", ""),
            viewer_token=os.getenv("VIEWER_TOKEN", ""),
            downstream_url=os.getenv("DOWNSTREAM_URL", "http://127.0.0.1:8001/mcp"),
            timeout_seconds=float(os.getenv("DOWNSTREAM_TIMEOUT_SECONDS", "5")),
        )


def get_role(request: Request, settings: Settings) -> str | None:
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        return None
    parts = headers[0].split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].isascii():
        return None
    token = parts[1]
    if hmac.compare_digest(token, settings.admin_token):
        return "admin"
    if hmac.compare_digest(token, settings.viewer_token):
        return "viewer"
    return None


def valid_reply(reply, request_id) -> bool:
    if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
        return False
    if type(reply.get("id")) is not type(request_id) or reply.get("id") != request_id:
        return False
    if ("result" in reply) == ("error" in reply):
        return False
    if "result" in reply:
        return isinstance(reply["result"], dict)
    error = reply["error"]
    return (isinstance(error, dict) and type(error.get("code")) is int
            and isinstance(error.get("message"), str))


def create_app(settings: Settings | None = None,
               downstream_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One connection pool per process; close it when the app stops.
        async with httpx.AsyncClient(
            transport=downstream_transport, timeout=settings.timeout_seconds,
            follow_redirects=False, trust_env=False,
        ) as client:
            app.state.downstream = client
            yield

    app = FastAPI(title="MCP security gateway", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        if isinstance(exc, ClientDisconnect):
            raise exc
        logger.error("Unexpected gateway error")
        if getattr(request.state, "is_notification", False):
            return Response(status_code=500)
        return error_response(-32603, "Internal error",
                              getattr(request.state, "rpc_id", None), status=500)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/mcp")
    async def proxy(request: Request):
        role = get_role(request, settings)
        if role is None:
            # No trusted identity yet: do not parse or forward the request body.
            return JSONResponse(status_code=401, content={"error": "Invalid or missing Bearer token"},
                                headers={"WWW-Authenticate": "Bearer"})
        try:
            body = await request.body()
            frame = parse_request(body)
        except RpcError as exc:
            return error_response(exc.code, exc.message, exc.request_id, status=400)

        request_id = frame.get("id")
        # Store only validated routing metadata for the unexpected-error handler.
        request.state.rpc_id = request_id
        request.state.is_notification = "id" not in frame
        if frame["method"] == "tools/call":
            name = frame["params"]["name"]
            if name.startswith("admin_") and role != "admin":
                logger.warning("Blocked admin tool call for viewer")
                return error_response(-32001, "Unauthorized Tool Call", request_id)

        try:
            # The outer deadline also limits total time, not just idle network time.
            async with asyncio.timeout(settings.timeout_seconds):
                upstream = await request.app.state.downstream.post(
                    settings.downstream_url, content=body,
                    # Do not pass the user's Authorization, cookies, or other headers.
                    headers={"Content-Type": "application/json", "Cookie": ""},
                )
        except (TimeoutError, httpx.TimeoutException):
            logger.warning("Downstream timeout")
            return error_response(-32002, "Downstream timeout", request_id, status=504)
        except httpx.RequestError:
            logger.warning("Downstream connection failed")
            return error_response(-32003, "Downstream unavailable", request_id, status=502)

        if 300 <= upstream.status_code < 400 or upstream.status_code >= 500:
            return error_response(-32003, "Downstream unavailable", request_id, status=502)
        if "id" not in frame:
            # A notification has no JSON-RPC response.
            return Response(status_code=204)
        try:
            reply = load_json(upstream.content)
        except (ValueError, RecursionError):
            reply = None
        if not valid_reply(reply, request_id):
            return error_response(-32003, "Invalid downstream response", request_id, status=502)
        if "error" in reply and reply["error"]["code"] == -32603:
            # Internal errors may carry private messages, stack traces, or metadata.
            return error_response(-32603, "Internal error", request_id, status=upstream.status_code)
        return Response(content=upstream.content, status_code=upstream.status_code,
                        media_type="application/json")

    return app
