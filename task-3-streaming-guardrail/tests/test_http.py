"""Check real network delivery; HTTPX ASGITransport buffers full responses."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sse import SSEParser


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(url, process):
    deadline = time.monotonic() + 20
    with httpx.Client(timeout=0.5, trust_env=False) as client:
        while time.monotonic() < deadline:
            assert process.poll() is None, "Server exited during startup"
            try:
                if client.get(url + "/health").status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.05)
    raise AssertionError("Server did not start")


def test_real_http_delivers_redacted_text_before_provider_finishes(tmp_path):
    provider_port, gateway_port = free_port(), free_port()
    while gateway_port == provider_port:
        gateway_port = free_port()
    provider_url = f"http://127.0.0.1:{provider_port}"
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    env = {**os.environ, "PROVIDER_URL": provider_url + "/generate"}
    processes = []
    with (tmp_path / "servers.log").open("w", encoding="utf-8") as logs:
        try:
            for module, port, url in [("mock_provider:app", provider_port, provider_url),
                                      ("gateway:create_app", gateway_port, gateway_url)]:
                command = [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)]
                if module.startswith("gateway"):
                    command.append("--factory")
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=logs, stderr=logs)
                processes.append(process)
                wait_for_server(url, process)
            events = []
            parser = SSEParser()
            with httpx.Client(timeout=5, trust_env=False) as client:
                with client.stream("POST", gateway_url + "/generate", json={"prompt": "demo"}) as response:
                    assert response.status_code == 200
                    for raw in response.iter_raw():
                        received = time.monotonic()
                        for name, data in parser.feed(raw.decode("utf-8")):
                            events.append((name, json.loads(data), received))
            text = "".join(data["text"] for name, data, _ in events if name == "delta")
            assert text == "Hello! Email [REDACTED]; SSN [REDACTED]; card [REDACTED]. Done!"
            assert events[0][0] == "delta" and events[-1][0] == "done"
            assert events[-1][2] - events[0][2] >= 0.05
            assert all(value not in text for value in ("alex", "123-45", "4111"))
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
