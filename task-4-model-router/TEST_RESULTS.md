# Task 4 test results

Date: October 30, 2026.

Local environment: Windows, Python 3.14.0, FastAPI 0.162.0, HTTPX 0.28.1, Uvicorn 0.54.0.

Command:

```sh
uv run --locked pytest -q
```

Result:

```text
67 passed in 25.68s
```

The tests cover:

- Exact tenant limits and the 60-second sliding-window boundary.
- Expired row deletion, accurate retry hints, actual usage settlement, and zero-cost 429 rejection.
- Forty concurrent calls sharing one budget through separate connections.
- Two separate processes writing to one SQLite file without overbooking.
- Database work not blocking the event loop.
- Recent usage surviving a new limiter instance and a real gateway process restart.
- A real 3000 ms primary deadline, local cancellation before fallback, and retained uncertain charges.
- A separate backup reservation, budget exhaustion before fallback, and stopping after one backup.
- No fallback on HTTP errors other than 429, or ordinary non-timeout connection errors.
- Safe handling of invalid provider usage, usage overruns, database failures, and unexpected exceptions.
- API-key isolation, hashed key storage, and no forwarding of client credentials.
- Three real HTTP servers demonstrating primary 429, backup success, and persistent quota rejection after restart.

GitHub Actions runs Task 4 on Windows and Linux with Python 3.11 and 3.14. The providers use a documented demo tokenizer; these tests do not establish real-vendor compatibility or production throughput.
