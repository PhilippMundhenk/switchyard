"""Browser tests: the real server in demo mode, driven through a real Chromium."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

PASSWORD = "e2e-password-1"
ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, data: Path):
        self.port = _free_port()
        env = {
            **os.environ,
            "SWITCHYARD_DEMO_ENABLED": "true",
            "SWITCHYARD_AUTH_PASSWORD": PASSWORD,
            "SWITCHYARD_SERVER_HOST": "127.0.0.1",
            "SWITCHYARD_SERVER_PORT": str(self.port),
            "SWITCHYARD_NSDP_POLL_SECONDS": "0",
            "SWITCHYARD_MONITOR_INTERVAL_SECONDS": "1",
            "SWITCHYARD_NSDP_DISCOVERY_SECONDS": "0.4",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "switchyard", "--data", str(data), "serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.url = f"http://127.0.0.1:{self.port}"
        for _ in range(150):
            try:
                urllib.request.urlopen(self.url + "/health", timeout=1).read()
                return
            except OSError:
                time.sleep(0.1)
        self.proc.kill()
        raise RuntimeError("server did not start")

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(10)


@pytest.fixture
def server(tmp_path):
    srv = LiveServer(tmp_path)
    yield srv
    srv.stop()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    args = {**browser_type_launch_args, "args": ["--no-sandbox", "--disable-gpu"]}
    if os.environ.get("SWITCHYARD_CHROMIUM"):
        args["executable_path"] = os.environ["SWITCHYARD_CHROMIUM"]
    return args


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1280, "height": 900}}


@pytest.fixture
def app(page, server):
    """A page that is logged in."""
    page.set_default_timeout(15000)
    page.goto(server.url)
    page.get_by_test_id("login-password").fill(PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("scan").wait_for()
    page.server = server
    return page


def api(page, method: str, path: str, body=None):
    import json

    r = page.request.fetch(
        f"{page.server.url}/api{path}",
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    return r


def add_switch(page, mac: str, password: str | None = None, label: str = ""):
    r = api(page, "POST", "/discovery/scan", {})
    assert r.ok
    body = {"mac": mac, "label": label}
    if password:
        body["password"] = password
    r = api(page, "POST", "/devices", body)
    assert r.ok, r.text()
