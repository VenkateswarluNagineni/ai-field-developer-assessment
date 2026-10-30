# Task 3 test results

Date: October 29, 2026.

Local environment: Windows, Python 3.14.0, FastAPI 0.162.0, HTTPX 0.28.1, Uvicorn 0.54.0.

Command:

```sh
uv run --locked pytest -q
```

Result:

```text
104 passed in 6.07s
```

The tests cover:

- Every two-chunk split of sample emails, formatted SSNs, and card-like numbers.
- One-character deltas, random chunking, punctuation boundaries, and multiple sensitive values.
- Unquoted ASCII email characters, including apostrophes.
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
