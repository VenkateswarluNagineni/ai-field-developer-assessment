"""Run two real HTTP servers to check that the documented setup works."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(url, process):
    deadline = time.monotonic() + 20
    with httpx.Client(trust_env=False, timeout=0.5) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("HTTP server exited during startup")
            try:
                if client.get(url + "/health").status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.05)
    raise AssertionError("HTTP server did not start")


def test_gateway_and_mock_over_real_http(tmp_path):
    downstream_port = free_port()
    gateway_port = free_port()
    while gateway_port == downstream_port:
        gateway_port = free_port()
    downstream_url = f"http://127.0.0.1:{downstream_port}"
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    env = {**os.environ, "ADMIN_TOKEN": "http-test-admin", "VIEWER_TOKEN": "http-test-viewer",
           "DOWNSTREAM_URL": downstream_url + "/mcp"}
    processes = []
    with (tmp_path / "servers.log").open("w", encoding="utf-8") as logs:
        try:
            for module, port, url in [("mock_server:app", downstream_port, downstream_url),
                                      ("gateway:create_app", gateway_port, gateway_url)]:
                command = [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)]
                if module.startswith("gateway"):
                    command.append("--factory")
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=logs, stderr=logs)
                processes.append(process)
                wait_for_server(url, process)
            with httpx.Client(base_url=gateway_url, trust_env=False) as client:
                frame = {"jsonrpc": "2.0", "id": "http-1", "method": "tools/list"}
                response = client.post("/mcp", json=frame, headers={"Authorization": "Bearer http-test-viewer"})
                assert len(response.json()["result"]["tools"]) == 2
                frame.update(method="tools/call", params={"name": "admin_reset_key", "arguments": {}})
                blocked = client.post("/mcp", json=frame, headers={"Authorization": "Bearer http-test-viewer"})
                assert blocked.json()["error"]["code"] == -32001
                allowed = client.post("/mcp", json=frame, headers={"Authorization": "Bearer http-test-admin"})
                assert allowed.json()["result"]["content"][0]["text"] == "Key reset simulated"
                assert allowed.json()["id"] == "http-1"
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
