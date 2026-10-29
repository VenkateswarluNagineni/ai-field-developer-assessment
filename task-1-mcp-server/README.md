# Task 1: MCP server

This server has two tools:

- `get_customer_record`: returns a sample customer.
- `trigger_refund`: returns a simulated refund receipt.

It uses the official Python MCP SDK and Pydantic. No external services or API keys are needed.

## Run

From this folder:

```sh
uv sync --locked
uv run --locked server.py
```

The server waits for an MCP client on stdin. Logs go to stderr. Only JSON-RPC messages go to stdout. Closing stdin stops the server.

For a client that uses `mcpServers` settings, start with [client-config.example.json](client-config.example.json). Replace `/absolute/path/to/ai-field-developer-assessment/task-1-mcp-server` with the folder on your computer. Use forward slashes on Windows, for example `C:/projects/ai-field-developer-assessment/task-1-mcp-server`.

## Rules

| Input | Rule |
| --- | --- |
| `customer_id` | Exactly `CUST-` and five ASCII digits, such as `CUST-12345`. Spaces and lowercase letters are rejected. |
| `amount` | A finite number greater than zero. Strings and booleans are rejected. |
| `reason` | At least 10 characters after trimming surrounding spaces. |
| Extra fields | Rejected. |

Sample customers: `CUST-12345` and `CUST-00001`.

## How the code works

1. The MCP SDK reads the request.
2. Pydantic checks the tool inputs.
3. Invalid inputs return an error. Valid inputs reach the sample customer or refund code.
4. The SDK sends the result back to the client.

| File | Job |
| --- | --- |
| `schemas.py` | Defines the input rules and the schemas shown to the client. |
| `service.py` | Looks up sample customers and creates simulated refund receipts. |
| `server.py` | Connects the tools to the MCP SDK. |
| `transport.py` | Handles malformed input and keeps stdout messages separate. |
| `tests/test_server.py` | Tests the tools and the real stdin/stdout connection. |

The small transport adapter is needed because the SDK does not produce all the exact error replies required by this assessment. See [protocol notes](PROTOCOL.md).

## Errors

| Problem | Reply |
| --- | --- |
| Broken JSON or UTF-8 | `-32700`: Parse error |
| Invalid JSON-RPC request | `-32600`: Invalid Request |
| Unknown protocol method | `-32601`: Method not found |
| Invalid fields or unknown tool name | `-32602`: Invalid params |
| Unexpected operation failure | `-32603`: Internal error |
| Valid customer ID absent from sample data | Tool result with `isError: true` |

`tools/call` is a method. `trigger_refund` is a tool name inside that method. This is why an unknown method and an unknown tool use different errors.

## Example tool call

After the client initializes the connection:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "trigger_refund",
    "arguments": {
      "customer_id": "CUST-12345",
      "amount": 25.5,
      "reason": "Item arrived damaged"
    }
  }
}
```

The receipt includes the customer ID, amount, reason, a new refund ID, and `"status": "simulated"`. No money is transferred. Refunds are not stored after the response.

## Tests

```sh
uv run --locked pytest -q
```

The 44 tests cover valid calls, invalid fields, error codes, malformed input, recovery after errors, concurrent replies, stdout isolation, clean shutdown, and the official MCP client. See [verification details](TEST_RESULTS.md).
