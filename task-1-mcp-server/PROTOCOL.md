# Why the server has a transport adapter

We use official MCP SDK version 1.30.0. It handles initialization, tool discovery, request handling, and stdio.

The assessment also asks for exact JSON-RPC error codes. Two SDK defaults need a small adjustment:

1. The normal tool decorator catches exceptions and can return `isError: true`. We register a typed tool handler so invalid fields return `-32602` instead.
2. The normal stdio reader forwards parse exceptions without creating the required parse-error reply. Our input adapter returns `-32700` and lets the client keep using the connection.

The adapter also returns `-32601` for unknown methods before the SDK tries to validate them as known requests.

A parse error must use `id: null`. This SDK version's error model requires a string or integer ID. The adapter writes that error itself, using the same locked writer as the SDK. This keeps messages from being mixed together.

Valid requests still pass through the SDK. Logs go to stderr. Notifications get no replies. Tool calls without request IDs are ignored rather than executed.

Batch requests are rejected. This server does not make requests back to the client.

References:

- [Official Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [MCP stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [JSON-RPC error codes](https://www.jsonrpc.org/specification#error_object)
