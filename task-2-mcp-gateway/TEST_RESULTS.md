# Task 2 test results

Date: October 29, 2026.

Local environment: Windows, Python 3.14.0, FastAPI 0.162.0, HTTPX 0.28.1, Uvicorn 0.54.0.

Command:

```sh
uv run --locked pytest -q
```

Final local result:

```text
76 passed in 4.38s
```

The tests check:

- Both roles can list tools and call ordinary tools.
- Only admins can call tools with the `admin_` prefix.
- Blocked calls never reach the recording downstream transport.
- Missing, unknown, malformed, and duplicate credentials are rejected.
- Claims of an admin role in the JSON body do not change access.
- Escaped admin names are still blocked; duplicate JSON keys are rejected.
- Allowed request bytes and metadata survive forwarding. Successful replies and ordinary client errors preserve their bytes, status, and IDs.
- Downstream internal errors lose private messages, stack traces, and extra metadata while preserving their ID, code, and HTTP status.
- Unexpected gateway failures return a safe JSON-RPC `-32603` reply with HTTP 500 and the validated request ID.
- Cancellation and client disconnects are not turned into internal errors. Failed notifications have no JSON-RPC body.
- Client tokens and cookies are not passed downstream. Tokens stay out of gateway logs.
- Connection errors, redirects, bad replies, and server failures produce safe errors.
- The timeout cancels downstream work and does not retry a tool call.
- Twenty parallel calls preserve IDs and enforce the same role rule.
- Both servers start and communicate over real HTTP.

GitHub Actions also runs both Task 1 and Task 2 on Windows and Linux with Python 3.11 and 3.14. Check the pull request's test results for that run.

This uses demo tokens and a mock server. No real keys are reset.
