"""Setting numbers (TLV types) of the TP-Link Easy Smart protocol and their value layouts.

Sources and verification state: docs/PROTOCOL-EASYSMART.md.
"""

from __future__ import annotations

# identity and system
MODEL = 1
DESCRIPTION = 2  # the switch name
MAC = 3
IP = 4
NETMASK = 5
GATEWAY = 6
FIRMWARE = 7
HARDWARE = 8
DHCP = 9
PORT_COUNT = 10
LED = 12
AUTO_SAVE = 13
IS_FACTORY = 14

# credentials and actions
USERNAME = 512
PASSWORD = 514
REBOOT = 773
FACTORY_RESET = 1280
SAVE = 2304
TOKEN = 2305

# switching
PORT_SETTINGS = 4096
IGMP = 4352
IGMP_GROUPS = 4353
IGMP_REPORT_SUPPRESSION = 4354
TRUNK = 4608
MTU_VLAN = 8192
PORT_VLAN_ENABLED = 8448
PORT_VLAN = 8449
DOT1Q_ENABLED = 8704
DOT1Q_VLAN = 8705
PVID = 8706
VLAN_LIMITS = 8707
QOS_MODE = 12288
QOS_PRIORITY = 12289
RATE_INGRESS = 12544
RATE_EGRESS = 12545
POE_STATUS = 13568
POE_CONFIG = 13824
POE_GLOBAL_RECOVERY = 14080
POE_RECOVERY = 14336
POE_EXTEND = 14592
PORT_STATS = 16384
MIRROR = 16640
CABLE_TEST = 16896
LOOP_PREVENTION = 17152

NAMES = {
    MODEL: "model",
    DESCRIPTION: "name",
    MAC: "mac",
    IP: "ip",
    NETMASK: "netmask",
    GATEWAY: "gateway",
    FIRMWARE: "firmware",
    HARDWARE: "hardware",
    DHCP: "dhcp",
    PORT_COUNT: "port_count",
    11: "mac_vlan",
    LED: "led",
    AUTO_SAVE: "auto_save",
    IS_FACTORY: "is_factory",
    15: "flash_type",
    16: "port_order",
    USERNAME: "username",
    PASSWORD: "password",
    768: "read_config",
    REBOOT: "reboot",
    780: "write_config",
    FACTORY_RESET: "factory_reset",
    1536: "firmware_upgrade",
    SAVE: "save",
    TOKEN: "token",
    PORT_SETTINGS: "port_settings",
    IGMP: "igmp",
    IGMP_GROUPS: "igmp_groups",
    IGMP_REPORT_SUPPRESSION: "igmp_report_suppression",
    TRUNK: "trunk",
    MTU_VLAN: "mtu_vlan",
    PORT_VLAN_ENABLED: "port_vlan_enabled",
    PORT_VLAN: "port_vlan",
    DOT1Q_ENABLED: "dot1q_enabled",
    DOT1Q_VLAN: "dot1q_vlan",
    PVID: "pvid",
    VLAN_LIMITS: "vlan_limits",
    QOS_MODE: "qos_mode",
    QOS_PRIORITY: "qos_priority",
    RATE_INGRESS: "rate_ingress",
    RATE_EGRESS: "rate_egress",
    POE_STATUS: "poe_status",
    POE_CONFIG: "poe_config",
    POE_GLOBAL_RECOVERY: "poe_global_recovery",
    POE_RECOVERY: "poe_recovery",
    POE_EXTEND: "poe_extend",
    PORT_STATS: "port_stats",
    MIRROR: "mirror",
    CABLE_TEST: "cable_test",
    LOOP_PREVENTION: "loop_prevention",
}

# writes and actions: never sent by the read-only diagnostics scan
ACTION_TAGS = {REBOOT, FACTORY_RESET, SAVE, 768, 780, 1536, CABLE_TEST}

KEY_EXCHANGE = 528
NEW_USERNAME = 513
NEW_PASSWORD = 515
STORM = 12800
PORT_VLAN_MAX = 8450
NAMES.update(
    {
        KEY_EXCHANGE: "session_key",
        NEW_USERNAME: "new_username",
        NEW_PASSWORD: "new_password",
        STORM: "storm_control",
        PORT_VLAN_MAX: "port_vlan_max",
    }
)
SECRET_TAGS = {USERNAME, NEW_USERNAME, PASSWORD, NEW_PASSWORD, KEY_EXCHANGE}

# speed codes of 4096 (configured and actual) and 16384 (link)
SPEEDS = {
    0: ("down", 0, None),
    1: ("auto", 0, None),
    2: ("10M half", 10, "half"),
    3: ("10M full", 10, "full"),
    4: ("100M half", 100, "half"),
    5: ("100M full", 100, "full"),
    6: ("1G full", 1000, "full"),
}
QOS_MODES = {0: "port", 1: "dot1p", 2: "dscp"}
# raw priority 0..3, 3 = highest
PRIORITIES = {3: "high", 2: "medium", 1: "normal", 0: "low"}


def text(value: bytes) -> str:
    return value.split(b"\x00", 1)[0].decode("ascii", "replace")


def cstr(value: str) -> bytes:
    return value.encode("ascii") + b"\x00"


def ipv4(value: bytes) -> str:
    return ".".join(str(b) for b in value[:4])


def pack_ipv4(address: str) -> bytes:
    return bytes(int(x) for x in address.split("."))


def mask_ports(mask: int, ports: int) -> list[int]:
    return [p for p in range(1, ports + 1) if mask >> (p - 1) & 1]


def ports_mask(members: list[int]) -> int:
    mask = 0
    for p in members:
        mask |= 1 << (int(p) - 1)
    return mask


def u32(value: bytes, offset: int) -> int:
    return int.from_bytes(value[offset : offset + 4], "big")


def link(code: int) -> dict:
    label, mbps, duplex = SPEEDS.get(code, (f"code {code}", 0, None))
    return {"link_code": code, "link": label, "up": code > 1, "speed_mbps": mbps, "duplex": duplex}


def port_settings(value: bytes) -> dict:
    """``port, enabled, LAG id, speed setting, actual speed, flow control setting, actual``."""
    return {
        "port": value[0],
        "enabled": value[1] == 1,
        "lag": value[2],
        "speed_setting": value[3],
        "speed_actual": value[4],
        "flow_setting": value[5] == 1,
        "flow_actual": value[6] == 1,
    }


def pack_port_settings(port: int, enabled: bool, speed: int, flow: bool) -> bytes:
    if speed not in range(1, 7):
        raise ValueError(f"unknown speed setting {speed}")
    return bytes([port, 1 if enabled else 0, 0, speed, 0, 1 if flow else 0, 0])


def port_stats(value: bytes) -> dict:
    """``port, enabled, link speed, tx good, tx bad, rx good, rx bad`` (packets, u32)."""
    return {
        "port": value[0],
        "enabled": value[1] == 1,
        **link(value[2]),
        "tx_packets": u32(value, 3),
        "tx_bad": u32(value, 7),
        "rx_packets": u32(value, 11),
        "rx_bad": u32(value, 15),
    }


def pack_port_stats(port, enabled, code, tx, tx_bad, rx, rx_bad) -> bytes:
    return bytes([port, 1 if enabled else 0, code]) + b"".join(
        (c & 0xFFFFFFFF).to_bytes(4, "big") for c in (tx, tx_bad, rx, rx_bad)
    )


def dot1q_vlan(value: bytes, ports: int) -> dict:
    """``VID u16, member mask, tagged mask, name NUL``."""
    vid = int.from_bytes(value[:2], "big")
    return {
        "id": vid,
        "members": mask_ports(u32(value, 2), ports),
        "tagged": mask_ports(u32(value, 6), ports),
        "name": text(value[10:]),
    }


def pack_dot1q_vlan(vid: int, members: list[int], tagged: list[int], name: str = "") -> bytes:
    if set(tagged) - set(members):
        raise ValueError("tagged ports must also be members")
    if not 1 <= vid <= 4094:
        raise ValueError("A VLAN ID is 1 to 4094.")
    return (
        vid.to_bytes(2, "big")
        + ports_mask(members).to_bytes(4, "big")
        + ports_mask(tagged).to_bytes(4, "big")
        + cstr(name[:10])
    )


def group_mask(value: bytes, ports: int) -> tuple[int, list[int]]:
    """``id, mask``: LAG groups (4608) and port-based VLANs (8449)."""
    return value[0], mask_ports(u32(value, 1), ports)


def pack_group_mask(group: int, members: list[int]) -> bytes:
    return bytes([group]) + ports_mask(members).to_bytes(4, "big")


def mirror(value: bytes, ports: int) -> dict:
    """``enabled, destination, ingress mask, egress mask``."""
    enabled, dest = value[0], value[1]
    ingress, egress = mask_ports(u32(value, 2), ports), mask_ports(u32(value, 6), ports)
    return {
        "destination": dest if enabled else 0,
        "sources": sorted(set(ingress) | set(egress)) if enabled else [],
        "ingress": ingress,
        "egress": egress,
    }


def pack_mirror(destination: int, ingress: list[int], egress: list[int]) -> bytes:
    if not destination:
        return bytes(10)
    return (
        bytes([1, destination])
        + ports_mask(ingress).to_bytes(4, "big")
        + ports_mask(egress).to_bytes(4, "big")
    )


def port_rate(value: bytes) -> tuple[int, int]:
    """``port, enabled, kbit/s`` -> (port, kbit/s; 0 = no limit)."""
    return value[0], u32(value, 2) if value[1] else 0


def pack_port_rate(port: int, kbps: int) -> bytes:
    if kbps % 64 or not 0 <= kbps <= 1_000_000:
        raise ValueError("A rate limit is a multiple of 64 kbit/s, at most 1000000.")
    return bytes([port, 1 if kbps else 0]) + kbps.to_bytes(4, "big")


def storm(value: bytes) -> dict:
    """``port, enabled, unknown unicast, multicast, broadcast, kbit/s``."""
    return {
        "port": value[0],
        "enabled": value[1] == 1,
        "unknown_unicast": value[2] == 1,
        "multicast": value[3] == 1,
        "broadcast": value[4] == 1,
        "kbps": u32(value, 5),
    }


def pack_storm(port: int, kbps: int, unicast=True, multicast=True, broadcast=True) -> bytes:
    on = bool(kbps) and (unicast or multicast or broadcast)
    if on and (kbps % 64 or kbps < 64):
        raise ValueError("A storm control rate is a multiple of 64 kbit/s.")
    flags = [1 if on and f else 0 for f in (unicast, multicast, broadcast)]
    return bytes([port, 1 if on else 0, *flags]) + (kbps if on else 0).to_bytes(4, "big")


# PoE auto recovery, per port (20 bytes, confirmed by a tester's scans, #114):
# port, ping IPv4, startup delay s, interval s, failure threshold, break time s (u16 each),
# 6 bytes of unknown meaning (kept as read), enabled (1 = on)
def poe_recovery(value: bytes) -> dict:
    return {
        "port": value[0],
        "ip": ipv4(value[1:5]),
        "startup_delay": int.from_bytes(value[5:7], "big"),
        "interval": int.from_bytes(value[7:9], "big"),
        "threshold": int.from_bytes(value[9:11], "big"),
        "break_time": int.from_bytes(value[11:13], "big"),
        "enabled": value[19] == 1,
        "_rest": value[13:19].hex(),
    }


def pack_poe_recovery(cfg: dict) -> bytes:
    rest = bytes.fromhex(cfg.get("_rest") or "00" * 6)
    return (
        bytes([cfg["port"]])
        + pack_ipv4(cfg["ip"])
        + b"".join(
            int(cfg[k]).to_bytes(2, "big")
            for k in ("startup_delay", "interval", "threshold", "break_time")
        )
        + rest[:6].ljust(6, b"\x00")
        + bytes([1 if cfg["enabled"] else 0])
    )
