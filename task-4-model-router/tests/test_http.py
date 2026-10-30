"""Use real HTTP and restart the gateway while keeping its SQLite file."""

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


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_real_fallback_and_persistence_after_gateway_restart(tmp_path):
    ports = set()
    while len(ports) < 3:
        ports.add(free_port())
    primary_port, backup_port, gateway_port = sorted(ports)
    primary = f"http://127.0.0.1:{primary_port}"
    backup = f"http://127.0.0.1:{backup_port}"
    gateway = f"http://127.0.0.1:{gateway_port}"
    env = {**os.environ, "TENANT_API_KEYS": "http-test-key", "TOKENS_PER_MINUTE": "9",
           "SQLITE_PATH": str(tmp_path / "persistent.db"),
           "PRIMARY_URL": primary + "/completions", "BACKUP_URL": backup + "/completions",
           "PRIMARY_MOCK_MODE": "rate_limit", "BACKUP_MOCK_MODE": "success"}
    processes = []
    with (tmp_path / "servers.log").open("w", encoding="utf-8") as logs:
        def start(module, port, url):
            command = [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)]
            if module.startswith("gateway"):
                command.append("--factory")
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=logs, stderr=logs)
            processes.append(process)
            wait_for_server(url, process)
            return process
        try:
            start("mock_provider:primary_app", primary_port, primary)
            start("mock_provider:backup_app", backup_port, backup)
            old_gateway = start("gateway:create_app", gateway_port, gateway)
            body = {"prompt": "demo", "max_output_tokens": 5}
            with httpx.Client(base_url=gateway, trust_env=False) as client:
                response = client.post("/completions", json=body, headers={"Authorization": "Bearer http-test-key"})
            assert response.status_code == 200
            assert response.json()["provider"] == "backup"
            assert response.json()["usage"] == {"input_tokens": 4, "output_tokens": 5}
            stop(old_gateway)
            start("gateway:create_app", gateway_port, gateway)
            with httpx.Client(base_url=gateway, trust_env=False) as client:
                denied = client.post("/completions", json=body, headers={"Authorization": "Bearer http-test-key"})
            assert denied.status_code == 429
            assert denied.json()["error"]["code"] == "token_limit_exceeded"
            assert (tmp_path / "persistent.db").is_file()
        finally:
            for process in reversed(processes):
                stop(process)
