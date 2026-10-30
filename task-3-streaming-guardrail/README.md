# Task 3: Streaming guardrail

A gateway that replaces sensitive generated text with `[REDACTED]` while the answer streams.

```text
Client → Gateway :8002 → Mock provider :8003
Client ← Checked SSE text ← Gateway ← Provider SSE events
```

The local provider sends fixed sample text with sensitive values split across several events. No API keys or paid model calls are needed.

## Run

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). From this folder:

```sh
uv sync --locked
cp .env.example .env
```

`cp` also works in PowerShell.

Terminal 1: start the provider.

```sh
uv run --locked uvicorn mock_provider:app --host 127.0.0.1 --port 8003
```

Terminal 2: start the gateway from the same folder.

```sh
uv run --locked --env-file .env uvicorn gateway:create_app --factory --host 127.0.0.1 --port 8002
```

Terminal 3: send a prompt. `--no-buffer` lets curl display events as they arrive.

```sh
curl --no-buffer http://127.0.0.1:8002/generate -H "Content-Type: application/json" --data '{"prompt":"Show the demo"}'
```

Use `curl.exe` in Windows PowerShell if `curl` is an alias. The mock accepts the prompt but generates the same demo answer each time.

The text in the returned delta events joins to:

```text
Hello! Email [REDACTED]; SSN [REDACTED]; card [REDACTED]. Done!
```

## How it works

1. Send the prompt to the fixed provider endpoint.
2. Assemble complete SSE events, even when network chunks split them.
3. Hold unfinished words and numeric runs in a buffer.
4. Redact complete sensitive candidates and release safe text.
5. On the provider's successful `done` event, check the remaining text and finish.

**Rule: check before sending. Never send an uncertain prefix and try to remove it later.** Each request has its own parser and redactor. The gateway waits for the client to accept output before reading more provider data. It does not create an unlimited queue.

| File | Job |
| --- | --- |
| `gateway.py` | Opens the provider stream, sends checked output, and handles errors and disconnects. |
| `redactor.py` | Holds unfinished text and masks supported sensitive values. |
| `sse.py` | Reads bounded UTF-8 SSE events and writes safe JSON event payloads. |
| `mock_provider.py` | Streams fixed demo text in small pieces. |
| `tests/` | Checks redaction, stream framing, timing, limits, failures, and real HTTP delivery. |

## What gets masked

| Pattern | Behavior |
| --- | --- |
| Common unquoted ASCII email-like text | Mask the whole token containing `@`, including incomplete `alex@`. Supports apostrophes, plus signs, and other unquoted ASCII email characters. |
| Formatted SSN | Mask `123-45-6789`. |
| Card-like number | Mask numeric runs with 13 or more digits, including spaces and hyphens. This covers 13–19 digit card formats and conservatively masks longer runs too. |

**Rule: invisible formatting characters must not split a sensitive value into apparently safe pieces.** The checker ignores Unicode format controls (category `Cf`) for matching and keeps the unfinished original text buffered. A detected value is masked together with controls inside it. Non-sensitive formatting, including joined emoji, stays unchanged. These controls still count toward the 512-character buffer limit. This handles format controls, not every possible Unicode disguise.

This is a privacy filter, not a card validator. It does not require a Luhn checksum. It may mask long order numbers, social handles, nearby email punctuation, or several adjacent numeric values as one candidate. That is intentional. It does not cover every PII type, quoted or international email addresses, address-literal domains, Unicode digits, or disguised text such as `alex at example dot com`.

## Limits and latency

| Limit | Value |
| --- | --- |
| Prompt | 1–4096 characters after trimming |
| Text in one provider delta | 4096 characters |
| Unfinished redaction candidate | 512 characters |
| SSE event parsing budget | 64 KiB of parsed UTF-8 text |

The redactor processes small slices, and the SSE decoder processes at most 1024 bytes at a time. Only bounded events and unfinished candidates are held; the full answer is never collected. These are data limits, not exact Python process-memory measurements.

The first complete safe words can leave immediately. Unfinished words and numbers wait until their boundary is clear. A long word or numeric run can hit the buffer limit even if it is harmless; the gateway then stops safely rather than sending unchecked text. Regex checks run locally on bounded text. There is no semantic model or background security queue.

## Provider contract

The configured provider must accept `POST` JSON containing `prompt` and return HTTP 200 with `Content-Type: text/event-stream`. It must send uncompressed UTF-8 SSE events:

```text
event: delta
data: {"text": "Hello! "}

event: done
data: {"status": "complete"}

```

Each event ends with a blank line. Comments/heartbeats, multiline `data`, UTF-8 BOMs, and LF/CR/CRLF line endings are supported. The gateway requires an explicit successful `done` event. EOF alone is not success.

The mock implements this contract. Vendor-specific APIs need an adapter to their event format and authentication. They are not drop-in replacements for this demo endpoint. The gateway sends only the prompt; client credentials and cookies are not passed to the provider. Compressed provider streams are rejected so decompression cannot bypass the parsing budget.

| Environment variable | Meaning | Default |
| --- | --- | --- |
| `PROVIDER_URL` | Fixed generation endpoint | `http://127.0.0.1:8003/generate` |
| `PROVIDER_TIMEOUT_SECONDS` | Idle network timeout | `5` |

The timeout applies to network waits, not the whole generation. The code uses async HTTPX streaming with one shared connection pool. It closes upstream connections when generation finishes, fails, or the client disconnects, including a disconnect before the first body chunk. It does not retry or switch providers.

## Errors

Before streaming starts, the gateway returns safe JSON errors with HTTP 400 for invalid input, 502 for an unavailable/unsuitable provider, 504 for a provider timeout, or 500 for an unexpected gateway error.

After streaming starts, HTTP headers have already been sent. A failure sends one safe SSE `error` event and closes the response. It does not send `done` or flush unfinished text. For example:

```text
event: error
data: {"code": "provider_stream_error", "message": "Provider stream failed"}

```

Other stream error codes are `buffer_limit`, `provider_timeout`, and `internal_error`. Provider error bodies, prompts, raw deltas, and stack traces are not included in these errors. If the client has already disconnected, there is no final event to deliver; upstream work is cancelled and its connection is closed.

## Tests and scope

```sh
uv run --locked pytest -q
```

All 137 local tests passed. Tests split examples at every two-chunk position, try one-character and random chunking, check invisible formatting controls and UTF-8/SSE framing, bound buffers, run parallel streams, and verify cleanup and backpressure. A real HTTP test checks that safe text reaches the client before the provider finishes. See [test results](TEST_RESULTS.md).

Task 3 covers generated-text redaction only. Prompt classification, authentication, rate limiting, model fallback, tool authorization, and MCP tool-output masking are outside this task. No production-scale concurrency or latency claim is made.
