"""MQTT / Home Assistant bridge against a real broker (amqtt, or mosquitto when installed)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as paho
import pytest
from fastapi.testclient import TestClient

from switchyard import config as cfg
from switchyard.mqtt import mac_id, parse_url
from switchyard.web.app import create_app

from .conftest import PASSWORD, make_env

MAC = "00:26:f2:aa:10:01"
NODE = f"switchyard_{mac_id(MAC)}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def broker(tmp_path_factory):
    """A real MQTT broker on loopback.

    SWITCHYARD_TEST_MQTT_PORT: use an already running broker (CI configures mosquitto as a
    service; AppArmor stops mosquitto from reading a config file under /tmp). Otherwise start
    mosquitto if installed, else amqtt.
    """
    running = os.environ.get("SWITCHYARD_TEST_MQTT_PORT")
    if running:
        yield int(running)
        return
    port = _free_port()
    folder = tmp_path_factory.mktemp("broker")
    mosquitto = os.environ.get("SWITCHYARD_MOSQUITTO") or shutil.which("mosquitto")
    if mosquitto:
        conf = folder / "mosquitto.conf"
        conf.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
        cmd = [mosquitto, "-c", str(conf)]
    else:
        conf = folder / "amqtt.yaml"
        conf.write_text(
            "listeners:\n  default:\n    type: tcp\n"
            f"    bind: 127.0.0.1:{port}\n"
            "plugins:\n  amqtt.plugins.authentication.AnonymousAuthPlugin:\n"
            "    allow_anonymous: true\n"
        )
        cmd = [str(Path(sys.executable).parent / "amqtt"), "-c", str(conf)]
    log = folder / "broker.log"
    with log.open("wb") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
    for _ in range(300):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
    else:
        proc.kill()
    if proc.poll() is not None or proc.returncode is not None:
        message = f"MQTT broker {cmd[0]} did not start:\n{log.read_text()[-2000:]}"
        if os.environ.get("CI"):
            pytest.fail(message)  # in CI a broker is installed: never hide this as a skip
        pytest.skip(message)
    yield port
    proc.terminate()
    proc.wait(10)


class Recorder:
    """Subscribes to everything and keeps the latest retained/non-retained payload per topic."""

    def __init__(self, port: int, prefix: str):
        self.messages: dict[str, bytes] = {}
        self.lock = threading.Lock()
        self.client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=f"rec-{prefix}")
        self.client.on_message = self._on_message
        self.client.connect("127.0.0.1", port)
        self.client.subscribe("#", qos=1)
        self.client.loop_start()

    def _on_message(self, client, userdata, msg):
        with self.lock:
            self.messages[msg.topic] = msg.payload

    def wait(self, predicate, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if predicate(dict(self.messages)):
                    return dict(self.messages)
            time.sleep(0.1)
        with self.lock:
            raise AssertionError(f"timed out; topics: {sorted(self.messages)[:40]}")

    def publish(self, topic: str, payload: str):
        self.client.publish(topic, payload, qos=1).wait_for_publish(5)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def make_app(tmp_path, sim, port, prefix, **extra):
    env = make_env(
        sim.port,
        sim.tftp_port,
        SWITCHYARD_MQTT_URL=f"mqtt://127.0.0.1:{port}",
        SWITCHYARD_MQTT_TOPIC_PREFIX=prefix,
        SWITCHYARD_MQTT_DISCOVERY_PREFIX=f"ha-{prefix}",
        **extra,
    )
    return create_app(cfg.load(tmp_path, env=env))


def add_office(c):
    c.post("/api/login", json={"password": PASSWORD})
    c.post("/api/discovery/scan")
    assert (
        c.post(
            "/api/devices", json={"mac": MAC, "password": "password", "label": "Office"}
        ).status_code
        == 201
    )


def sample(c, times=2):
    m = c.app.state.manager
    for _ in range(times):
        c.portal.call(m.monitor.sample_all)
        time.sleep(0.15)


def test_parse_url():
    assert parse_url("mqtt://broker") == {
        "host": "broker",
        "port": 1883,
        "tls": False,
        "username": None,
        "password": None,
    }
    t = parse_url("mqtts://u%40x:p%2Fw@broker.lan:8884")
    assert (t["port"], t["tls"], t["username"], t["password"]) == (8884, True, "u@x", "p/w")
    assert parse_url("mqtts://b")["port"] == 8883
    for bad in ("http://b", "mqtt://", "broker:1883"):
        with pytest.raises(ValueError):
            parse_url(bad)


def test_discovery_state_and_availability(tmp_path, sim, broker):
    rec = Recorder(broker, "t1")
    try:
        with TestClient(make_app(tmp_path, sim, broker, "t1")) as c:
            add_office(c)
            c.put(f"/api/devices/{MAC}/labels", json={"ports": {"1": "NAS"}})
            sample(c)
            msgs = rec.wait(
                lambda m: (
                    f"ha-t1/binary_sensor/{NODE}/port1_link/config" in m
                    and m.get(f"t1/{mac_id(MAC)}/availability") == b"online"
                    and b'"rx_bps":' in m.get(f"t1/{mac_id(MAC)}/state", b"")
                    and b'"rx_bps":null' not in m.get(f"t1/{mac_id(MAC)}/state", b"x")
                )
            )
            assert msgs["t1/status"] == b"online"
            online = json.loads(msgs[f"ha-t1/binary_sensor/{NODE}/online/config"])
            assert (
                online["unique_id"] == f"{NODE}_online" and online["device_class"] == "connectivity"
            )
            assert online["availability"] == [{"topic": "t1/status"}]
            assert online["device"]["identifiers"] == [NODE] and online["device"][
                "connections"
            ] == [["mac", MAC]]
            assert online["device"]["name"] == "Office" and online["device"]["model"] == "GS108E"
            link = json.loads(msgs[f"ha-t1/binary_sensor/{NODE}/port1_link/config"])
            assert link["name"] == "Port 1 (NAS) link"
            assert {"topic": f"t1/{mac_id(MAC)}/availability"} in link["availability"]
            rx = json.loads(msgs[f"ha-t1/sensor/{NODE}/port1_rx/config"])
            assert rx["unit_of_measurement"] == "bit/s" and rx["device_class"] == "data_rate"
            crc = json.loads(msgs[f"ha-t1/sensor/{NODE}/port3_crc/config"])
            assert (
                crc["state_class"] == "total_increasing" and crc["entity_category"] == "diagnostic"
            )
            assert not any(
                "/switch/" in t or "/button/" in t for t in msgs
            )  # control is off by default
            state = json.loads(msgs[f"t1/{mac_id(MAC)}/state"])
            assert (
                state["online"] is True
                and state["firmware"] == "1.00.12"
                and state["ports_up"] == 5
            )
            assert state["ports"]["1"]["link"] is True and state["ports"]["1"]["speed"] == 1000
            assert state["ports"]["3"] == {
                "link": False,
                "speed": 0,
                "rx_bps": 0,
                "tx_bps": 0,
                "crc": 0,
            }
            assert state["ports"]["1"]["rx_bps"] > 0
            assert len([t for t in msgs if t.startswith("ha-t1/") and NODE in t]) == 3 + 8 * 5
            # the system page reports the bridge
            assert c.get("/api/system").json()["mqtt"]["state"] == "connected"
            # the switch goes silent: availability follows
            m = c.app.state.manager
            m.transport.timeout, m.transport.retries = 0.1, 0
            sim.switches[0].offline_until = time.monotonic() + 30
            sample(c, 1)
            rec.wait(lambda m: m.get(f"t1/{mac_id(MAC)}/availability") == b"offline")
            sim.switches[0].offline_until = 0
            # removing the switch removes its entities (empty retained configs)
            assert c.delete(f"/api/devices/{MAC}").status_code == 200
            rec.wait(lambda m: m.get(f"ha-t1/binary_sensor/{NODE}/port1_link/config") == b"")
        msgs = rec.wait(lambda m: m.get("t1/status") == b"offline")
    finally:
        rec.close()


def test_control_reboot_and_port_switch(tmp_path, sim, broker):
    rec = Recorder(broker, "t2")
    try:
        with TestClient(
            make_app(tmp_path, sim, broker, "t2", SWITCHYARD_MQTT_ALLOW_CONTROL="true")
        ) as c:
            add_office(c)
            sample(c)
            msgs = rec.wait(
                lambda m: (
                    f"ha-t2/switch/{NODE}/port4_enabled/config" in m
                    and b'"enabled":true' in m.get(f"t2/{mac_id(MAC)}/state", b"")
                )
            )
            button = json.loads(msgs[f"ha-t2/button/{NODE}/reboot/config"])
            assert (
                button["command_topic"] == f"t2/{mac_id(MAC)}/reboot/press"
                and "state_topic" not in button
            )
            sw = json.loads(msgs[f"ha-t2/switch/{NODE}/port4_enabled/config"])
            assert sw["command_topic"] == f"t2/{mac_id(MAC)}/port/4/set"
            # port 4 off through Home Assistant
            rec.publish(f"t2/{mac_id(MAC)}/port/4/set", "OFF")
            rec.wait(lambda m: b'"4":{"link":false' in m.get(f"t2/{mac_id(MAC)}/state", b""))
            assert sim.switches[0].admin[4][0] == 1
            rec.publish(f"t2/{mac_id(MAC)}/port/4/set", "ON")
            end = time.time() + 10
            while sim.switches[0].admin[4][0] != 0 and time.time() < end:
                time.sleep(0.1)
            assert sim.switches[0].admin[4][0] == 0
            rec.publish(f"t2/{mac_id(MAC)}/reboot/press", "PRESS")
            end = time.time() + 10
            while sim.switches[0].online and time.time() < end:
                time.sleep(0.05)
            assert not sim.switches[0].online
            entries = c.get("/api/audit").json()["entries"]
            assert any(e["role"] == "mqtt" and "reboot" in e["route"] for e in entries)
            assert any(e["role"] == "mqtt" and "port/4 OFF" in e["route"] for e in entries)
    finally:
        rec.close()


async def test_commands_are_refused_or_ignored_safely(tmp_path, sim, broker):
    from switchyard.mqtt import MqttBridge

    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_MQTT_URL=f"mqtt://127.0.0.1:{broker}")
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        add_office(c)
        bridge: MqttBridge = c.app.state.mqtt
        call = lambda t, p: c.portal.call(bridge.handle_command, t, p)  # noqa: E731
        assert call(f"switchyard/{mac_id(MAC)}/reboot/press", "PRESS") == "ignored: control is off"
        assert sim.switches[0].online
        bridge.allow_control = True
        assert call("switchyard/ffffffffffff/reboot/press", "PRESS") == "ignored: unknown switch"
        assert call(f"switchyard/{mac_id(MAC)}/reboot/press", "NOPE") == "ignored: unknown command"
        assert call(f"switchyard/{mac_id(MAC)}/port/x/set", "ON").startswith("failed")
        c.patch(f"/api/devices/{MAC}", json={"forget_password": True})
        result = call(f"switchyard/{mac_id(MAC)}/reboot/press", "PRESS")
        assert result.startswith("failed") and "password" in result
        assert sim.switches[0].online
        gs105 = "e0:91:f5:cc:30:03"  # no port settings on this model
        c.post("/api/devices", json={"mac": gs105, "password": "password"})
        assert (
            call(f"switchyard/{mac_id(gs105)}/port/1/set", "OFF")
            == "ignored: this switch has no port settings"
        )


def test_broker_refusal_and_bad_url_are_reported(tmp_path, sim):
    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_MQTT_URL="mqtt://127.0.0.1:9")
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        c.post("/api/login", json={"password": PASSWORD})
        status = c.get("/api/system").json()["mqtt"]
        assert status["state"] in ("connecting", "error") and status["broker"] == "127.0.0.1:9"
    with pytest.raises(ValueError):
        create_app(cfg.load(tmp_path / "x", env=make_env(sim.port, SWITCHYARD_MQTT_URL="http://x")))


def test_mqtt_off_by_default(client):
    assert client.get("/api/system").json()["mqtt"]["state"] == "off"
