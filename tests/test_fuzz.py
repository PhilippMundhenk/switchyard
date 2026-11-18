"""Hostile firmware: every setting answered with random bytes of random length."""

import random

import pytest

from switchyard.nsdp import NsdpError, SwitchClient, Transport, Unreachable, parse_mac
from switchyard.nsdp.packet import Packet, PacketError
from switchyard.sim import Simulator, SimSwitch

ALLOWED = (Unreachable,)  # decoding problems must be absorbed, never raised


def test_packet_decoder_only_raises_packet_error():
    rng = random.Random(1)
    good = Packet(2, tlvs=[(1, b"abc"), (6, b"\x01\x02\x03\x04")]).encode()
    for _ in range(3000):
        data = bytearray(
            good
            if rng.random() < 0.7
            else bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
        )
        for _ in range(rng.randrange(0, 6)):
            if data:
                data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.3:
            data = data[: rng.randrange(0, len(data) + 1)]
        try:
            Packet.decode(bytes(data))
        except PacketError:
            pass


@pytest.mark.parametrize("seed", range(1, 41))
async def test_client_survives_garbled_replies(seed):
    switch = SimSwitch("02:00:00:00:00:99", garble=seed, ports=8)
    sim = Simulator([switch])
    await sim.start()
    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"], timeout=0.3, retries=0)
    await t.start()
    try:
        c = SwitchClient(t, parse_mac(switch.mac), None, "password")
        for read in (
            c.read_info,
            c.read_ports,
            c.read_vlan,
            c.read_qos,
            c.read_features,
            c.read_lag,
            c.read_port_admin,
        ):
            try:
                await read()
            except ALLOWED:
                pass
        try:
            state = await c.read_state()
            assert isinstance(state["ports"], list)
        except ALLOWED:
            pass
    finally:
        await t.stop()
        sim.stop()
