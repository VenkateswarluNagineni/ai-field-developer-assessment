"""A local SSE provider with sensitive values deliberately split across deltas."""

import asyncio

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from gateway import GenerateInput
from sse import encode_event


app = FastAPI(title="Mock text provider")
DELTAS = ["Hello! ", "Email alex@", "example.com", "; SSN 123-", "45-6789",
          "; card 4111 ", "1111 1111 ", "1111", ". Done!"]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/generate")
async def generate(payload: GenerateInput):
    async def stream():
        # The prompt is accepted but not echoed. This mock generates fixed demo text.
        for text in DELTAS:
            yield encode_event("delta", {"text": text})
            await asyncio.sleep(0.03)
        yield encode_event("done", {"status": "complete"})
    return StreamingResponse(stream(), media_type="text/event-stream")
