# Task 3 test results

Date: October 30, 2026.

Local environment: Windows, Python 3.14.0, FastAPI 0.162.0, HTTPX 0.28.1, Uvicorn 0.54.0.

Command:

```sh
uv run --locked pytest -q
```

Result:

```text
137 passed in 12.31s
```

The tests cover:

- Every two-chunk split of sample emails, formatted SSNs, and card-like numbers.
- One-character deltas, random chunking, punctuation boundaries, and multiple sensitive values.
- Unquoted ASCII email characters, including apostrophes.
- Invisible separators, joiners, and directional controls inside emails, SSNs, and card-like numbers, at every two-chunk split and one character at a time.
- Non-sensitive formatting and joined emoji remain unchanged. Invisible controls still count toward the buffer limit.
- Existing conservative masking of long counts, IDs, and `A@B` remains unchanged.
- No early release of sensitive prefixes and safe flushing only on normal completion.
- Long output without accumulating the answer; unfinished candidates remain bounded.
- SSE events split at every byte, including inside multibyte UTF-8 characters.
- BOMs, comments, multiline data, different line endings, oversized events, and malformed text.
- Safe HTTP errors before streaming and safe SSE errors after streaming starts.
- No unchecked-tail flush after timeout, malformed events, provider errors, or incomplete streams.
- Provider connection cleanup on normal completion, failures, client disconnects, and send failures.
- Slow-client backpressure and isolation across 25 parallel generations.
- Two real HTTP servers, with redacted text delivered before generation ends.

GitHub Actions runs Task 3 separately on Windows and Linux with Python 3.11 and 3.14. These tests do not establish production throughput or exact process-memory usage. The provider is a local mock, not a paid LLM integration.
