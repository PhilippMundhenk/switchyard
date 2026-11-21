import asyncio

import pytest

from switchyard.nsdp import (
    AuthError,
    NsdpError,
    PasswordRequired,
    SwitchClient,
    Transport,
    Unreachable,
    discover,
    parse_mac,
    probe,
)
from switchyard.nsdp import tags as T
from switchyard.nsdp.auth import PasswordMode
from switchyard.nsdp.client import _ports_from_model, validate_dot1q
from switchyard.nsdp.packet import Op, Packet
from switchyard.sim import SimSwitch, Simulator

MAC = "00:26:f2:aa:10:01"


def client_for(transport, mac=MAC, password="password", address=None):
    return SwitchClient(transport, parse_mac(mac), address, password)


async def test_discovery_finds_all_switches(transport):
    found = await discover(transport, 0.4)
    assert {d["model"] for d in found} == {"GS108E", "GS108Ev3", "GS105E", "JGS516PE"}
    assert all(d["source_ip"] == "127.0.0.1" for d in found)


async def test_probe_by_address(transport):
    ident = await probe(transport, "127.0.0.1", 0.5)
    assert ident is not None and ident["mac"]


async def test_probe_times_out_when_nobody_answers():
    t = Transport(client_port=0, targets=["127.0.0.1:9"])
    await t.start()
    try:
        assert await probe(t, "127.0.0.1", 0.1) is None
        assert not t._waiters
    finally:
        await t.stop()


@pytest.mark.parametrize("mode", list(PasswordMode))
@pytest.mark.parametrize("unknown", ["skip", "status", "silent"])
async def test_read_and_write_in_every_mode(mode, unknown):
    switch = SimSwitch(
        "02:00:00:00:00:09",
        password_mode=mode,
        unknown_tag=unknown,
        unsupported={T.LOOP_DETECTION},
        reboot_seconds=0.2,
    )
    sim = Simulator([switch])
    await sim.start()
    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.15, retries=1)
    await t.start()
    try:
        c = client_for(t, switch.mac)
        state = await c.read_state()
        assert state["info"]["model"] == "GS108E"
        assert state["info"]["password_mode"] == str(mode)
        assert state["loop_detection"] is None
        assert len(state["ports"]) == 8
        await c.set_network(name="lab")
        assert switch.name == "lab"
        bad = client_for(t, switch.mac, password="wrong")
        with pytest.raises(AuthError):
            await bad.write([(T.NAME, b"x")])
        assert switch.name == "lab"
    finally:
        await t.stop()
        sim.stop()


async def test_no_password_is_reported(transport):
    c = client_for(transport, password=None)
    with pytest.raises(PasswordRequired):
        await c.set_loop_detection(True)


async def test_login_accepts_and_rejects(transport):
    c = client_for(transport, password=None)
    with pytest.raises(AuthError) as exc:
        await c.login("nope")
    assert "Incorrect password" in str(exc.value)
    await c.login("password")
    assert c.password == "password"


async def test_info_and_ports_for_other_models(transport):
    for mac, ports, model in [
        ("e0:91:f5:cc:30:03", 5, "GS105E"),
        ("28:c6:8e:dd:40:04", 16, "JGS516PE"),
    ]:
        c = client_for(transport, mac)
        info = await c.read_info()
        assert info["ports"] == ports and info["model"] == model
        assert len(await c.read_ports()) == ports


async def test_network_settings_and_validation(transport, sim):
    c = client_for(transport)
    await c.set_network(
        dhcp=False, ip="192.168.0.50", netmask="255.255.255.0", gateway="192.168.0.1"
    )
    sw = sim.by_mac(MAC)
    assert (sw.ip, sw.dhcp, sw.gateway) == ("192.168.0.50", False, "192.168.0.1")
    await c.set_network(dhcp=True)
    assert sw.dhcp is True
    with pytest.raises(ValueError):
        await c.set_network(dhcp=False, ip="192.168.0.50")
    with pytest.raises(ValueError):
        await c.set_network(
            dhcp=False, ip="192.168.0.50", netmask="255.255.255.0", gateway="10.0.0.1"
        )


async def test_vlan_modes_and_port_based(transport, sim):
    c = client_for(transport)
    await c.set_vlan_mode("port_advanced")
    await c.set_port_vlans([{"id": 1, "members": [1, 2]}, {"id": 2, "members": [3, 4]}])
    vlan = await c.read_vlan()
    assert vlan["mode"] == "port_advanced"
    assert {v["id"]: v["members"] for v in vlan["port_vlans"]} == {1: [1, 2], 2: [3, 4]}
    await c.set_vlan_mode("disabled")
    assert (await c.read_vlan())["mode"] == "disabled"


async def test_apply_dot1q_orders_writes_safely(transport, sim):
    c = client_for(transport)
    await c.set_vlan_mode("dot1q_advanced")
    # move ports 5-8 from VLAN 1 to VLAN 20 including their PVID: a naive order is refused
    await c.apply_dot1q(
        [
            {"id": 1, "members": [1, 2, 3, 4], "tagged": []},
            {"id": 20, "members": [1, 5, 6, 7, 8], "tagged": [1]},
        ],
        {5: 20, 6: 20, 7: 20, 8: 20},
    )
    v = await c.read_vlan()
    assert {x["id"]: (x["members"], x["tagged"]) for x in v["dot1q"]} == {
        1: ([1, 2, 3, 4], []),
        20: ([1, 5, 6, 7, 8], [1]),
    }
    assert v["pvid"][5] == 20 and v["pvid"][1] == 1
    # and back, deleting the leftover VLAN
    await c.apply_dot1q(
        [{"id": 1, "members": list(range(1, 9)), "tagged": []}], dict.fromkeys(range(1, 9), 1)
    )
    assert [x["id"] for x in (await c.read_vlan())["dot1q"]] == [1]


async def test_apply_dot1q_validation(transport):
    c = client_for(transport)
    with pytest.raises(ValueError):  # not in an 802.1Q mode
        await c.apply_dot1q([], {})
    await c.set_vlan_mode("dot1q_advanced")
    full = {"id": 1, "members": list(range(1, 9)), "tagged": []}
    for vlans, pvid in [
        ([full, {"id": 5000, "members": [1], "tagged": []}], {}),
        ([full, {"id": 9, "members": [99], "tagged": []}], {}),
        ([full, {"id": 9, "members": [1], "tagged": [2]}], {}),
        ([full], {1: 77}),
        ([{"id": 1, "members": [2, 3], "tagged": []}], {1: 1}),
    ]:
        with pytest.raises(ValueError):
            await c.apply_dot1q(vlans, pvid)
    with pytest.raises(ValueError):
        validate_dot1q({i: {"members": [1], "tagged": []} for i in range(1, 5)}, {}, 8, 3)


async def test_delete_vlan_and_pvid_refusal(transport):
    c = client_for(transport)
    await c.set_vlan_mode("dot1q_advanced")
    await c.set_dot1q_vlan(30, [1, 2], [])
    await c.set_pvid({2: 30})
    with pytest.raises(NsdpError):
        await c.delete_vlan(30)  # still a PVID
    await c.set_pvid({2: 1})
    await c.delete_vlan(30)


async def test_qos_rates_storm_mirror_igmp_loop(transport, sim):
    c = client_for(transport)
    sw = sim.by_mac(MAC)
    await c.set_qos(
        mode="port",
        priorities={1: "high", 2: "low"},
        ingress={1: 5},
        egress={2: 8},
        storm_control=True,
        storm_rates={3: 4},
    )
    q = await c.read_qos()
    assert q["mode"] == "port" and q["priorities"][1] == "high" and q["priorities"][2] == "low"
    assert q["ingress"][1] == 5 and q["egress"][2] == 8 and q["storm_control"] is True
    assert q["storm_rates"][3] == 4
    await c.set_qos(storm_control=False, storm_rates={3: 1})
    assert sw.storm_rates[3] == 4  # ignored while storm control is off
    await c.set_mirror(8, [1, 2])
    assert (await c.read_features())["mirror"] == {"destination": 8, "sources": [1, 2]}
    for dest, src in [(1, [1]), (2, [])]:
        with pytest.raises(ValueError):
            await c.set_mirror(dest, src)
    await c.set_mirror(0, [])
    assert (await c.read_features())["mirror"]["destination"] == 0
    await c.set_igmp(True, 10, True, True)
    g = (await c.read_features())["igmp"]
    assert g == {"enabled": True, "vlan": 10, "block_unknown": True, "validate_v3": True}
    await c.set_igmp(False)
    await c.set_loop_detection(True)
    assert sw.loop_detection is True


async def test_stats_and_cable_test(transport, sim):
    c = client_for(transport)
    sw = sim.by_mac(MAC)
    sw.counters[1][0] = 5
    await c.reset_stats()
    assert sw.counters[1][0] == 0
    assert (await c.cable_test(3))["status"] == "open"  # link down in the demo
    assert (await c.cable_test(1))["status"] == "ok"


async def test_unsupported_setting_is_a_clean_error(transport):
    c = client_for(transport, "e0:91:f5:cc:30:03")  # GS105E: no loop detection
    with pytest.raises(NsdpError) as exc:
        await c.set_loop_detection(True)
    assert "does not support" in str(exc.value)


async def test_reboot_goes_silent_then_returns(transport, sim):
    c = client_for(transport)
    await c.reboot()
    fast = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.1, retries=0)
    await fast.start()
    try:
        quick = SwitchClient(fast, parse_mac(MAC), None, "password")
        with pytest.raises(Unreachable):
            await quick._exchange(Op.READ_REQUEST, [(T.MODEL, b"")])
    finally:
        await fast.stop()
    await asyncio.sleep(0.5)
    assert (await c.read_info())["model"] == "GS108E"


async def test_factory_reset_restores_defaults(transport, sim):
    c = client_for(transport)
    await c.set_network(name="changed")
    await c.change_password("newpass")
    await c.factory_reset()
    await asyncio.sleep(0.5)
    sw = sim.by_mac(MAC)
    assert sw.name == "office" and sw.password == "password"


async def test_change_password(transport):
    c = client_for(transport)
    await c.change_password("secret")
    assert c.password == "secret"
    await c.write([(T.NAME, b"ok")])
    old = client_for(transport, password="password")
    with pytest.raises(AuthError):
        await old.write([(T.NAME, b"no")])
    with pytest.raises(ValueError):
        await c.change_password("x" * 21)


async def test_hash_modes_change_password(transport):
    for mac in ("a0:21:b7:bb:20:02",):  # HASH8
        c = client_for(transport, mac)
        await c.change_password("abc123")
        await c.write([])  # the new password is accepted (as the XOR-coded secret)


def test_model_port_guess():
    assert _ports_from_model("GS108E") == 8
    assert _ports_from_model("GS105Ev2") == 5
    assert _ports_from_model("JGS516PE") == 16
    assert _ports_from_model("GS724T") == 24
    assert _ports_from_model("weird") == 8


async def test_silent_firmware_group_is_split(transport):
    sw = SimSwitch("02:00:00:00:00:0a", unknown_tag="silent", unsupported={T.QOS_MODE})
    sim = Simulator([sw])
    await sim.start()
    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.15, retries=1)
    await t.start()
    try:
        c = client_for(t, sw.mac)
        assert await c.read_qos() is None
        assert T.QOS_MODE in c.unsupported
    finally:
        await t.stop()
        sim.stop()


async def test_transport_ignores_noise_and_stale_replies(transport, sim):
    transport._received(b"junk", ("127.0.0.1", 1), "any")
    stale = Packet(Op.READ_RESPONSE, seq=1234).encode()
    transport._received(stale, ("127.0.0.1", 1), "any")  # nobody waits for seq 1234
    other_request = Packet(Op.READ_REQUEST, seq=5).encode()
    transport._received(other_request, ("127.0.0.1", 1), "any")
    assert any(e["summary"] == "not an NSDP packet" for e in transport.packet_log)


async def test_request_timeout_raises(transport):
    with pytest.raises(TimeoutError):
        await transport.request(
            Op.READ_REQUEST, parse_mac("02:99:99:99:99:99"), [(1, b"")], timeout=0.1, retries=0
        )
    assert not transport._waiters


async def test_packet_log_never_holds_passwords(transport):
    c = client_for(transport, password="hunter2-secret")
    sw_pass = b"hunter2-secret"
    with pytest.raises(AuthError):
        await c.write([(T.NAME, b"x")])
    blob = b"".join(bytes.fromhex(e["hex"]) for e in transport.packet_log)
    from switchyard.nsdp.auth import xor_password

    assert sw_pass not in blob and xor_password(sw_pass) not in blob


async def test_interface_routes_with_fake_enumeration(sim):
    from switchyard.nsdp.netif import Interface

    ifaces = [
        Interface("fake0", "127.0.0.1", bytes.fromhex("020000000001")),
        Interface("skipme", "127.0.0.2", bytes(6)),
    ]
    t = Transport(client_port=0, interfaces=["fake0"], enumerate_interfaces=lambda: ifaces)
    await t.start()
    try:
        assert [r.name for r in t.routes] == ["fake0"]
    finally:
        await t.stop()
    empty = Transport(client_port=0, enumerate_interfaces=lambda: [])
    await empty.start()
    try:
        assert [r.name for r in empty.routes] == ["default"]
    finally:
        await empty.stop()


def test_list_interfaces_runs():
    from switchyard.nsdp.netif import list_interfaces, read_mac

    assert isinstance(list_interfaces(), list)
    assert read_mac("does-not-exist") == bytes(6)


# ----------------------------------------------------------------- LAG, speed, firmware
async def test_lag_read_and_set(transport, sim):
    c = client_for(transport)
    lag = await c.read_lag()
    assert (
        lag["groups"] == 4
        and lag["max_members"] == 4
        and not any(x["enabled"] for x in lag["lags"])
    )
    await c.set_lag(1, True, [1, 2])
    await c.set_lag(2, True, [3, 4, 5])
    got = {x["id"]: x for x in (await c.read_lag())["lags"]}
    assert got[1] == {"id": 1, "enabled": True, "members": [1, 2]}
    assert got[2]["members"] == [3, 4, 5]
    await c.set_lag(1, False, [])
    assert sim.by_mac(MAC).lags[1] == (False, [])


async def test_lag_validation(transport, sim):
    c = client_for(transport)
    for group, enabled, members in [(1, True, [1]), (1, True, [1, 2, 3, 4, 5]), (9, True, [1, 2])]:
        with pytest.raises(ValueError):
            await c.set_lag(group, enabled, members)
    await c.set_lag(1, True, [1, 2])
    with pytest.raises(ValueError, match="already belongs"):
        await c.set_lag(2, True, [2, 3])
    # the switch enforces the same speed on all members: the client does not pre-check this
    await c.set_port_admin({3: {"speed_code": 5, "flow_control": False}})
    with pytest.raises(NsdpError):
        await c.set_lag(2, True, [3, 4])
    c5 = client_for(transport, "e0:91:f5:cc:30:03")
    assert (await c5.read_lag())["groups"] == 2
    xs = SimSwitch("02:00:00:00:00:77", model="XS708E")
    s2 = Simulator([xs])
    await s2.start()
    t = Transport(client_port=0, targets=[f"127.0.0.1:{s2.port}"], timeout=0.3, retries=1)
    await t.start()
    try:
        cx = client_for(t, xs.mac)
        await cx.set_lag(1, True, [1, 2, 3, 4, 5, 6, 7, 8])
        assert (await cx.read_lag())["max_members"] == 8
    finally:
        await t.stop()
        s2.stop()


async def test_lag_unsupported_switch_returns_none(transport):
    sw = SimSwitch("02:00:00:00:00:0b", unsupported={T.LAG, T.LAG_COUNT})
    sim = Simulator([sw])
    await sim.start()
    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.3, retries=1)
    await t.start()
    try:
        c = client_for(t, sw.mac)
        assert await c.read_lag() is None
        with pytest.raises(ValueError, match="does not support"):
            await c.set_lag(1, True, [1, 2])
    finally:
        await t.stop()
        sim.stop()


async def test_apply_lags_changes_only_what_differs(transport, sim):
    c = client_for(transport)
    await c.set_lag(1, True, [1, 2])
    await c.apply_lags(
        [
            {"id": 1, "enabled": True, "members": [1, 2, 3]},
            {"id": 2, "enabled": True, "members": [5, 6]},
        ]
    )
    lags = {x["id"]: x["members"] for x in (await c.read_lag())["lags"] if x["enabled"]}
    assert lags == {1: [1, 2, 3], 2: [5, 6]}
    await c.apply_lags([])
    assert not any(x["enabled"] for x in (await c.read_lag())["lags"])


async def test_port_admin_speed_and_flow(transport, sim):
    c = client_for(transport)
    admin = await c.read_port_admin()
    assert admin[1] == {"port": 1, "speed_code": 0, "speed": "auto", "flow_control": False}
    await c.set_port_admin(
        {1: {"speed_code": 5, "flow_control": True}, 2: {"speed_code": 1, "flow_control": False}}
    )
    admin = await c.read_port_admin()
    assert admin[1]["speed"] == "100M full" and admin[1]["flow_control"] is True
    assert admin[2]["speed"] == "disabled"
    ports = {p["port"]: p for p in await c.read_ports()}
    assert ports[1]["speed_mbps"] == 100 and ports[2]["up"] is False  # settings take effect
    for bad in ({9: {"speed_code": 0}}, {1: {"speed_code": 42}}):
        with pytest.raises(ValueError):
            await c.set_port_admin(bad)
    await c.set_lag(1, True, [3, 4])
    with pytest.raises(NsdpError):
        await c.set_port_admin({3: {"speed_code": 1}})  # cannot disable a LAG member
    gs105 = client_for(transport, "e0:91:f5:cc:30:03")
    assert await gs105.read_port_admin() is None  # not supported on this model


def make_image(board="GS108E", version="100C", size=5000):
    from switchyard.nsdp.firmware import build_image

    return build_image(board, version, bytes(i % 251 for i in range(size)))


def test_firmware_image_checks():
    from switchyard.nsdp import firmware as fw

    img = make_image()
    parsed = fw.parse(img)
    assert (parsed.board, parsed.version, parsed.version_raw) == ("GS108E", "1.00.12", "100C")
    assert fw.matches_model("GS108E", "GS108Ev3") and not fw.matches_model("GS105E", "GS108E")
    assert not fw.matches_model("", "GS108E")
    bad = {
        "small": img[:100],
        "magic": b"XXXX" + img[4:],
        "length": img[:-10],
        "checksum": img[:-1] + bytes([img[-1] ^ 1]),
        "header": img[:4] + b"ZZZZZZZZ" + img[12:],
    }
    for data in bad.values():
        with pytest.raises(fw.FirmwareError):
            fw.parse(data)
    assert fw.guess_version("zz") == "zz"


async def test_firmware_upgrade_end_to_end(transport, sim):
    sw = sim.by_mac(MAC)
    sw.tftp_drop_blocks = {3}  # a lost ACK must be survived by retransmission
    transport.tftp_port = sim.tftp_port
    transport.tftp_timeout = 0.3
    c = client_for(transport)
    seen = []
    info = await c.upgrade_firmware(
        make_image(version="100D"),
        progress=lambda step, a, b: seen.append((step, a, b)),
        settle=0.05,
    )
    assert info.version == "1.00.13"
    assert sw.firmware == "1.00.13" and sw.upgrades == 1 and not sw.upgrade_mode
    assert seen[0][0] == "entering upgrade mode" and seen[-1][1] == seen[-1][2]
    await asyncio.sleep(0.5)  # reboot
    assert (await c.read_info())["firmware"] == "1.00.13"


async def test_firmware_upgrade_refusals(transport, sim):
    from switchyard.nsdp import firmware as fw

    transport.tftp_port = sim.tftp_port
    transport.tftp_timeout = 0.3
    c = client_for(transport)
    with pytest.raises(fw.FirmwareError, match="for GS105E"):
        await c.upgrade_firmware(make_image(board="GS105E"), settle=0.01)
    assert not sim.by_mac(MAC).upgrade_mode  # nothing was switched
    with pytest.raises(fw.FirmwareError):
        await c.upgrade_firmware(b"garbage" * 1000, settle=0.01)
    bad_password = client_for(transport, password="wrong")
    with pytest.raises(AuthError):
        await bad_password.upgrade_firmware(make_image(), settle=0.01)
    assert not sim.by_mac(MAC).upgrade_mode
    # forced wrong board: the switch itself refuses it at the end and keeps its firmware
    before = sim.by_mac(MAC).firmware
    with pytest.raises(NsdpError, match="Firmware upload failed"):
        await c.upgrade_firmware(make_image(board="GS105E"), force=True, settle=0.01)
    assert sim.by_mac(MAC).firmware == before


async def test_firmware_upload_fails_cleanly_when_tftp_is_down(transport, sim):
    transport.tftp_port = 9  # nobody listens
    transport.tftp_timeout = 0.05
    c = client_for(transport)
    with pytest.raises(NsdpError, match="stopped answering"):
        await c.upgrade_firmware(make_image(), settle=0.01)


def test_tftp_target_needs_an_address():
    t = Transport(client_port=0)
    assert t.tftp_target("192.168.0.9") == ("192.168.0.9", 69)
    with pytest.raises(ValueError):
        t.tftp_target(None)


async def test_probe_ignores_answers_from_other_hosts(sim):
    """Only the probed address may answer, so nobody else can slip in a fake switch."""
    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.3, retries=0)
    await t.start()
    try:
        t.targets = []  # behave like the real network: replies must come from the probed host
        t._listener_send = t._listener.sendto
        sent = []
        t._listener.sendto = lambda raw, dest: (
            sent.append(dest) or t._listener_send(raw, ("127.0.0.1", sim.port))
        )
        assert await probe(t, "192.0.2.10", 0.2) is None  # the answer came from 127.0.0.1
        assert sent and sent[0] == ("192.0.2.10", t.device_port)
        assert await probe(t, "127.0.0.1", 0.3) is not None
    finally:
        await t.stop()


async def test_unicast_uses_the_wildcard_socket(sim):
    from switchyard.nsdp.netif import Interface

    iface = Interface("fake0", "127.0.0.1", bytes.fromhex("020000000001"))
    t = Transport(client_port=0, enumerate_interfaces=lambda: [iface])
    await t.start()
    try:
        via_route, via_listener = [], []
        t.routes[0].transport.sendto = lambda raw, dest: via_route.append(dest)
        t._listener.sendto = lambda raw, dest: via_listener.append(dest)
        t._send(Packet(Op.READ_REQUEST, seq=1), "203.0.113.7")
        assert via_listener == [("203.0.113.7", t.device_port)] and via_route == []
        t._send(Packet(Op.READ_REQUEST, seq=2), None)  # broadcast still per interface
        assert via_route == [("255.255.255.255", t.device_port)]
        assert t.packet_log[0]["peer"] == f"203.0.113.7:{t.device_port}"
    finally:
        await t.stop()


async def test_firmware_that_answers_empty_when_a_tag_is_unknown():
    """GS108E v1 (1.00.09): one unknown tag in a read gives an empty, successful reply."""
    sw = SimSwitch(
        "02:00:00:00:00:a1",
        model="GS108E",
        unknown_tag="empty",
        unsupported={T.LOOP_DETECTION, T.SERIAL, T.LAG, T.LAG_COUNT, T.PORT_ADMIN},
    )
    sim = Simulator([sw])
    await sim.start()
    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.2, retries=0)
    await t.start()
    try:
        c = client_for(t, sw.mac)
        info = await c.read_info()
        assert info["model"] == "GS108E" and info["firmware"] == "1.00.12" and info["ports"] == 8
        features = await c.read_features()
        assert features["mirror"] == {"destination": 0, "sources": []}
        assert features["igmp"]["enabled"] is False and features["loop_detection"] is None
        assert {T.LOOP_DETECTION, T.SERIAL} <= c.unsupported
        before = sim.requests
        await c.read_features()  # known gaps are skipped: one request, no splitting
        assert sim.requests - before == 1
        state = await c.read_state()
        assert state["lag"] is None and state["port_admin"] is None
        assert state["vlan"]["mode"] == "disabled" and state["qos"]["mode"] == "dot1p"
    finally:
        await t.stop()
        sim.stop()


@pytest.mark.parametrize("unknown", ["skip", "status", "silent", "empty"])
async def test_every_unknown_tag_behaviour_reads_the_same_state(unknown):
    sw = SimSwitch(
        "02:00:00:00:00:a2", unknown_tag=unknown, unsupported={T.LOOP_DETECTION, T.SERIAL}
    )
    sim = Simulator([sw])
    await sim.start()
    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.15, retries=0)
    await t.start()
    try:
        state = await client_for(t, sw.mac).read_state()
        assert state["info"]["model"] == "GS108E" and state["info"]["serial"] is None
        assert state["mirror"] is not None and state["igmp"] is not None
        assert state["loop_detection"] is None and len(state["ports"]) == 8
    finally:
        await t.stop()
        sim.stop()


def test_virtual_interface_names():
    from switchyard.nsdp.netif import is_virtual

    for name in (
        "docker0",
        "br-e6d349daa005",
        "veth12ab",
        "virbr0",
        "cni0",
        "flannel.1",
        "podman1",
    ):
        assert is_virtual(name), name
    for name in ("eth0", "ens18", "enp3s0", "wlan0", "bond0", "br0", "lo"):
        assert not is_virtual(name), name


async def test_virtual_interfaces_are_skipped_unless_listed():
    from switchyard.nsdp.netif import Interface

    ifaces = [
        Interface("ens18", "127.0.0.1", bytes(6), "255.0.0.0"),
        Interface("docker0", "127.0.0.2", bytes(6), "255.255.0.0"),
        Interface("br-e6d349daa005", "127.0.0.3", bytes(6), "255.255.0.0"),
    ]
    t = Transport(client_port=0, enumerate_interfaces=lambda: ifaces)
    await t.start()
    try:
        assert [r.name for r in t.routes] == ["ens18"]
        assert str(t.routes[0].network) == "127.0.0.0/8"
    finally:
        await t.stop()
    t = Transport(client_port=0, interfaces=["docker0"], enumerate_interfaces=lambda: ifaces)
    await t.start()
    try:
        assert [r.name for r in t.routes] == ["docker0"]  # explicitly listed: used
    finally:
        await t.stop()


async def test_requests_go_to_the_interface_the_switch_answered_on():
    from switchyard.nsdp.netif import Interface

    ifaces = [
        Interface("lan", "127.0.0.1", bytes(6), "255.255.255.0"),
        Interface("iot", "127.0.1.1", bytes(6), "255.255.255.0"),
    ]
    t = Transport(client_port=0, enumerate_interfaces=lambda: ifaces, timeout=0.05, retries=1)
    await t.start()
    try:
        sent: dict[str, int] = {"lan": 0, "iot": 0}
        for route in t.routes:
            route.transport.sendto = lambda raw, dest, n=route.name: sent.__setitem__(
                n, sent[n] + 1
            )
        mac = parse_mac(MAC)
        t._send(Packet(Op.READ_REQUEST, device_mac=mac, seq=1), None)
        assert sent == {"lan": 1, "iot": 1}  # unknown switch: every interface
        reply = Packet(Op.READ_RESPONSE, device_mac=mac, seq=77).encode()
        t._waiters[77] = lambda p, a: None
        t._received(reply, ("127.0.1.20", 63322), "any")  # answers from the iot subnet
        assert t.home[mac] == "iot"
        t._send(Packet(Op.READ_REQUEST, device_mac=mac, seq=2), None)
        assert sent == {"lan": 1, "iot": 2}  # only where it lives
        with pytest.raises(TimeoutError):  # no answer: the retry goes out everywhere again
            await t.request(Op.READ_REQUEST, mac, [(1, b"")])
        assert sent == {"lan": 2, "iot": 4}
        t._received(
            Packet(Op.READ_RESPONSE, device_mac=bytes(6), seq=77).encode(), ("bogus", 1), "any"
        )
        t._received(
            Packet(Op.READ_RESPONSE, device_mac=bytes(6), seq=77).encode(), ("10.9.9.9", 1), "any"
        )
        assert bytes(6) not in t.home  # outside every subnet: nothing learned
    finally:
        del t._waiters[77]
        await t.stop()
