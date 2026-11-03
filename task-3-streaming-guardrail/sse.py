"""Bounded SSE parsing. HTTP chunks are not the same thing as SSE events."""

import codecs
import json
from collections.abc import AsyncIterator


MAX_EVENT_BYTES = 65_536
MAX_DELTA_CHARS = 4096


class StreamFormatError(ValueError):
    pass


def encode_event(name: str, data: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=True, allow_nan=False)}\n\n".encode()


class SSEParser:
    def __init__(self):
        self.line = []
        self.data = []
        self.name = "message"
        self.skip_lf = False
        self.size = 0

    def feed(self, text: str):
        for char in text:
            if self.skip_lf:
                self.skip_lf = False
                if char == "\n":
                    continue
            self.size += len(char.encode("utf-8"))
            if self.size > MAX_EVENT_BYTES:
                raise StreamFormatError("Provider event too large")
            if char not in "\r\n":
                self.line.append(char)
                continue
            self.skip_lf = char == "\r"
            line = "".join(self.line)
            self.line.clear()
            if not line:
                event = (self.name, "\n".join(self.data)) if self.data else None
                self.data.clear()
                self.name = "message"
                self.size = 0
                if event is not None:
                    yield event
            elif not line.startswith(":"):
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    self.data.append(value)
                elif field == "event":
                    self.name = value or "message"


async def read_events(chunks: AsyncIterator[bytes]):
    parser = SSEParser()
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="strict")
    try:
        async for chunk in chunks:
            # Do not decode an arbitrarily large transport chunk in one go.
            for offset in range(0, len(chunk), 1024):
                for event in parser.feed(decoder.decode(chunk[offset:offset + 1024])):
                    yield event
        for event in parser.feed(decoder.decode(b"", final=True)):
            yield event
    except UnicodeDecodeError:
        raise StreamFormatError("Invalid provider encoding") from None


def event_data(data: str) -> dict:
    try:
        payload = json.loads(data)
    except (ValueError, RecursionError):
        raise StreamFormatError("Invalid provider JSON") from None
    if not isinstance(payload, dict):
        raise StreamFormatError("Invalid provider event")
    return payload
