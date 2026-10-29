"""Customer tools served over official MCP SDK stdio transport."""

import json
import logging
import sys
from io import TextIOWrapper

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from pydantic import ValidationError

from schemas import INPUT_MODELS
from service import execute
from transport import ProtocolOutput, ValidatingInput


logger = logging.getLogger(__name__)
server = Server("customer-mcp", version="1.0.0")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    descriptions = {
        "get_customer_record": "Read a mock customer record by exact customer ID.",
        "trigger_refund": "Simulate a refund. No money is transferred.",
    }
    return [
        types.Tool(name=name, description=descriptions[name], inputSchema=model.model_json_schema())
        for name, model in INPUT_MODELS.items()
    ]


async def call_tool(request: types.CallToolRequest) -> types.ServerResult:
    name = request.params.name
    model = INPUT_MODELS.get(name)
    if model is None:
        raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message="Unknown tool"))
    try:
        arguments = model.model_validate(request.params.arguments or {})
    except ValidationError as exc:
        # Never include raw argument values or Pydantic diagnostic URLs.
        issues = [
            {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
            for e in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
        logger.warning("Rejected tool arguments")
        raise McpError(types.ErrorData(
            code=types.INVALID_PARAMS, message="Invalid params", data={"issues": issues},
        )) from None

    try:
        result = execute(name, arguments)
    except LookupError:
        return types.ServerResult(types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text="Customer not found")],
        ))
    except Exception:
        logger.exception("Tool execution failed")
        raise McpError(types.ErrorData(code=types.INTERNAL_ERROR, message="Internal error")) from None
    return types.ServerResult(types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, allow_nan=False))],
        structuredContent=result, isError=False,
    ))


# The convenience decorator converts exceptions into isError tool results.
# Register the typed handler directly so McpError reaches SDK protocol handling.
server.request_handlers[types.CallToolRequest] = call_tool


async def main():
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True,
    )
    logger.info("Starting customer MCP server (stdio)")
    # Decode per frame in the adapter so malformed UTF-8 cannot end the session.
    stdin = anyio.wrap_file(sys.stdin.buffer)
    output = ProtocolOutput(anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8")))
    async with stdio_server(stdin=ValidatingInput(stdin, output), stdout=output) as (read, write):
        await server.run(read, write, server.create_initialization_options())
    logger.info("Customer MCP server stopped")


if __name__ == "__main__":
    anyio.run(main)
