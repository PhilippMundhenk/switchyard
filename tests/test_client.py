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
