"""TLV tag numbers and the value layouts behind them.

Ports are numbered from 1. Port bitmaps are MSB-first (port 1 = 0x80 of the
first byte) and ``ceil(ports / 8)`` bytes long.
"""

from __future__ import annotations

import ipaddress
import struct

MODEL = 0x0001
NAME = 0x0003
MAC = 0x0004
LOCATION = 0x0005
IP = 0x0006
NETMASK = 0x0007
GATEWAY = 0x0008
NEW_PASSWORD = 0x0009
PASSWORD = 0x000A
DHCP = 0x000B
FIRMWARE = 0x000D
FIRMWARE2 = 0x000E
FIRMWARE_UPGRADE = 0x0010
REBOOT = 0x0013
PASSWORD_MODE = 0x0014
PASSWORD_SALT = 0x0017
AUTH_V2 = 0x001A
FACTORY_RESET = 0x0400
PORT_STATUS = 0x0C00
PORT_STATS = 0x1000
STATS_RESET = 0x1400
CABLE_TEST = 0x1800
CABLE_RESULT = 0x1C00
VLAN_MODE = 0x2000
VLAN_PORT = 0x2400
VLAN_DOT1Q = 0x2800
VLAN_DELETE = 0x2C00
PVID = 0x3000
QOS_MODE = 0x3400
QOS_PRIORITY = 0x3800
RATE_INGRESS = 0x4C00
RATE_EGRESS = 0x5000
STORM_ENABLE = 0x5400
STORM_RATE = 0x5800
MIRROR = 0x5C00
PORT_COUNT = 0x6000
MAX_VLANS = 0x6400
IGMP = 0x6800
IGMP_BLOCK_UNKNOWN = 0x6C00
IGMP_VALIDATE_V3 = 0x7000
SERIAL = 0x7800
LAG = 0x8800
LAG_COUNT = 0x8C00
LOOP_DETECTION = 0x9000
PORT_ADMIN = 0x9400

NAMES = {
    value: name.lower()
    for name, value in list(globals().items())
    if name.isupper() and isinstance(value, int)
}

VLAN_MODES = ["disabled", "port_basic", "port_advanced", "dot1q_basic", "dot1q_advanced"]
QOS_MODES = {1: "port", 2: "dot1p"}
PRIORITIES = {1: "high", 2: "medium", 3: "normal", 4: "low"}
# link code -> (label, Mbit/s, duplex)
LINK_CODES = {
    0: ("down", 0, None),
    1: ("10M half", 10, "half"),
    2: ("10M full", 10, "full"),
    3: ("100M half", 100, "half"),
    4: ("100M full", 100, "full"),
    5: ("1G full", 1000, "full"),
    6: ("10G full", 10000, "full"),
}
# rate code -> kbit/s (0 = no limit)
RATE_CODES = {
    0: 0,
    1: 512,
    2: 1024,
    3: 2048,
    4: 4096,
    5: 8192,
    6: 16384,
    7: 32768,
    8: 65536,
    9: 131072,
    10: 262144,
    11: 524288,
}
# admin speed setting code -> label (written to tag 0x0c00, read from 0x9400)
ADMIN_SPEEDS = {
    0: "auto",
    1: "disabled",
    2: "10M half",
    3: "10M full",
    4: "100M half",
    5: "100M full",
    6: "1G full",
}
CABLE_STATUS = {
    0: "ok",
    1: "no_cable",
    2: "open",
    3: "short",
    4: "fiber",
    5: "shorted",
    6: "unknown",
    7: "crosstalk",
}


def text(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def ipv4(value: bytes) -> str:
    return str(ipaddress.IPv4Address(value[:4].rjust(4, b"\0")))


def pack_ipv4(address: str) -> bytes:
    return ipaddress.IPv4Address(address).packed


def flag(value: bytes) -> bool:
    return any(value)


def pack_flag(on: bool) -> bytes:
    return b"\x01" if on else b"\x00"


def bitmap_len(ports: int) -> int:
    return max(1, (ports + 7) // 8)


def ports_from_bitmap(bitmap: bytes, ports: int | None = None) -> list[int]:
    limit = ports if ports is not None else len(bitmap) * 8
    return [
        p
        for p in range(1, min(limit, len(bitmap) * 8) + 1)
        if bitmap[(p - 1) // 8] & (0x80 >> ((p - 1) % 8))
    ]


def bitmap_from_ports(members: list[int], ports: int) -> bytes:
    out = bytearray(bitmap_len(ports))
    for p in members:
        if not 1 <= p <= ports:
            raise ValueError(f"port {p} is out of range 1-{ports}")
        out[(p - 1) // 8] |= 0x80 >> ((p - 1) % 8)
    return bytes(out)


def port_status(value: bytes) -> dict:
    port, code = value[0], value[1]
    label, mbps, duplex = LINK_CODES.get(code, (f"code {code}", 0, None))
    return {
        "port": port,
        "link_code": code,
        "link": label,
        "up": code != 0,
        "speed_mbps": mbps,
        "duplex": duplex,
        "flow_control": bool(value[2]) if len(value) > 2 else None,
    }


def port_stats(value: bytes, crc_index: int = 5) -> dict:
    """``[port][6 x u64]``: received bytes, sent bytes, then model-specific counters.

    The CRC error counter is the last one on the Gigabit models and the third
    one on the 10-Gigabit XS family.
    """
    counters = [
        int.from_bytes(value[1 + 8 * i : 9 + 8 * i], "big") for i in range((len(value) - 1) // 8)
    ]
    counters += [0] * (6 - len(counters))
    return {
        "port": value[0],
        "rx_bytes": counters[0],
        "tx_bytes": counters[1],
        "crc_errors": counters[crc_index],
        "counters": counters,
    }


def pack_port_stats(port: int, counters: list[int]) -> bytes:
    return bytes([port]) + b"".join(struct.pack(">Q", c) for c in (counters + [0] * 6)[:6])


def port_vlan(value: bytes, ports: int) -> dict:
    return {
        "id": int.from_bytes(value[:2], "big"),
        "members": ports_from_bitmap(value[2:], ports),
    }


def pack_port_vlan(vid: int, members: list[int], ports: int) -> bytes:
    return struct.pack(">H", vid) + bitmap_from_ports(members, ports)


def dot1q_vlan(value: bytes, ports: int) -> dict:
    n = (len(value) - 2) // 2
    return {
        "id": int.from_bytes(value[:2], "big"),
        "members": ports_from_bitmap(value[2 : 2 + n], ports),
        "tagged": ports_from_bitmap(value[2 + n : 2 + 2 * n], ports),
    }


def pack_dot1q_vlan(vid: int, members: list[int], tagged: list[int], ports: int) -> bytes:
    if set(tagged) - set(members):
        raise ValueError("tagged ports must also be members")
    return (
        struct.pack(">H", vid)
        + bitmap_from_ports(members, ports)
        + bitmap_from_ports(tagged, ports)
    )


def port_u16(value: bytes) -> tuple[int, int]:
    """``[port][u16]`` records (PVID)."""
    return value[0], int.from_bytes(value[1:3], "big")


def pack_port_u16(port: int, number: int) -> bytes:
    return struct.pack(">BH", port, number)


def port_byte(value: bytes) -> tuple[int, int]:
    """``[port][u8]`` records (priority)."""
    return value[0], value[1]


def port_rate(value: bytes) -> tuple[int, int]:
    """``[port][0][0][u16 rate code]`` records."""
    return value[0], int.from_bytes(value[1:5], "big")


def pack_port_rate(port: int, code: int) -> bytes:
    return struct.pack(">BI", port, code)


def mirror(value: bytes, ports: int) -> dict:
    return {"destination": value[0], "sources": ports_from_bitmap(value[2:], ports)}


def pack_mirror(destination: int, sources: list[int], ports: int) -> bytes:
    if not destination:
        return bytes(2 + bitmap_len(ports))
    return bytes([destination, 0]) + bitmap_from_ports(sources, ports)


def igmp(value: bytes) -> dict:
    enabled, vlan = struct.unpack(">HH", value[:4].ljust(4, b"\0"))
    return {"enabled": bool(enabled), "vlan": vlan}


def pack_igmp(enabled: bool, vlan: int) -> bytes:
    return struct.pack(">HH", 1 if enabled else 0, vlan if enabled else 0)


def cable_result(value: bytes) -> dict:
    status, distance = struct.unpack(">II", value[1:9].ljust(8, b"\0"))
    return {
        "port": value[0],
        "status_code": status,
        "status": CABLE_STATUS.get(status, "unknown"),
        "distance_m": distance,
    }


def pack_cable_result(port: int, status: int, distance: int) -> bytes:
    return struct.pack(">BII", port, status, distance)


def lag(value: bytes, ports: int) -> dict:
    """``[id][admin 0/1][member bitmap]``."""
    return {
        "id": value[0],
        "enabled": value[1] == 1,
        "members": ports_from_bitmap(value[2:], ports),
    }


def pack_lag(group: int, enabled: bool, members: list[int], ports: int) -> bytes:
    return bytes([group, 1 if enabled else 0]) + bitmap_from_ports(members, ports)


def port_admin(value: bytes) -> dict:
    """``[port][speed setting][flow control]``."""
    code = value[1]
    return {
        "port": value[0],
        "speed_code": code,
        "speed": ADMIN_SPEEDS.get(code, f"code {code}"),
        "flow_control": bool(value[2]) if len(value) > 2 else False,
    }


def pack_port_admin(port: int, speed_code: int, flow_control: bool) -> bytes:
    if speed_code not in ADMIN_SPEEDS:
        raise ValueError(f"unknown speed setting {speed_code}")
    return bytes([port, speed_code, 1 if flow_control else 0])
