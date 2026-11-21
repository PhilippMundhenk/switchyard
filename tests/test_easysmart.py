"""TP-Link Easy Smart: packets, both protocol generations, settings, API, against the simulator."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from switchyard import config as cfg
from switchyard.drivers import AuthError, PasswordRequired, Unreachable, Unsupported
from switchyard.easysmart import tags as T
from switchyard.easysmart.client import EasySmartClient, EasySmartError
from switchyard.easysmart.packet import (
    RC4_STATE,
    Op,
    Packet,
    key_exchange_value,
    scramble,
    session_scramble,
)
from switchyard.easysmart.sim import (
    SIM_MODULUS,
    EasySmartSimulator,
    SimEasySmart,
    demo_easysmart,
)
from switchyard.easysmart.transport import EasySmartTransport
from switchyard.nsdp.packet import parse_mac
from switchyard.web.app import create_app

from .conftest import PASSWORD, make_env

MAC_A = "50:c7:bf:5e:00:01"  # older firmware: tokens
MAC_B = "50:c7:bf:5e:00:02"  # newer firmware: session key


# -- packets -----------------------------------------------------------------


def test_static_cipher_matches_published_capture():
    # a reply captured from a TL-SG105E (port count 5), as published with tplink-ess-lib
    cipher = bytes.fromhex(
        "5d761a4b2a37d1d8e7700b2685317e00422ba2f5d786aeed508f0019c202909a5b1981c722d5d43649"
    )
    packet = Packet.decode(cipher)
    assert (packet.op, packet.seq, packet.token) == (Op.REPLY, 149, 17956)
    assert packet.tlvs == [(T.PORT_COUNT, b"\x05")]
    assert scramble(bytes(16)).hex() == "5c746a047dbeb0b2fb6b06c314957e95"
    assert sorted(RC4_STATE) == list(range(256))


def test_packet_round_trip_and_rejects():
    p = Packet(Op.LOGIN, b"\x01" * 6, b"\x02" * 6, 999, token=7, tlvs=[(T.LED, b"\x01")])
    q = Packet.decode(p.encode())
    assert (q.op, q.seq, q.token, q.tlvs) == (Op.LOGIN, 999, 7, [(T.LED, b"\x01")])
    assert q.first(T.LED) == b"\x01" and q.first(T.IP) is None and q.has(T.LED)
    key = b"abcdEFGH"
    r = Packet.decode(p.encode_session(key), key)
    assert r.tlvs == p.tlvs
    assert session_scramble(session_scramble(b"hello", key), key) == b"hello"
    with pytest.raises(ValueError):
        Packet.decode(b"\x00" * 10)
    with pytest.raises(ValueError):
        Packet.decode(scramble(bytes(40)))  # version 0
    with pytest.raises(ValueError):
        Packet.decode(bytes(2000))
    with pytest.raises(ValueError):
        scramble(bytes(2000))
    bad = bytearray(p.encode_plain())
    bad[34:36] = b"\x00\x50"  # a TLV longer than the packet
    with pytest.raises(ValueError):
        Packet.decode(scramble(bytes(bad)))


def test_key_exchange_value():
    value = key_exchange_value(b"ABCDEFGH", SIM_MODULUS)
    assert value[0] == 0 and 1 <= value[1] <= 4 and len(value) == 10


def test_codecs():
    assert T.mask_ports(0b1010, 8) == [2, 4] and T.ports_mask([2, 4]) == 0b1010
    vlan = T.dot1q_vlan(T.pack_dot1q_vlan(10, [1, 2, 3], [3], "cams"), 8)
    assert vlan == {"id": 10, "members": [1, 2, 3], "tagged": [3], "name": "cams"}
    with pytest.raises(ValueError):
        T.pack_dot1q_vlan(10, [1], [2])
    with pytest.raises(ValueError):
        T.pack_dot1q_vlan(5000, [1], [])
    with pytest.raises(ValueError):
        T.pack_port_rate(1, 100)
    with pytest.raises(ValueError):
        T.pack_storm(1, 100)
    assert T.storm(T.pack_storm(2, 0))["enabled"] is False
    assert T.mirror(bytes(10), 8) == {"destination": 0, "sources": [], "ingress": [], "egress": []}
    with pytest.raises(ValueError):
        T.pack_port_settings(1, True, 9, False)
    assert T.link(6)["speed_mbps"] == 1000 and T.link(0)["up"] is False


# -- against the simulator ---------------------------------------------------


@pytest.fixture
def tpsim():
    ready = threading.Event()
    box: dict = {}

    def runner():
        loop = asyncio.new_event_loop()
        sim = EasySmartSimulator(demo_easysmart())
        loop.run_until_complete(sim.start())
        box.update(loop=loop, sim=sim)
        ready.set()
        loop.run_forever()
        sim.stop()
        loop.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    assert ready.wait(5)
    sim = box["sim"]
    for switch in sim.switches:
        switch.reboot_seconds = 0.4
    yield sim
    box["loop"].call_soon_threadsafe(box["loop"].stop)
    thread.join(5)


@pytest.fixture
async def tptransport(tpsim):
    t = EasySmartTransport(client_port=0, targets=[f"127.0.0.1:{tpsim.port}"], timeout=0.4)
    await t.start()
    yield t
    await t.stop()


def client_for(transport, mac, password="admin"):
    return EasySmartClient(transport, parse_mac(mac), password, "admin", rsa_modulus=SIM_MODULUS)


@pytest.mark.parametrize("mac", [MAC_A, MAC_B])
async def test_read_everything(tptransport, tpsim, mac):
    c = client_for(tptransport, mac)
    info = await c.read_info()
    assert info["mac"] == mac and info["ports"] in (5, 8)
    assert c.generation == ("A" if mac == MAC_A else "B")
    state = await c.read_state()
    assert state["vlan"]["mode"] == "disabled" and state["qos"]["mode"] == "port"
    assert state["mirror"] == {"destination": 0, "sources": [], "ingress": [], "egress": []}
    assert state["igmp"]["enabled"] is False and state["loop_detection"] is False
    assert state["led"] is True and state["poe"] is None
    ports = state["ports"]
    assert ports[0]["up"] and ports[0]["speed_mbps"] == 1000 and ports[0]["rx_bytes"] is None
    assert "rx_packets" in ports[0] and ports[0]["errors"] == 0
    assert state["port_admin"][1]["speed"] == "auto"
    assert state["lag"]["groups"] == (2 if mac == MAC_A else 1)
    # an unknown setting is silence, remembered as not supported
    assert await c._read(0x7777) is None and 0x7777 in c.unsupported


async def test_discovery(tptransport, tpsim):
    from switchyard.drivers.easysmart import EasySmartBackend

    backend = EasySmartBackend(tptransport, SIM_MODULUS)
    found = await backend.discover(0.3)
    assert {f["mac"] for f in found} == {MAC_A, MAC_B}
    a = next(f for f in found if f["mac"] == MAC_A)
    assert a["model"] == "TL-SG108PE" and a["firmware"].startswith("1.0.0")
    assert backend.packet_log() and backend.driver(MAC_A, None, None).username == "admin"


@pytest.mark.parametrize("mac", [MAC_A, MAC_B])
async def test_login_and_password(tptransport, tpsim, mac):
    c = client_for(tptransport, mac, None)
    with pytest.raises(PasswordRequired):
        await c.set_loop_detection(True)
    with pytest.raises(AuthError):
        await c.login("wrong")
    with pytest.raises(AuthError):
        await c.login("admin", "root")
    assert c.username == "admin"
    await c.login("admin")
    await c.change_password("n3w-secret")
    switch = next(s for s in tpsim.switches if s.mac == mac)
    assert switch.config["password"] == "n3w-secret"
    with pytest.raises(ValueError):
        await c.change_password("x" * 17)


async def test_token_is_single_use(tptransport, tpsim):
    c = client_for(tptransport, MAC_A)
    await c.read_info()
    token = (await c._exchange(Op.GET, [(T.TOKEN, b"")])).token
    creds = [(T.USERNAME, T.cstr("admin")), (T.PASSWORD, T.cstr("admin"))]
    await c._exchange(Op.LOGIN, creds, token, retries=0)
    with pytest.raises(Unreachable):  # a spent token gets no answer
        await c._exchange(Op.LOGIN, creds, token, retries=0)


async def test_writes_and_save(tptransport, tpsim):
    a = tpsim.switches[0]
    c = client_for(tptransport, MAC_A)
    await c.set_loop_detection(True)
    await c.set_led(False)
    await c.set_igmp(enabled=True, report_suppression=True)
    features = await c.read_features()
    assert features["loop_detection"] and features["led"] is False
    assert features["igmp"]["report_suppression"] is True
    with pytest.raises(Exception, match="only IGMP snooping"):
        await c.set_igmp(block_unknown=True)
    # this switch does not save by itself: Switchyard sends 'save' after every change
    assert a.saves >= 3 and a.saved["loop"] is True
    await c.set_mirror(8, [1, 2])
    assert (await c.read_features())["mirror"]["sources"] == [1, 2]
    with pytest.raises(ValueError):
        await c.set_mirror(1, [1])
    with pytest.raises(ValueError):
        await c.set_mirror(9, [1])
    with pytest.raises(ValueError):
        await c.set_mirror(2, [])
    await c.set_mirror(0, [])
    assert (await c.read_features())["mirror"]["destination"] == 0


async def test_session_firmware_saves_itself(tptransport, tpsim):
    b = tpsim.switches[1]
    c = client_for(tptransport, MAC_B)
    await c.set_loop_detection(True)
    assert b.config["loop"] is True and b.saves == 0  # auto-save: no extra 'save'
    # a restart loses the session; the client sets up a new one and carries on
    await c.reboot()
    time.sleep(0.5)
    assert (await c.read_info())["mac"] == MAC_B and c.generation == "B"


async def test_vlans(tptransport, tpsim):
    c = client_for(tptransport, MAC_A)
    await c.apply_dot1q(
        [
            {"id": 1, "members": [1, 2, 3, 4, 5, 6, 7, 8], "tagged": []},
            {"id": 20, "members": [7, 8], "tagged": [8], "name": "iot"},
        ],
        {7: 20},
    )
    vlan = await c.read_vlan()
    assert vlan["mode"] == "dot1q_advanced" and vlan["max_vlans"] == 32
    assert {v["id"] for v in vlan["dot1q"]} == {1, 20} and vlan["pvid"][7] == 20
    assert next(v for v in vlan["dot1q"] if v["id"] == 20)["name"] == "iot"
    with pytest.raises(ValueError, match="not a VLAN"):
        await c.apply_dot1q([{"id": 1, "members": [1], "tagged": []}], {2: 30})
    with pytest.raises(ValueError, match="does not exist"):
        await c.apply_dot1q([{"id": 1, "members": [1], "tagged": []}], {9: 1})
    await c.apply_dot1q([{"id": 1, "members": [1, 2, 3, 4, 5, 6, 7, 8], "tagged": []}], {7: 1})
    assert {v["id"] for v in (await c.read_vlan())["dot1q"]} == {1}
    await c.apply_dot1q(
        [
            {"id": 1, "members": [1, 2, 3, 4, 5, 6, 7, 8], "tagged": []},
            {"id": 30, "members": [3], "tagged": []},
        ],
        {},
    )
    await c.delete_vlan(30)
    await c.set_vlan_mode("port_basic")
    await c.set_port_vlans([{"id": 2, "members": [5, 6]}])
    vlan = await c.read_vlan()
    assert vlan["mode"] == "port_basic" and {"id": 2, "members": [5, 6]} in vlan["port_vlans"]
    await c.set_port_vlans([])
    await c.set_vlan_mode("disabled")
    assert (await c.read_vlan())["mode"] == "disabled"
    with pytest.raises(ValueError):
        await c.set_vlan_mode("weird")


async def test_qos_lag_ports_network(tptransport, tpsim):
    a = tpsim.switches[0]
    c = client_for(tptransport, MAC_A)
    await c.set_qos(mode="port", priorities={1: "high"}, ingress={2: 3}, egress={2: 12})
    qos = await c.read_qos()
    assert qos["priorities"][1] == "high" and qos["priorities"][2] == "low"
    assert c.rate_codes[qos["ingress"][2]] == 2048 and qos["egress"][2] == 12
    await c.set_qos(storm_rates={3: 2})
    assert (await c.read_qos())["storm_control"] is True
    await c.set_qos(storm_control=False)
    assert (await c.read_qos())["storm_control"] is False
    with pytest.raises(ValueError):
        await c.set_qos(mode="nonsense")
    with pytest.raises(ValueError):
        await c.set_qos(ingress={1: 99})
    a.config["ingress"][4] = 640  # a value the switch's own page allows: shown as a new code
    qos = await c.read_qos()
    assert c.rate_codes[qos["ingress"][4]] == 640

    await c.apply_lags([{"id": 1, "enabled": True, "members": [1, 2]}])
    lag = await c.read_lag()
    assert lag["lags"][0]["members"] == [1, 2] and lag["ranges"][2] == [5, 6, 7, 8]
    with pytest.raises(ValueError, match="only use ports 5 to 8"):
        await c.apply_lags([{"id": 2, "enabled": True, "members": [1, 5]}])
    with pytest.raises(ValueError, match="2 to 4"):
        await c.apply_lags([{"id": 2, "enabled": True, "members": [5]}])
    with pytest.raises(ValueError, match="1 to 2"):
        await c.apply_lags([{"id": 3, "enabled": True, "members": [5, 6]}])
    await c.apply_lags([{"id": 1, "enabled": True, "members": [3, 4]}])
    assert (await c.read_lag())["lags"][0]["members"] == [3, 4]
    await c.apply_lags([{"id": 1, "enabled": False, "members": []}])
    assert (await c.read_lag())["lags"][0]["members"] == []

    await c.set_port_admin({3: {"speed_code": 1}, 4: {"speed_code": 6, "flow_control": True}})
    admin = await c.read_port_admin()
    assert admin[3]["speed"] == "disabled" and admin[4]["speed"] == "1G full"
    assert admin[4]["flow_control"] is True
    with pytest.raises(ValueError, match="flow control"):
        await c.set_port_admin({5: {"speed_code": 5, "flow_control": True}})
    with pytest.raises(ValueError):
        await c.set_port_admin({9: {"speed_code": 0}})
    await c.set_port_admin({3: {"speed_code": 0}})
    assert (await c.read_port_admin())[3]["speed"] == "auto"

    await c.set_network(name="garage-2")
    assert (await c.read_info())["name"] == "garage-2"
    await c.set_network(dhcp=False, ip="192.168.0.50", netmask="255.255.255.0")
    assert a.config["ip"] == "192.168.0.50"
    with pytest.raises(ValueError, match="same subnet"):
        await c.set_network(ip="192.168.0.50", netmask="255.255.255.0", gateway="10.0.0.1")
    with pytest.raises(ValueError, match="letters"):
        await c.set_network(name="bad name!")
    await c.set_network(dhcp=True)
    assert a.config["dhcp"] is True


async def test_errors_and_reboot(tptransport, tpsim):
    a = tpsim.switches[0]
    c = client_for(tptransport, MAC_A)
    with pytest.raises(EasySmartError, match="refused"):
        await c.write([(T.PVID, bytes([1, 0, 99]))])
    with pytest.raises(Unsupported):
        await c.cable_test(1)
    # a change that was not saved is gone after a restart without saving
    await c.write([(T.LED, b"\x00")], save=False)
    a.config["led"] = False
    await c._write([(T.REBOOT, b"\x00")])
    time.sleep(0.5)
    assert (await c.read_features())["led"] is True
    await c.reboot()
    time.sleep(0.5)
    await c.factory_reset()
    time.sleep(0.5)
    assert (await c.read_info())["name"] == "garage"


async def test_diagnostics_scan(tptransport, tpsim):
    c = client_for(tptransport, MAC_A)
    tags = c.diagnostic_tags()
    assert T.PASSWORD not in tags and T.REBOOT not in tags and T.PORT_STATS in tags
    assert not c.tag_allowed(T.PASSWORD) and c.tag_allowed(0x1234)
    rows = await c.scan_tags([T.DOT1Q_VLAN, 0x7777], timeout=0.2)
    assert rows[0]["answered"] and T.VLAN_LIMITS in rows[0]["also"]
    assert rows[1]["status"] is None and not rows[1]["answered"]


async def test_unreachable(tptransport):
    c = client_for(tptransport, "50:c7:bf:5e:00:99")
    tptransport.timeout = 0.1
    with pytest.raises(Unreachable):
        await c.read_info()


# -- through the API ---------------------------------------------------------


def test_api_with_tplink(tmp_path, sim, tpsim):
    env = make_env(
        sim.port,
        sim.tftp_port,
        SWITCHYARD_EASYSMART_ENABLED="true",
        SWITCHYARD_EASYSMART_TARGETS=f"127.0.0.1:{tpsim.port}",
        SWITCHYARD_EASYSMART_CLIENT_PORT="0",
    )
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        backend = c.app.state.manager.backends["easysmart"]
        backend.rsa_modulus = SIM_MODULUS
        backend.transport.timeout = 0.4
        found = c.post("/api/discovery/scan").json()["found"]
        assert {"nsdp", "easysmart"} <= {f["protocol"] for f in found}
        r = c.post("/api/devices", json={"mac": MAC_A, "password": "admin", "username": "admin"})
        assert r.status_code == 201, r.text
        device = r.json()
        assert device["vendor"] == "TP-Link" and device["has_password"]
        assert device["needs_username"] and device["unicast"] is False
        state = c.get(f"/api/devices/{MAC_A}/state").json()
        assert state["info"]["model"] == "TL-SG108PE" and state["lag"]["groups"] == 2
        r = c.put(f"/api/devices/{MAC_A}/loop-detection", json={"enabled": True})
        assert r.status_code == 200, r.text
        r = c.post(f"/api/devices/{MAC_A}/cable-test", json={"ports": [1]})
        assert r.status_code == 409 and r.json()["code"] == "unsupported"
        diag = c.get(f"/api/devices/{MAC_A}/diagnostics").json()
        assert any(t["answered"] for t in diag["tags"])
        log = c.get("/api/log").json()["entries"]
        assert any("TP-Link" in e["summary"] for e in log)
        assert all("61646d696e00" not in e["hex"] for e in log)  # "admin\0" never logged
        r = c.post(f"/api/devices/{MAC_A}/login", json={"password": "bad", "username": "admin"})
        assert r.status_code == 409 and r.json()["code"] == "switch_auth"
        r = c.put(f"/api/devices/{MAC_A}/igmp", json={"enabled": True, "report_suppression": True})
        assert r.status_code == 200, r.text  # the stored password still works
        # backup and restore go through the same driver
        backup = c.get(f"/api/devices/{MAC_A}/backup").json()
        assert backup["config"]["igmp"]["report_suppression"] is True
        r = c.post(f"/api/devices/{MAC_A}/restore", json={"backup": backup})
        assert r.status_code == 200, r.text
        assert all(s["ok"] for s in r.json()["steps"]), r.json()
        # the monitor keeps packet rates for switches that count packets only
        m = c.app.state.manager
        for _ in range(2):
            c.portal.call(m.monitor.sample_all)
            time.sleep(0.2)
        hist = c.get(f"/api/devices/{MAC_A}/history").json()
        assert hist["unit"] == "packets/s" and hist["series"]["1"]
        assert m.monitor.rates(MAC_A) == {}  # no bit rates
        assert m.monitor.counter(MAC_A, 1, "rx_packets") > 0
        metrics = m.monitor.metrics()
        assert "switchyard_port_received_packets_total" in metrics
        assert f'switchyard_port_received_bytes_total{{mac="{MAC_A}"' not in metrics


def test_demo_has_both_vendors(tmp_path):
    env = {
        "SWITCHYARD_DEMO_ENABLED": "true",
        "SWITCHYARD_AUTH_PASSWORD": PASSWORD,
        "SWITCHYARD_NSDP_POLL_SECONDS": "0",
        "SWITCHYARD_MONITOR_INTERVAL_SECONDS": "0",
        "SWITCHYARD_NSDP_DISCOVERY_SECONDS": "0.3",
    }
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        c.post("/api/login", json={"password": PASSWORD})
        found = c.post("/api/discovery/scan").json()["found"]
        assert any(f["protocol"] == "easysmart" for f in found)


def test_sim_rules():
    s = SimEasySmart("50:c7:bf:5e:00:09")
    assert s._set([(T.DESCRIPTION, T.cstr("x"))]) == 3  # system settings only together
    assert s._set_one(T.TRUNK, T.pack_group_mask(1, [1])) == 7415
    assert s._set_one(T.TRUNK, T.pack_group_mask(9, [1, 2])) == 7503
    assert s._set_one(0x7777, b"") == 7503
    assert s._set_one(T.PORT_VLAN, T.pack_group_mask(9, [1])) == 7503
    s._set_one(T.TRUNK, T.pack_group_mask(1, [1, 2]))
    assert s._set_one(T.PORT_SETTINGS, T.pack_port_settings(1, True, 2, False)) == 7407
    s._set_one(T.DOT1Q_ENABLED, b"\x01")
    for vid in range(2, 33):
        assert s._set_one(T.DOT1Q_VLAN, T.pack_dot1q_vlan(vid, [1], [])) == 0
    assert s._set_one(T.DOT1Q_VLAN, T.pack_dot1q_vlan(40, [1], [])) == 7505
    assert s._set_one(T.DOT1Q_VLAN, T.pack_dot1q_vlan(99, [], [])) == 7503
    assert s._get(0x7777) is None and s._get(T.MTU_VLAN)


async def test_transport_on_interfaces():
    from switchyard.nsdp.netif import Interface

    t = EasySmartTransport(
        client_port=0,
        enumerate_interfaces=lambda: [
            Interface("lo", "127.0.0.1", b"\x02\x00\x00\x00\x00\x07", "255.0.0.0"),
            Interface("docker0", "127.0.0.2", b"\x02" * 6),
        ],
        timeout=0.05,
        retries=0,
    )
    await t.start()
    try:
        assert [r.name for r in t.routes] == ["lo"]
        assert await t.collect(Op.DISCOVERY, [], 0.05) == []
        t._received(b"junk", ("127.0.0.1", 1), "lo")  # ignored
        stranger = Packet(Op.REPLY, b"\x01" * 6, b"\x09" * 6, 1).encode()
        t._received(stranger, ("127.0.0.1", 1), "lo")  # for another manager: ignored
    finally:
        await t.stop()
    t2 = EasySmartTransport(client_port=0, enumerate_interfaces=lambda: [])
    await t2.start()
    assert [r.name for r in t2.routes] == ["default"]
    await t2.stop()
    with pytest.raises(RuntimeError):
        t2._send(Packet(Op.GET, b"\x01" * 6, b"\x00" * 6, 1), [])


def test_driver_defaults_are_unsupported():
    from switchyard.drivers.base import SwitchDriver

    class Bare(SwitchDriver):
        async def login(self, password, username=None): ...
        async def read_info(self): ...
        async def read_ports(self): ...
        async def read_state(self): ...

    d = Bare()
    calls = [
        d.scan_tags([1]), d.set_network(), d.set_vlan_mode("x"), d.set_port_vlans([]),
        d.apply_dot1q([], {}), d.set_qos(), d.set_mirror(0, []), d.set_igmp(),
        d.set_loop_detection(True), d.apply_lags([]), d.set_port_admin({}), d.set_poe({}),
        d.reset_stats(), d.cable_test(1), d.reboot(), d.factory_reset(),
        d.change_password("x"), d.upgrade_firmware(b""),
    ]  # fmt: skip
    for call in calls:
        with pytest.raises(Unsupported):
            asyncio.run(call)
