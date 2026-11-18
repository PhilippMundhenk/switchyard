import http.server
import threading
import time

import pytest
from fastapi.testclient import TestClient

from switchyard import config as cfg
from switchyard.notify import post_json
from switchyard.web.app import create_app

from .conftest import PASSWORD, make_env

MAC = "00:26:f2:aa:10:01"
D = f"/api/devices/{MAC}"


def manager_of(client):
    return client.app_.state.manager


def sample(client):
    client.portal.call(manager_of(client).monitor.sample_all)


def test_samples_build_history_and_snapshot(client, office):
    sample(client)
    time.sleep(0.2)
    sample(client)
    h = client.get(f"{D}/history?minutes=5").json()
    assert "0" in h["series"] and "1" in h["series"]
    assert all(len(point) == 3 for point in h["series"]["1"])
    assert h["series"]["1"][-1][1] > 0  # port 1 carries simulated traffic
    ports = client.get("/api/devices").json()["devices"][0]["ports"]
    assert [p["port"] for p in ports] == list(range(1, 9)) and ports[0]["up"] is True
    assert client.get("/api/devices/aa:bb:cc:dd:ee:ff/history").status_code == 404


def test_link_and_online_events_are_recorded(client, office):
    m = manager_of(client)
    sample(client)
    assert client.get("/api/events").json()["events"] == []  # first sample is the baseline
    sw = client.sim.switches[0]
    sw.links[2] = 5  # port 3 comes up
    sw.links[0] = 0  # port 1 goes down
    sw.links[3] = 5  # port 4 changes from 100M to 1G
    sample(client)
    texts = [e["text"] for e in client.get("/api/events").json()["events"]]
    assert "Port 3 came up at 1 Gbit/s" in texts and "Port 1 went down" in texts
    assert "Port 4 changed from 100 Mbit/s to 1 Gbit/s" in texts
    # switch goes silent, then returns
    m.transport.timeout, m.transport.retries = 0.1, 0
    sw.offline_until = time.monotonic() + 30
    sample(client)
    sw.offline_until = 0
    sample(client)
    types = [e["type"] for e in client.get("/api/events").json()["events"]]
    assert types[-2:] == ["offline", "online"]
    last = client.get("/api/events").json()["events"][-1]["id"]
    assert client.get(f"/api/events?after={last}").json()["events"] == []
    assert client.get(f"/api/events?device={MAC}").json()["events"]
    assert client.get("/api/events?device=other").json()["events"] == []


def test_history_ignores_counter_resets(client, office):
    sample(client)
    client.post(f"{D}/stats/reset")
    time.sleep(0.1)
    sample(client)
    h = client.get(f"{D}/history").json()
    assert all(p[1] >= 0 and p[2] >= 0 for p in h["series"].get("1", []))


def test_metrics_need_session_or_token(tmp_path, sim):
    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_METRICS_TOKEN="scrape-token")
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert (
            c.get("/metrics", headers={"Authorization": "Bearer scrape-token"}).status_code == 200
        )
        c.post("/api/login", json={"password": PASSWORD})
        assert c.get("/metrics").status_code == 200


def test_metrics_content(client, office):
    sample(client)
    body = client.get("/metrics").text
    assert f'switchyard_switch_up{{mac="{MAC}",model="GS108E",name="office"}} 1' in body
    assert f'switchyard_port_up{{mac="{MAC}",port="1"}} 1' in body
    assert "# TYPE switchyard_port_received_bytes_total counter" in body
    assert f'switchyard_port_speed_mbps{{mac="{MAC}",port="1"}} 1000' in body
    assert client.get("/metrics").headers["content-type"].startswith("text/plain")


def test_metrics_escape_labels(client, office):
    client.patch(D, json={"label": 'a"b\\c'})
    assert 'name="a\\"b\\\\c"' in client.get("/metrics").text


def test_monitor_loop_runs_in_background(tmp_path, sim):
    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_MONITOR_INTERVAL_SECONDS="1")
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        c.post("/api/login", json={"password": PASSWORD})
        c.post("/api/discovery/scan")
        c.post("/api/devices", json={"mac": MAC})
        deadline = time.time() + 6
        while time.time() < deadline:
            if c.get("/api/devices").json()["devices"][0]["ports"]:
                break
            time.sleep(0.3)
        else:
            raise AssertionError("monitor never sampled")


class _Hook(http.server.BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        _Hook.bodies.append((self.headers["Content-Type"], self.rfile.read(length)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def hook_server():
    _Hook.bodies = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/hook"
    srv.shutdown()


def test_webhook_receives_events(tmp_path, sim, hook_server):
    import json

    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_NOTIFY_WEBHOOK_URL=hook_server)
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        c.post("/api/login", json={"password": PASSWORD})
        c.post("/api/discovery/scan")
        c.post("/api/devices", json={"mac": MAC, "password": "password"})
        m = c.app.state.manager
        c.portal.call(m.monitor.sample, MAC)
        sim.switches[0].links[2] = 5
        c.portal.call(m.monitor.sample, MAC)
        deadline = time.time() + 5
        while not _Hook.bodies and time.time() < deadline:
            time.sleep(0.1)
    ctype, raw = _Hook.bodies[0]
    body = json.loads(raw)
    assert ctype == "application/json" and body["event"] == "link_up" and body["mac"] == MAC
    assert "Port 3 came up" in body["text"]


async def test_webhook_failures_do_not_raise():
    assert await post_json("http://127.0.0.1:9/none", {"a": 1}) is False
    assert await post_json("file:///etc/passwd", {"a": 1}) is False
