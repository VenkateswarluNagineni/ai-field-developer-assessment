"""Local HTTP/JSON-RPC mock. No keys are changed by its admin tool."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from protocol import RpcError, error_response, parse_request


app = FastAPI(title="Mock MCP server")
TOOLS = [
    {"name": "get_status", "description": "Return the mock service status.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "admin_reset_key", "description": "Simulate an admin key reset.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/mcp")
async def handle(request: Request):
    try:
        frame = parse_request(await request.body())
    except RpcError as exc:
        return error_response(exc.code, exc.message, exc.request_id, status=400)
    if "id" not in frame:
        return Response(status_code=204)
    request_id = frame["id"]
    method = frame["method"]
    if method == "initialize":
        result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "mock-mcp", "version": "1.0.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = frame["params"]["name"]
        if name not in {tool["name"] for tool in TOOLS}:
            return error_response(-32602, "Unknown tool", request_id)
        if frame["params"].get("arguments", {}):
            return error_response(-32602, "This mock tool takes no arguments", request_id)
        text = "Service is running" if name == "get_status" else "Key reset simulated"
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        return error_response(-32601, "Method not found", request_id)
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
