"""Small input adapter for error semantics not supplied by SDK stdio.

The SDK still owns stdio framing of valid messages and all MCP lifecycle handling.
Only rejected frames are serialized here. A shared lock prevents interleaved output.
"""

import json
import logging

import anyio
from mcp import types
from pydantic import ValidationError


logger = logging.getLogger(__name__)
SUPPORTED_METHODS = {"initialize", "ping", "tools/list", "tools/call"}


class ProtocolOutput:
    def __init__(self, stream):
        self.stream = stream
        self.lock = anyio.Lock()

    async def write(self, text: str):
        async with self.lock:
            await self.stream.write(text)
            await self.stream.flush()

    async def flush(self):
        # write() already flushes while holding the shared lock.
        pass

    async def error(self, request_id, code: int, message: str):
        logger.warning("Rejected incoming frame (code=%s)", code)
        await self.write(json.dumps({
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message},
        }, allow_nan=False) + "\n")


def reject_constant(value: str):
    raise ValueError("Non-standard JSON numeric constant")


class ValidatingInput:
    def __init__(self, stream, output: ProtocolOutput):
        self.stream = stream
        self.output = output

    async def __aiter__(self):
        async for raw_line in self.stream:
            try:
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            except UnicodeDecodeError:
                await self.output.error(None, types.PARSE_ERROR, "Parse error")
                continue
            try:
                frame = json.loads(line, parse_constant=reject_constant)
            except (ValueError, RecursionError):
                await self.output.error(None, types.PARSE_ERROR, "Parse error")
                continue

            request_id = frame.get("id") if isinstance(frame, dict) else None
            valid_id = type(request_id) in (int, str)
            if (
                not isinstance(frame, dict)
                or frame.get("jsonrpc") != "2.0"
                or not isinstance(frame.get("method"), str)
                or ("id" in frame and not valid_id)
                or "result" in frame or "error" in frame
            ):
                await self.output.error(request_id if valid_id else None,
                                        types.INVALID_REQUEST, "Invalid Request")
                continue

            if "id" not in frame:
                # Notifications never get a response and must not execute tools.
                if frame["method"].startswith("notifications/"):
                    try:
                        types.ClientNotification.model_validate(frame)
                    except ValidationError:
                        logger.warning("Ignored invalid notification")
                    else:
                        yield line
                continue

            if frame["method"] not in SUPPORTED_METHODS:
                await self.output.error(request_id, types.METHOD_NOT_FOUND, "Method not found")
                continue
            try:
                types.ClientRequest.model_validate(frame)
            except ValidationError:
                await self.output.error(request_id, types.INVALID_PARAMS, "Invalid params")
                continue
            yield line
