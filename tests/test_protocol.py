import pytest

from switchyard.nsdp import auth
from switchyard.nsdp import tags as T
from switchyard.nsdp.packet import Op, Packet, PacketError, format_mac, parse_mac


def test_header_layout_is_byte_exact():
    p = Packet(
        Op.READ_REQUEST, parse_mac("aa:bb:cc:dd:ee:ff"), parse_mac("11:22:33:44:55:66"), 0x0102
    )
    p.tlvs = [(T.MODEL, b"")]
    raw = p.encode()
    expected = (
        "01"
        "01"
        "00"
        "00"
        "0000"
        "0000"  # version, op, status, pad, failed tag, pad
        "aabbccddeeff"
        "112233445566"
        "0000"
        "0102"
        "4e534450"
        "00000000"
        "00010000"
        "ffff0000"
    )
    assert raw.hex() == expected
    assert len(raw) == 32 + 4 + 4


def test_round_trip_with_values_and_status():
    p = Packet(
        Op.WRITE_RESPONSE, bytes(6), bytes(range(6)), 7, 0x07, 0x000A, [(1, b"GS108E"), (3, b"x")]
    )
    q = Packet.decode(p.encode())
    assert (q.op, q.seq, q.status, q.failed_tag) == (Op.WRITE_RESPONSE, 7, 7, 0x000A)
    assert q.tlvs == p.tlvs
    assert q.first(1) == b"GS108E" and q.has(3) and not q.has(9) and q.first(9) is None


def test_ffff_length_means_empty():
    raw = (
        Packet(Op.READ_RESPONSE, tlvs=[]).encode()[:-4]
        + bytes.fromhex("0001ffff")
        + bytes.fromhex("ffff0000")
    )
    assert Packet.decode(raw).tlvs == [(1, b"")]


@pytest.mark.parametrize("data", [b"", b"\x01" * 10, bytes(40)])
def test_garbage_is_rejected(data):
    with pytest.raises(PacketError):
        Packet.decode(data)


def test_truncated_tlv_and_bad_version():
    good = Packet(Op.READ_RESPONSE, tlvs=[(1, b"abcdef")]).encode()
    with pytest.raises(PacketError):
        Packet.decode(good[:38])
    bad = bytearray(good)
    bad[0] = 2
    with pytest.raises(PacketError):
        Packet.decode(bytes(bad))
    with pytest.raises(PacketError):
        Packet(1, host_mac=b"123").encode()


def test_mac_helpers():
    assert format_mac(parse_mac("AA-BB-CC-DD-EE-FF")) == "aa:bb:cc:dd:ee:ff"
    assert parse_mac("aabb.ccdd.eeff") == bytes.fromhex("aabbccddeeff")
    for bad in ("zz:zz:zz:zz:zz:zz", "aa:bb"):
        with pytest.raises(ValueError):
            parse_mac(bad)


def test_password_modes_from_flags():
    assert auth.mode_from_flags(None) is auth.PasswordMode.PLAIN
    assert auth.mode_from_flags(b"\0\0\0\1") is auth.PasswordMode.XOR
    assert auth.mode_from_flags(b"\0\0\0\x08") is auth.PasswordMode.HASH4
    assert auth.mode_from_flags(b"\0\0\0\x10") is auth.PasswordMode.HASH8
    assert auth.mode_from_flags(b"\0\0\0\x18") is auth.PasswordMode.HASH8
    assert auth.mode_from_flags(b"\0\0\0\0") is auth.PasswordMode.PLAIN
    for mode in auth.PasswordMode:
        assert auth.mode_from_flags(auth.flags_for_mode(mode)) is mode


def test_xor_password_known_answer():
    assert auth.xor_password(b"password") == bytes(
        a ^ b for a, b in zip(b"password", b"NtgrSmartSwitchRock", strict=False)
    )
    assert (
        auth.xor_password(auth.xor_password(b"a long password with wrap!"))
        == b"a long password with wrap!"
    )


def test_hash_known_answers():
    mac = bytes.fromhex("001122334455")
    salt = bytes.fromhex("0a0b0c0d")
    k = b"password".ljust(20, b"\0")
    h8 = auth.hash8(b"password", mac, salt)
    assert len(h8) == 8
    assert h8[0] == salt[3] ^ salt[2] ^ mac[1] ^ mac[5] ^ k[0] ^ k[1] ^ k[2]
    assert h8[7] == salt[0] ^ salt[1] ^ mac[4] ^ mac[5] ^ k[1] ^ k[3] ^ k[5]
    h4 = auth.hash4(b"password", mac, salt)
    assert h4[0] == mac[1] ^ salt[3] ^ salt[2] ^ k[13] ^ k[9] ^ mac[5] ^ k[7] ^ k[1]
    assert len(h4) == 4
    assert auth.hash8(b"x", mac, salt) != auth.hash8(b"y", mac, salt)


def test_credential_tlvs():
    mac, salt = bytes(6), bytes(4)
    assert auth.credential_tlv(auth.PasswordMode.PLAIN, "pw", mac, None) == (0x0A, b"pw")
    assert auth.credential_tlv(auth.PasswordMode.XOR, "pw", mac, None)[0] == 0x0A
    assert auth.credential_tlv(auth.PasswordMode.HASH4, "pw", mac, salt)[0] == 0x0A
    assert auth.credential_tlv(auth.PasswordMode.HASH8, "pw", mac, salt)[0] == 0x1A
    assert auth.credential_tlv(auth.PasswordMode.HASH8, "pw", mac, None)[0] == 0x1A
    assert auth.new_password_tlv(auth.PasswordMode.PLAIN, "pw") == (0x09, b"pw")
    assert auth.new_password_tlv(auth.PasswordMode.HASH8, "pw") == (0x09, auth.xor_password(b"pw"))
    assert not auth.PasswordMode.XOR.needs_salt and auth.PasswordMode.HASH8.needs_salt


@pytest.mark.parametrize("ports", [5, 8, 16, 24, 48])
def test_bitmaps(ports):
    members = [1, 2, ports]
    bm = T.bitmap_from_ports(members, ports)
    assert len(bm) == (ports + 7) // 8
    assert bm[0] & 0x80 and bm[0] & 0x40  # MSB first
    assert T.ports_from_bitmap(bm, ports) == sorted(members)


def test_bitmap_range_errors():
    with pytest.raises(ValueError):
        T.bitmap_from_ports([9], 8)
    with pytest.raises(ValueError):
        T.bitmap_from_ports([0], 8)


def test_value_codecs_round_trip():
    assert T.ipv4(T.pack_ipv4("192.168.0.9")) == "192.168.0.9"
    assert T.port_vlan(T.pack_port_vlan(3, [1, 4], 8), 8) == {"id": 3, "members": [1, 4]}
    d = T.dot1q_vlan(T.pack_dot1q_vlan(10, [1, 2, 8], [1], 8), 8)
    assert d == {"id": 10, "members": [1, 2, 8], "tagged": [1]}
    with pytest.raises(ValueError):
        T.pack_dot1q_vlan(10, [1], [2], 8)
    assert T.port_u16(T.pack_port_u16(3, 20)) == (3, 20)
    assert T.port_rate(T.pack_port_rate(2, 7)) == (2, 7)
    assert T.mirror(T.pack_mirror(8, [1, 2], 8), 8) == {"destination": 8, "sources": [1, 2]}
    assert T.pack_mirror(0, [1], 8) == bytes(3)
    assert T.igmp(T.pack_igmp(True, 5)) == {"enabled": True, "vlan": 5}
    assert T.igmp(T.pack_igmp(False, 5)) == {"enabled": False, "vlan": 0}
    assert T.cable_result(T.pack_cable_result(2, 2, 9)) == {
        "port": 2,
        "status_code": 2,
        "status": "open",
        "distance_m": 9,
    }
    s = T.port_stats(T.pack_port_stats(1, [10, 20, 0, 0, 0, 3]))
    assert (s["rx_bytes"], s["tx_bytes"], s["crc_errors"]) == (10, 20, 3)
    assert T.port_stats(T.pack_port_stats(1, [10, 20, 3]), crc_index=2)["crc_errors"] == 3
    assert T.port_status(bytes([1, 5, 0]))["speed_mbps"] == 1000
    assert T.port_status(bytes([1, 99]))["link"] == "code 99"
    assert T.text(b"abc\0def") == "abc"
    assert T.flag(b"\x01") and not T.flag(b"\x00") and T.pack_flag(True) == b"\x01"
