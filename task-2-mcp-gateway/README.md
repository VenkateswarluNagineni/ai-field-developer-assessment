# Task 2: MCP security gateway

An HTTP gateway that checks the user's role before forwarding MCP JSON-RPC requests.

```text
Client → Gateway :8000 → Mock server :8001
```

The mock server has two tools: `get_status` and `admin_reset_key`. The admin tool only simulates a key reset.

## The rule

| Request | Viewer | Admin |
| --- | --- | --- |
| `tools/list` | Forward | Forward |
| `get_status` | Forward | Forward |
| Any tool starting with `admin_` | Block | Forward |

A blocked request never reaches the mock server. The reply keeps the request ID:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": {
    "code": -32001,
    "message": "Unauthorized Tool Call"
  }
}
```

Viewers still see admin tools in `tools/list`. The assessment requires that listing to pass through unchanged.

## Start the servers

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

From this folder:

```sh
uv sync --locked
cp .env.example .env
```

`cp` also works in PowerShell. The copied file contains local demo tokens. The gateway will not start without two different tokens.

In terminal 1, start the mock server:

```sh
uv run --locked uvicorn mock_server:app --host 127.0.0.1 --port 8001
```

In terminal 2, from the same folder, start the gateway:

```sh
uv run --locked --env-file .env uvicorn gateway:create_app --factory --host 127.0.0.1 --port 8000
```

Send requests to `http://127.0.0.1:8000/mcp`.

## Try a request

Send this JSON with `Content-Type: application/json`:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "admin_reset_key",
    "arguments": {}
  }
}
```

With `Authorization: Bearer local-demo-viewer-token`, it returns `-32001`.

With `Authorization: Bearer local-demo-admin-token`, it returns `Key reset simulated`.

For a command-line example using the unchanged demo tokens:

```sh
curl http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -H "Authorization: Bearer local-demo-viewer-token" --data '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"admin_reset_key","arguments":{}}}'
```

Use `curl.exe` in Windows PowerShell if `curl` is an alias for another command. If you change the tokens in `.env`, change them in the examples too.

## How it works

1. Read the Bearer token. Match it to a role configured on the server.
2. Check the JSON-RPC fields.
3. If `tools/call` selects an `admin_` tool, require the admin role.
4. Forward the original request body to the fixed downstream URL.
5. Return the downstream JSON-RPC response.

The gateway does not trust a role supplied in the request body. It does not forward the user's Bearer token, cookies, or arbitrary headers. Tokens are compared using `hmac.compare_digest` and are not written to logs.

| File | Job |
| --- | --- |
| `gateway.py` | Reads settings, checks the role, and forwards requests. |
| `protocol.py` | Reads JSON-RPC fields and builds local error replies. |
| `mock_server.py` | Provides sample tools over HTTP. |
| `tests/test_gateway.py` | Checks forwarding, authorization, and failures. |
| `tests/test_http.py` | Starts both servers and sends real HTTP requests. |

## Settings

| Environment variable | Meaning | Default |
| --- | --- | --- |
| `ADMIN_TOKEN` | Token mapped to admin | Required |
| `VIEWER_TOKEN` | Token mapped to viewer | Required |
| `DOWNSTREAM_URL` | Fixed server endpoint | `http://127.0.0.1:8001/mcp` |
| `DOWNSTREAM_TIMEOUT_SECONDS` | Total downstream deadline | `5` |

These are opaque demo tokens, not JWTs. There is no login service in this task. The `.env.example` values are public test values. Use different random tokens if running outside a local demo, and use HTTPS for network access. Keep the downstream server reachable only by the gateway when enforcing this policy in a deployment.

## Errors

| Problem | HTTP status | Error |
| --- | --- | --- |
| Missing, malformed, duplicate, or unknown Bearer token | `401` | HTTP authentication error with `WWW-Authenticate: Bearer` |
| Broken JSON or UTF-8 | `400` | JSON-RPC `-32700` |
| Invalid envelope, duplicate JSON keys, or batch request | `400` | JSON-RPC `-32600` |
| Invalid method parameters | `400` | JSON-RPC `-32602` |
| Viewer calls an `admin_` tool | `200` | JSON-RPC `-32001` |
| Downstream timeout | `504` | JSON-RPC `-32002` |
| Connection failure, redirect, server failure, or invalid response | `502` | JSON-RPC `-32003` |

HTTP `200` on a blocked tool call means the JSON-RPC exchange succeeded. Its `error` field means the tool call was denied.

Authentication runs before body parsing, so authentication failures use an HTTP error rather than an untrusted request ID. Authenticated JSON-RPC requests keep their IDs in gateway errors.

Allowed requests keep their original body bytes, including extra metadata. Valid downstream responses keep their body bytes and HTTP status, including JSON-RPC errors. Redirects are not followed. HTTP 5xx bodies are replaced with a safe error. A mismatched response ID is rejected. Failed requests are not retried, since a tool may already have acted.

Valid `notifications/` messages are forwarded and return HTTP `204` without a JSON-RPC reply. Tool calls require an ID. Other request methods pass through for the downstream server to handle.

## Scope

This is the assessment's HTTP/JSON-RPC proxy and mock. It does not implement Streamable HTTP sessions, SSE, OAuth, prompt detection, refund amount caps, PII masking, or rate limits. Task 1 remains a separate stdio server. The `admin_` prefix rule is case-sensitive and does not restrict `trigger_refund`.

## Tests

```sh
uv run --locked pytest -q
```

All 66 tests passed locally. They check both roles, unknown tokens, malformed requests, duplicate fields, escaped tool names, false admin claims, downstream failures, timeouts, cancellation, response IDs, parallel requests, and real HTTP forwarding. Blocked calls are checked against a recording downstream transport, not just their error messages. See [test results](TEST_RESULTS.md).

GitHub also runs both task suites on Windows and Linux with Python 3.11 and 3.14. Dependencies are fixed by `uv.lock`.
