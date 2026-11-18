"""Take the documentation screenshots from the demo server.

Run: python tools/screenshots.py [output-dir]
Needs Playwright with Chromium. Set SWITCHYARD_CHROMIUM to use another binary.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "demo-password-1"
MAC = "00:26:f2:aa:10:01"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, data: Path):
        self.port = free_port()
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
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "switchyard", "--data", str(data), "serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.url = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url + "/health", timeout=1).read()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)


def seed(page, base: str) -> None:
    """Prepare a nice demo state through the API (same session as the page)."""

    def call(method: str, path: str, body=None):
        r = page.request.fetch(
            f"{base}/api{path}",
            method=method,
            data=json.dumps(body) if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        assert r.ok, (path, r.status, r.text())
        return r.json()

    found = call("POST", "/discovery/scan")["found"]
    labels = {
        "GS108E": "Office",
        "GS108Ev3": "Rack top",
        "GS105E": "Living room",
        "JGS516PE": "Basement",
    }
    for f in found:
        call(
            "POST",
            "/devices",
            {"mac": f["mac"], "label": labels.get(f["model"], ""), "password": "password"},
        )
    call("POST", "/discovery/scan")
    base_dev = f"/devices/{MAC}"
    call("PUT", f"{base_dev}/vlan/mode", {"mode": "dot1q_advanced"})
    call(
        "PUT",
        f"{base_dev}/vlan/dot1q",
        {
            "vlans": [
                {"id": 1, "members": [1, 2, 3, 4], "tagged": []},
                {"id": 10, "members": [1, 5, 6], "tagged": [1]},
                {"id": 20, "members": [1, 7, 8], "tagged": [1]},
            ],
            "pvid": {"5": 10, "6": 10, "7": 20, "8": 20},
        },
    )
    call(
        "PUT",
        f"{base_dev}/qos",
        {
            "mode": "port",
            "priorities": {"1": "high", "5": "low"},
            "ingress": {"6": 5},
            "egress": {"6": 7},
        },
    )
    call("PUT", f"{base_dev}/mirror", {"destination": 8, "sources": [1, 2]})
    call("PUT", f"{base_dev}/lag", {"lags": [{"id": 1, "enabled": True, "members": [5, 6]}]})


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    chromium = os.environ.get("SWITCHYARD_CHROMIUM")
    with tempfile.TemporaryDirectory() as data:
        server = Server(Path(data))
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    executable_path=chromium, args=["--no-sandbox", "--disable-gpu"]
                )
                desktop = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    device_scale_factor=2,
                    color_scheme="light",
                )
                page = desktop.new_page()
                page.goto(server.url)
                page.get_by_test_id("login-password").fill(PASSWORD)
                page.get_by_test_id("login-submit").click()
                page.get_by_test_id("scan").wait_for()
                seed(page, server.url)

                def shot(name: str, hash_: str, ready: str, pause: float = 0.6):
                    page.goto(f"{server.url}/#{hash_}")
                    page.reload()
                    page.get_by_test_id(ready).first.wait_for()
                    time.sleep(pause)
                    page.screenshot(path=str(out / name))

                shot("switches.png", "/", "switch-card")
                base = f"/switch/{MAC}"
                shot("overview.png", f"{base}/overview", "port-panel")
                shot("vlan.png", f"{base}/vlan", "vlan-matrix")
                shot("qos.png", f"{base}/qos", "qos-save")
                shot("lag.png", f"{base}/lag", "lag-matrix")
                shot("firmware.png", f"{base}/firmware", "fw-file")
                page.goto(f"{server.url}/#/log")
                page.reload()
                page.get_by_test_id("log-row").first.wait_for()
                page.screenshot(path=str(out / "log.png"))

                dark = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    device_scale_factor=2,
                    color_scheme="dark",
                )
                dpage = dark.new_page()
                dpage.goto(server.url)
                dpage.get_by_test_id("login-password").fill(PASSWORD)
                dpage.get_by_test_id("login-submit").click()
                dpage.get_by_test_id("scan").wait_for()
                dpage.goto(f"{server.url}/#{base}/overview")
                dpage.reload()
                dpage.get_by_test_id("port-panel").wait_for()
                time.sleep(0.6)
                dpage.screenshot(path=str(out / "overview-dark.png"))

                phone = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    device_scale_factor=2,
                    is_mobile=True,
                    has_touch=True,
                )
                ppage = phone.new_page()
                ppage.goto(server.url)
                ppage.get_by_test_id("login-password").fill(PASSWORD)
                ppage.get_by_test_id("login-submit").click()
                ppage.get_by_test_id("switch-card").first.wait_for()
                time.sleep(0.6)
                ppage.screenshot(path=str(out / "phone-switches.png"))
                browser.close()
        finally:
            server.stop()
    print("wrote", ", ".join(sorted(p.name for p in out.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
