from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from switchyard import config as cfg
from switchyard.nsdp import Transport
from switchyard.sim import SimSwitch, Simulator, demo_switches
from switchyard.web.app import create_app

PASSWORD = "test-password-1"


@pytest.fixture
def sim():
    """The demo fleet on loopback, served from its own thread and event loop."""
    ready = threading.Event()
    box: dict = {}

    def runner():
        loop = asyncio.new_event_loop()
        simulator = Simulator(demo_switches())
        loop.run_until_complete(simulator.start())
        box.update(loop=loop, sim=simulator)
        ready.set()
        loop.run_forever()
        simulator.stop()
        loop.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    assert ready.wait(5)
    simulator = box["sim"]
    for switch in simulator.switches:
        switch.reboot_seconds = 0.4
    yield simulator
    box["loop"].call_soon_threadsafe(box["loop"].stop)
    thread.join(5)


@pytest.fixture
async def transport(sim):
    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.5, retries=1)
    await t.start()
    yield t
    await t.stop()


def make_env(sim_port: int, tftp_port: int = 69, **extra) -> dict[str, str]:
    env = {
        "SWITCHYARD_NSDP_TARGETS": f"127.0.0.1:{sim_port}",
        "SWITCHYARD_NSDP_CLIENT_PORT": "0",
        "SWITCHYARD_NSDP_TFTP_PORT": str(tftp_port),
        "SWITCHYARD_NSDP_POLL_SECONDS": "0",
        "SWITCHYARD_MONITOR_INTERVAL_SECONDS": "0",
        "SWITCHYARD_NSDP_DISCOVERY_SECONDS": "0.3",
        "SWITCHYARD_AUTH_PASSWORD": PASSWORD,
    }
    env.update(extra)
    return env


@pytest.fixture
def config(tmp_path, sim):
    return cfg.load(tmp_path, env=make_env(sim.port, sim.tftp_port))


@pytest.fixture
def client(config, sim):
    app = create_app(config)
    with TestClient(app, base_url="http://testserver") as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        c.sim = sim
        c.app_ = app
        yield c


@pytest.fixture
def office(client):
    """The first demo switch, discovered and added with its password."""
    client.post("/api/discovery/scan")
    mac = "00:26:f2:aa:10:01"
    r = client.post("/api/devices", json={"mac": mac, "password": "password"})
    assert r.status_code == 201, r.text
    return mac


def quick(**kw) -> SimSwitch:
    return SimSwitch("02:00:00:00:00:01", reboot_seconds=0.3, **kw)
