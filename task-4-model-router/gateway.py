"""HTTP endpoint for the SQLite-backed completion router."""

import hashlib
import hmac
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.requests import ClientDisconnect

from limiter import LimitExceeded, TokenLimiter
from router import GatewayError, ModelRouter, PROVIDER_TIMEOUT_SECONDS


class CompletionInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    max_output_tokens: int = Field(default=128, ge=1, le=4096)


@dataclass(frozen=True)
class Settings:
    tenant_keys: tuple[str, ...]
    database_path: str = "data/router.sqlite3"
    primary_url: str = "http://127.0.0.1:8005/completions"
    backup_url: str = "http://127.0.0.1:8006/completions"
    token_limit: int = 50_000

    def __post_init__(self):
        if not self.tenant_keys or len(set(self.tenant_keys)) != len(self.tenant_keys):
            raise ValueError("Configure distinct TENANT_API_KEYS")
        for key in self.tenant_keys:
            if not key or not key.isascii() or any(char.isspace() for char in key):
                raise ValueError("Tenant keys must be nonempty ASCII values without spaces")
        for value in (self.primary_url, self.backup_url):
            url = httpx.URL(value)
            if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.fragment:
                raise ValueError("Provider URLs must use HTTP(S) without credentials or fragments")
        if type(self.token_limit) is not int or self.token_limit <= 0:
            raise ValueError("TOKENS_PER_MINUTE must be positive")
        if not self.database_path or self.database_path == ":memory:":
            raise ValueError("Use an on-disk SQLite database")

    @classmethod
    def from_env(cls):
        return cls(
            tenant_keys=tuple(key.strip() for key in os.getenv("TENANT_API_KEYS", "").split(",") if key.strip()),
            database_path=os.getenv("SQLITE_PATH", "data/router.sqlite3"),
            primary_url=os.getenv("PRIMARY_URL", "http://127.0.0.1:8005/completions"),
            backup_url=os.getenv("BACKUP_URL", "http://127.0.0.1:8006/completions"),
            token_limit=int(os.getenv("TOKENS_PER_MINUTE", "50000")),
        )


def error(status, code, message, headers=None):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


def tenant_identity(request, settings):
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        return None
    parts = headers[0].split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].isascii():
        return None
    if any(hmac.compare_digest(parts[1], key) for key in settings.tenant_keys):
        return hashlib.sha256(parts[1].encode()).hexdigest()
    return None


def create_app(settings: Settings | None = None, provider_transport=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        limiter = TokenLimiter(settings.database_path, limit=settings.token_limit)
        await limiter.initialize()
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SECONDS, transport=provider_transport,
                                     follow_redirects=False, trust_env=False) as client:
            app.state.router = ModelRouter(client, limiter, settings.primary_url, settings.backup_url)
            yield

    app = FastAPI(title="SQLite model router", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return error(400, "invalid_request", "Provide a valid prompt and max_output_tokens")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        if isinstance(exc, ClientDisconnect):
            raise exc
        return error(500, "internal_error", "Unable to complete the request")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/completions")
    async def complete(request: Request, payload: CompletionInput):
        identity = tenant_identity(request, settings)
        if identity is None:
            return error(401, "unauthorized", "Invalid or missing API key", {"WWW-Authenticate": "Bearer"})
        try:
            return await app.state.router.complete(identity, payload.prompt, payload.max_output_tokens)
        except LimitExceeded as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
            return error(429, "token_limit_exceeded", "Tenant token limit exceeded", headers)
        except GatewayError as exc:
            return error(exc.status, exc.code, exc.message)
        except sqlite3.Error:
            # A busy or unavailable database must not bypass the limiter.
            return error(503, "accounting_unavailable", "Token accounting is unavailable")

    return app
