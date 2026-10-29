"""Exercise real pipes, not only Python handler return values."""

import asyncio
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


class WireClient:
    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "server.py")], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.messages = queue.Queue()
        self.stdout_lines = []
        self.stderr_lines = []
        self.threads = [
            threading.Thread(target=self.read_stdout, daemon=True),
            threading.Thread(target=self.read_stderr, daemon=True),
        ]
        for thread in self.threads:
            thread.start()
        self.next_id = 0

    def read_stdout(self):
        for line in self.process.stdout:
            self.stdout_lines.append(line)
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                self.messages.put({"stdout_leak": line})

    def read_stderr(self):
        self.stderr_lines.extend(self.process.stderr)

    def send(self, frame):
        self.raw(json.dumps(frame))

    def raw(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def receive(self):
        message = self.messages.get(timeout=10)
        assert message.get("jsonrpc") == "2.0", message
        assert ("result" in message) != ("error" in message), message
        return message

    def request(self, method, params=None):
        self.next_id += 1
        frame = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            frame["params"] = params
        self.send(frame)
        result = self.receive()
        assert result["id"] == self.next_id
        return result

    def initialize(self):
        result = self.request("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "assessment-tests", "version": "1.0"},
        })
        assert result["result"]["capabilities"]["tools"] == {"listChanged": False}
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            pytest.fail("Server did not stop after stdin EOF")
        for thread in self.threads:
            thread.join(timeout=2)
        assert self.process.returncode == 0, "".join(self.stderr_lines)
        assert self.stderr_lines, "Startup diagnostics should go to stderr"
        for line in self.stdout_lines:
            message = json.loads(line)
            assert message["jsonrpc"] == "2.0"
            assert "id" in message
            assert ("result" in message) != ("error" in message)


@pytest.fixture
def wire():
    client = WireClient()
    try:
        client.initialize()
        yield client
    finally:
        client.close()


def test_discovery_and_valid_tools(wire):
    tools = {t["name"]: t for t in wire.request("tools/list")["result"]["tools"]}
    assert set(tools) == {"get_customer_record", "trigger_refund"}
    for tool in tools.values():
        assert tool["inputSchema"]["additionalProperties"] is False
    refund_schema = tools["trigger_refund"]["inputSchema"]
    assert set(refund_schema["required"]) == {"customer_id", "amount", "reason"}
    assert refund_schema["properties"]["amount"]["exclusiveMinimum"] == 0
    assert refund_schema["properties"]["reason"]["minLength"] == 10
    customer = wire.call("get_customer_record", {"customer_id": "CUST-12345"})["result"]
    assert customer["structuredContent"]["name"] == "Alex Morgan"
    assert json.loads(customer["content"][0]["text"]) == customer["structuredContent"]
    for amount in (1, 25.5, 0.001):
        refund = wire.call("trigger_refund", {
            "customer_id": "CUST-00001", "amount": amount, "reason": "  1234567890  ",
        })["result"]
        assert not refund["isError"]
        assert refund["structuredContent"]["status"] == "simulated"
        assert refund["structuredContent"]["reason"] == "1234567890"


@pytest.mark.parametrize("customer_id", [
    "CUST-1234", "CUST-123456", "cust-12345", "CUST-１２３４５", "CUST-١٢٣٤٥",
    " CUST-12345", "CUST-12345 ", "CUST-12345\n", "CUST-12345junk", "", 12345, True, None,
])
def test_invalid_customer_ids(wire, customer_id):
    response = wire.call("get_customer_record", {"customer_id": customer_id})
    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["issues"][0]["field"] == "customer_id"


@pytest.mark.parametrize("amount", [0, -0.01, -10, "25.5", True, False, None, [], {}])
def test_invalid_amounts(wire, amount):
    response = wire.call("trigger_refund", {
        "customer_id": "CUST-12345", "amount": amount, "reason": "Damaged item received",
    })
    assert response["error"]["code"] == -32602


@pytest.mark.parametrize("reason", ["broken", "123456789", "", " " * 20, "  broken  ", 1234567890, None])
def test_invalid_reasons(wire, reason):
    response = wire.call("trigger_refund", {
        "customer_id": "CUST-12345", "amount": 10, "reason": reason,
    })
    assert response["error"]["code"] == -32602


@pytest.mark.parametrize("arguments", [
    {}, {"customer_id": "CUST-12345", "extra": "blocked"},
])
def test_missing_and_extra_arguments(wire, arguments):
    assert wire.call("get_customer_record", arguments)["error"]["code"] == -32602


def test_protocol_error_codes_and_recovery(wire):
    for malformed in ('{"jsonrpc":', '{bad}', 'NaN'):
        wire.raw(malformed)
        response = wire.receive()
        assert response["id"] is None
        assert response["error"]["code"] == -32700
    for invalid in ([], {}, 42, {"jsonrpc": "1.0", "method": "ping", "id": 99},
                    {"jsonrpc": "2.0", "method": "ping", "id": True}):
        wire.send(invalid)
        assert wire.receive()["error"]["code"] == -32600
    assert wire.request("does/not/exist")["error"]["code"] == -32601
    assert wire.call("missing_tool", {})["error"]["code"] == -32602
    assert wire.request("tools/call", {"name": 123})["error"]["code"] == -32602
    assert wire.request("tools/call", {"name": "trigger_refund", "arguments": []})["error"]["code"] == -32602
    assert wire.request("ping")["result"] == {}


def test_notifications_and_request_ids(wire):
    wire.send({"jsonrpc": "2.0", "method": "notifications/unknown"})
    wire.send({"jsonrpc": "2.0", "method": "tools/call", "params": {
        "name": "trigger_refund", "arguments": {},
    }})
    wire.send({"jsonrpc": "2.0", "id": "string-id", "method": "ping"})
    assert wire.receive()["id"] == "string-id"
    with pytest.raises(queue.Empty):
        wire.messages.get(timeout=0.1)


def test_concurrent_requests_do_not_interleave_stdout(wire):
    for request_id in range(100, 120):
        wire.send({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {
            "name": "get_customer_record", "arguments": {"customer_id": "CUST-12345"},
        }})
    assert {wire.receive()["id"] for _ in range(20)} == set(range(100, 120))


def test_nonexistent_customer_is_execution_error(wire):
    for name, arguments in [
        ("get_customer_record", {"customer_id": "CUST-99999"}),
        ("trigger_refund", {"customer_id": "CUST-99999", "amount": 10, "reason": "Damaged delivery"}),
    ]:
        result = wire.call(name, arguments)["result"]
        assert result["isError"] is True
        assert result["content"][0]["text"] == "Customer not found"


def test_nonfinite_numbers(wire):
    wire.raw('{"jsonrpc":"2.0","id":91,"method":"tools/call","params":'
             '{"name":"trigger_refund","arguments":{"customer_id":"CUST-12345",'
             '"amount":1e999,"reason":"Damaged delivery"}}}')
    assert wire.receive()["error"]["code"] == -32602


def test_malformed_utf8_is_recoverable(wire):
    wire.process.stdin.buffer.write(b'{"jsonrpc":"2.0","method":"\xff"}\n')
    wire.process.stdin.buffer.flush()
    response = wire.receive()
    assert response["id"] is None
    assert response["error"]["code"] == -32700
    assert wire.request("ping")["result"] == {}


@pytest.mark.parametrize("missing", ["customer_id", "amount", "reason"])
def test_required_refund_fields(wire, missing):
    arguments = {"customer_id": "CUST-12345", "amount": 10, "reason": "Damaged delivery"}
    del arguments[missing]
    assert wire.call("trigger_refund", arguments)["error"]["code"] == -32602


def test_validation_prevents_business_execution(monkeypatch):
    def forbidden(*args):
        pytest.fail("Business operation executed with invalid arguments")
    monkeypatch.setattr(server, "execute", forbidden)
    request = types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(
        name="trigger_refund", arguments={"customer_id": "bad", "amount": -1, "reason": "short"},
    ))
    with pytest.raises(McpError) as exc:
        asyncio.run(server.call_tool(request))
    assert exc.value.error.code == -32602


def test_internal_errors_are_sanitized(monkeypatch):
    def broken(*args):
        raise RuntimeError("private-backend-credential")
    monkeypatch.setattr(server, "execute", broken)
    request = types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(
        name="get_customer_record", arguments={"customer_id": "CUST-12345"},
    ))
    with pytest.raises(McpError) as exc:
        asyncio.run(server.call_tool(request))
    assert exc.value.error.code == -32603
    assert "private-backend" not in str(exc.value.error)


@pytest.mark.asyncio
async def test_official_sdk_client_interoperability():
    parameters = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            assert len((await session.list_tools()).tools) == 2
            result = await session.call_tool("get_customer_record", {"customer_id": "CUST-12345"})
            assert result.structuredContent["customer_id"] == "CUST-12345"
            with pytest.raises(McpError) as exc:
                await session.call_tool("get_customer_record", {"customer_id": "invalid"})
            assert exc.value.error.code == -32602
