"""Read the JSON-RPC fields needed by the gateway and mock server."""

import json

from fastapi.responses import JSONResponse


class RpcError(Exception):
    def __init__(self, code: int, message: str, request_id=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


class DuplicateKeyError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("Duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Non-standard JSON number")


def load_json(body: bytes):
    # Duplicate keys can make the gateway and downstream disagree about a tool.
    return json.loads(body.decode("utf-8"), object_pairs_hook=unique_object,
                      parse_constant=reject_constant)


def parse_request(body: bytes) -> dict:
    try:
        frame = load_json(body)
    except DuplicateKeyError:
        raise RpcError(-32600, "Duplicate JSON key") from None
    except (ValueError, RecursionError):
        raise RpcError(-32700, "Parse error") from None

    if not isinstance(frame, dict):
        raise RpcError(-32600, "Invalid Request")
    request_id = frame.get("id")
    valid_id = type(request_id) in (int, str)
    if (frame.get("jsonrpc") != "2.0" or not isinstance(frame.get("method"), str)
            or not frame["method"] or "result" in frame or "error" in frame
            or ("id" in frame and not valid_id)):
        raise RpcError(-32600, "Invalid Request", request_id if valid_id else None)
    if "id" not in frame and not frame["method"].startswith("notifications/"):
        raise RpcError(-32600, "Request ID required")
    params = frame.get("params", {})
    if not isinstance(params, dict):
        raise RpcError(-32602, "Invalid params", request_id)
    if frame["method"] == "tools/call":
        if (not isinstance(params.get("name"), str) or not params["name"]
                or not isinstance(params.get("arguments", {}), dict)):
            raise RpcError(-32602, "Invalid tool call params", request_id)
    return frame


def error_response(code: int, message: str, request_id=None, status: int = 200):
    return JSONResponse(status_code=status, content={
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": code, "message": message},
    })
