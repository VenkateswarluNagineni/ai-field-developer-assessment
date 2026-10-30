import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sse import MAX_EVENT_BYTES, SSEParser, StreamFormatError, encode_event, read_events


async def chunks(parts):
    for part in parts:
        yield part


@pytest.mark.asyncio
async def test_every_byte_split_including_multibyte_utf8():
    body = b"\xef\xbb\xbf" + 'event: delta\ndata: {"text":"café 🙂"}\n\n'.encode()
    expected = [("delta", '{"text":"café 🙂"}')]
    for split in range(len(body) + 1):
        assert [event async for event in read_events(chunks([body[:split], body[split:]]))] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
async def test_comments_multiline_data_and_split_line_endings(newline):
    text = newline.join([": heartbeat", "", "event: delta", "id: ignored", "retry: 10",
                         'data: {"text":', 'data: "hello"}', "", ""])
    body = text.encode()
    actual = [event async for event in read_events(chunks([bytes([byte]) for byte in body]))]
    assert actual == [("delta", '{"text":\n"hello"}')]


@pytest.mark.asyncio
async def test_multiple_events_in_a_single_network_chunk():
    body = encode_event("delta", {"text": "hello"}) + encode_event("done", {"status": "complete"})
    assert len([event async for event in read_events(chunks([body]))]) == 2


@pytest.mark.asyncio
async def test_incomplete_event_is_not_dispatched():
    assert [event async for event in read_events(chunks([b'data: {"text":"private"}\n']))] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"\xff", b"\xf0\x9f"])
async def test_invalid_or_truncated_utf8_is_rejected(body):
    with pytest.raises(StreamFormatError):
        _ = [event async for event in read_events(chunks([body]))]


def test_event_size_is_bounded_even_without_newlines():
    parser = SSEParser()
    with pytest.raises(StreamFormatError):
        list(parser.feed("x" * (MAX_EVENT_BYTES + 1)))
    assert len(parser.line) == MAX_EVENT_BYTES


def test_multiline_event_size_cannot_bypass_limit():
    parser = SSEParser()
    with pytest.raises(StreamFormatError):
        list(parser.feed("data: x\n" * MAX_EVENT_BYTES))


def test_sse_encoding_keeps_text_newlines_inside_json():
    body = encode_event("delta", {"text": "hello\nworld"})
    assert body.count(b"\n\n") == 1
    assert b"hello\\nworld" in body
