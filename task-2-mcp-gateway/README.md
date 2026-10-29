# Task 2: MCP security gateway

Status: pending. This folder contains the requirements, not an implementation.

## What to build

An HTTP proxy between an MCP client and a sample MCP server.

## Logic

1. Read the Bearer token and identify the user's role.
2. Forward `tools/list` to the server.
3. For `tools/call`, read `params.name`.
4. If the tool name starts with `admin_`, allow only admins.
5. For a blocked call, return `-32001` with `Unauthorized Tool Call`. Do not call the server.
6. Forward allowed requests and their responses.

## Tests to add

- Tool listing is forwarded.
- Admins can call admin tools.
- Viewers cannot call admin tools.
- Blocked calls never reach the sample server.
- Missing or invalid tokens fail safely.
- Request IDs and JSON-RPC response structure are preserved.
