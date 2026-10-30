"""Local completion providers. The primary defaults to 429 to show fallback."""

import asyncio
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from gateway import CompletionInput


def create_mock(mode):
    if mode not in {"success", "rate_limit", "timeout", "server_error"}:
        raise ValueError("Use success, rate_limit, timeout, or server_error")
    app = FastAPI(title="Mock completion provider")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/completions")
    async def complete(payload: CompletionInput):
        if mode == "rate_limit":
            return JSONResponse(status_code=429, content={"private_debug": "mock provider rate limit"})
        if mode == "timeout":
            await asyncio.sleep(10)
        if mode == "server_error":
            return JSONResponse(status_code=500, content={"private_debug": "mock provider stack trace"})
        text = "Mock completion."[:payload.max_output_tokens]
        return {"text": text, "usage": {"input_tokens": len(payload.prompt.encode("utf-8")),
                                       "output_tokens": len(text.encode("utf-8"))}}

    return app


primary_app = create_mock(os.getenv("PRIMARY_MOCK_MODE", "rate_limit"))
backup_app = create_mock(os.getenv("BACKUP_MOCK_MODE", "success"))
