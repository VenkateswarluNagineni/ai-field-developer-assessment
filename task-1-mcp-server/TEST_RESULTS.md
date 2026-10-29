# Local test results

Date: October 29, 2026.

Environment: Windows, Python 3.14.0, MCP SDK 1.30.0, Pydantic 2.14.1.

Command:

```sh
uv run --locked pytest -q
```

The repository copy was tested before publishing:

```text
44 passed in 77.86s (0:01:17)
```

GitHub Actions runs the suite on Windows and Linux with Python 3.11 and 3.14; use the Actions tab to see those results.

The tests check both tool behavior and real process pipes. Every captured stdout line must be a JSON-RPC reply. Startup logs must appear on stderr. Invalid inputs must never reach the refund operation.

Refunds use sample data. No payment service is called.
