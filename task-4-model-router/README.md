# Task 4: Rate limiter and model router

A non-streaming completion gateway with a per-key token budget, one primary provider, one backup, and an on-disk SQLite database.

```text
Request → Reserve tokens in SQLite → Primary
                                     ├─ success → Record usage → Return answer
                                     └─ 429 or 3000 ms timeout → Reserve for backup → Backup
```

**Rule: a provider attempt cannot start without a token reservation.** A fallback attempt needs its own reservation too.

## Run the demo

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). From this folder:

```sh
uv sync --locked
cp .env.example .env
```

`cp` also works in PowerShell. The example keys are public local-demo values.

Terminal 1: start the primary provider.

```sh
uv run --locked --env-file .env uvicorn mock_provider:primary_app --host 127.0.0.1 --port 8005
```

Terminal 2: start the backup.

```sh
uv run --locked --env-file .env uvicorn mock_provider:backup_app --host 127.0.0.1 --port 8006
```

Terminal 3: start the gateway.

```sh
uv run --locked --env-file .env uvicorn gateway:create_app --factory --host 127.0.0.1 --port 8004
```

Terminal 4: make a request.

```sh
curl http://127.0.0.1:8004/completions -H "Content-Type: application/json" -H "Authorization: Bearer local-demo-tenant-a" --data '{"prompt":"Hello","max_output_tokens":32}'
```

Use `curl.exe` in Windows PowerShell if `curl` is an alias. The primary defaults to HTTP 429, so the answer should come from `backup`. The response has `text`, `provider`, and `usage` fields.

To test another path, change `PRIMARY_MOCK_MODE` in `.env` and restart the primary. Modes are `success`, `rate_limit`, `timeout`, and `server_error`. The backup supports the same modes through `BACKUP_MOCK_MODE`.

## Token accounting

The default is **50,000 tokens per tenant API key in the preceding 60 seconds**. This is a sliding window, not a reset at the start of each clock minute.

Before an attempt, reserve:

```text
input budget + maximum output tokens
```

For the demo contract, the input budget is the prompt's UTF-8 byte length. The mock uses one input token per byte and one output token per generated ASCII byte. This is an explicit demo tokenizer, not an exact tokenizer for every real model. A real provider adapter must supply the correct tokenizer/upper bound, account for provider-added input, and honor the output limit.

| Outcome | Charge |
| --- | --- |
| Success with valid usage | Replace the reservation with reported input plus output tokens. |
| HTTP 429 | Release the reservation. This contract treats 429 as rejection before generation. |
| Timeout, network failure, HTTP error, invalid usage, or cancellation | Keep the full reservation because actual usage is unknown. |
| Reported usage exceeds the reserved contract | Record reported usage and reject the response. Further calls may be blocked. |

The backup is checked against the same tenant budget. For a reservation of 14 tokens:

- Primary 429, backup uses 6: charge 6.
- Primary times out, backup uses 6: keep 14 for the primary and charge 6 for the backup.
- Primary times out and there is no room for another reservation: return the tenant's rate-limit error without calling the backup.

The returned `usage` describes the provider that returned the answer. It does not claim that an earlier timed-out attempt cost nothing. Conservative reservations can temporarily reject requests that might ultimately have fit.

## SQLite and concurrency

Each row contains a reservation ID, API-key hash, token amount, and admission timestamp. Raw keys, prompts, and answers are not stored.

`BEGIN IMMEDIATE` makes checking the current sum and inserting a reservation one atomic transaction. Two threads or local worker processes cannot spend the same remaining budget. Connections are created inside worker threads, and SQLite work runs through `asyncio.to_thread`, so lock waits do not block the event loop. The database has a two-second lock wait and uses WAL mode.

Charges are dated when each provider attempt is admitted. Settlement changes the amount, not its timestamp. Entries at least 60 seconds old are deleted on the next admission check; idle files may still contain expired entries until then. A crashed process leaves its conservative reservations on disk until they expire. Restarting the gateway does not clear recent usage.

This is one local SQLite file shared by local workers, not a multi-region quota system. Use the same file and limit settings for those workers. If SQLite is busy or unavailable, the gateway rejects the request instead of skipping accounting.

## Routing and errors

Each provider attempt has a **3000 ms deadline**, covering network waits and response receipt. Only primary HTTP 429 or timeout triggers one backup attempt. Other primary errors do not trigger fallback. Backup failure ends the request; there is no retry loop.

The timed-out local HTTP attempt is cancelled before fallback. This cannot guarantee that a remote provider stops computing or billing, which is why the reservation remains charged. Two timed-out attempts can take about six seconds, plus database work.

All endpoint failures use a safe shape:

```json
{
  "error": {
    "code": "providers_unavailable",
    "message": "Unable to complete the request"
  }
}
```

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | Invalid prompt or output limit. |
| 401 | `unauthorized` | Missing or invalid API key. |
| 429 | `token_limit_exceeded` | Not enough tenant budget. Includes `Retry-After` when expiry can make room. |
| 502 | `providers_unavailable` / `invalid_provider_response` | Provider failure or invalid response/usage. |
| 503 | `accounting_unavailable` | SQLite cannot safely perform accounting. |
| 504 | `providers_unavailable` | The backup also timed out. |
| 500 | `internal_error` | Unexpected gateway failure. |

Provider error bodies, stack traces, internal URLs, and database paths are not returned. Redirects are not followed. Client credentials and cookies are not forwarded to providers.

## Provider contract and settings

Each endpoint accepts `POST` with `prompt` and `max_output_tokens` and returns:

```json
{"text":"Mock completion.","usage":{"input_tokens":5,"output_tokens":16}}
```

Usage values must be nonnegative integers. A successful response must respect the reserved input budget and output limit. The primary and backup receive the same payload. Real vendor APIs need an adapter for this request/response format and their tokenizer.

| Setting | Default |
| --- | --- |
| `TENANT_API_KEYS` | Required comma-separated keys |
| `SQLITE_PATH` | `data/router.sqlite3` |
| `TOKENS_PER_MINUTE` | `50000` |
| `PRIMARY_URL` | `http://127.0.0.1:8005/completions` |
| `BACKUP_URL` | `http://127.0.0.1:8006/completions` |
| `PRIMARY_MOCK_MODE` | `rate_limit` |
| `BACKUP_MOCK_MODE` | `success` |

The API key determines the tenant; a caller cannot select a different tenant in the body. Keys are compared with `hmac.compare_digest`, then SHA-256 hashed for accounting. Changing a key creates a different budget identity. These are demo credentials, not a login/token-rotation system. Use different random keys and HTTPS outside a local demo. Keep `.env` and database files out of Git.

## Files, tests, and scope

| File | Job |
| --- | --- |
| `limiter.py` | Reserve and settle tokens in SQLite. |
| `router.py` | Enforce attempt budgets, call providers, and apply fallback rules. |
| `gateway.py` | Read settings, authenticate keys, validate requests, and return safe errors. |
| `mock_provider.py` | Run local primary and backup examples. |

Run tests:

```sh
uv run --locked pytest -q
```

All 67 tests passed locally. They cover sliding-window boundaries, actual usage, concurrent threads and processes, the real 3000 ms deadline, fallback accounting, cancellation, sanitized errors, and real HTTP with a gateway restart. See [test results](TEST_RESULTS.md).

This task covers non-streaming completion routing with local mock providers. Streaming fallback, health monitoring, circuit breakers, extra fallback triggers, and vendor adapters are outside scope. No production throughput claim is made.
