"""A software switch that speaks NSDP.

Used by the tests, by demo mode and for the documentation screenshots. It
models what the real hardware is known to do, including the awkward parts:
password encodings, unsupported settings and going silent during a reboot.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import struct
import time
from dataclasses import dataclass, field

from .nsdp import firmware as fw
from .nsdp import tags as T
from .nsdp.auth import (
    TAG_AUTH_V2,
    TAG_NEW_PASSWORD,
    TAG_PASSWORD,
    PasswordMode,
    credential_tlv,
    flags_for_mode,
    xor_password,
)
from .nsdp.packet import BROADCAST_MAC, Op, Packet, PacketError, parse_mac

log = logging.getLogger(__name__)

OK, UNSUPPORTED, BAD_VALUE, BAD_PASSWORD = 0, 3, 5, 7


@dataclass
class SimSwitch:
    mac: str
    model: str = "GS108E"
    ports: int = 8
    name: str = ""
    ip: str = "192.168.0.239"
    netmask: str = "255.255.255.0"
    gateway: str = "192.168.0.254"
    dhcp: bool = False
    firmware: str = "1.00.12"
    password: str = "password"
    password_mode: PasswordMode = PasswordMode.XOR
    serial: str | None = None
    links: list[int] = field(default_factory=list)
    unsupported: set[int] = field(default_factory=set)
    # "status": answer unknown tags with error 3; "skip": leave them out;
    # "silent": do not answer the whole request (seen on old firmware);
    # "empty": answer the whole request with an empty success reply (GS108E v1 1.00.09)
    unknown_tag: str = "skip"
    reboot_seconds: float = 3.0
    max_vlans: int = 32
    traffic: bool = True
    lag_groups: int = 0  # 0 = by size: 2 for 5-port models, 4 otherwise
    tftp_drop_blocks: set[int] = field(default_factory=set)  # lose the first ACK of these
    garble: int = 0  # non-zero: answer every setting with random bytes (seeded), for fuzz tests

    def __post_init__(self):
        self._mac = parse_mac(self.mac)
        if not self.links:
            self.links = ([5, 5, 0, 4, 0, 5, 0, 2] * 6)[: self.ports]
        self._factory = None
        self._factory = {k: copy.deepcopy(v) for k, v in self.__dict__.items()}
        self.offline_until = 0.0
        self.salt = os.urandom(4)
        self.upgrade_mode = False
        self.upgrades = 0
        self.failed_logins = 0
        self.writes = 0
        self._reset_config()

    def _reset_config(self) -> None:
        every = list(range(1, self.ports + 1))
        self.vlan_mode = 0
        self.port_vlans = {1: list(every)}
        self.dot1q: dict[int, tuple[list[int], list[int]]] = {1: (list(every), [])}
        self.pvid = dict.fromkeys(every, 1)
        self.qos_mode = 2
        self.priorities = dict.fromkeys(every, 3)
        self.ingress = dict.fromkeys(every, 0)
        self.egress = dict.fromkeys(every, 0)
        self.storm = False
        self.storm_rates = dict.fromkeys(every, 0)
        self.mirror = (0, [])
        self.igmp = (False, 0)
        self.block_unknown = False
        self.validate_v3 = False
        self.loop_detection = False
        self.lags = {
            g: (False, []) for g in range(1, (self.lag_groups or (2 if self.ports <= 5 else 4)) + 1)
        }
        self.admin = dict.fromkeys(every, (0, False))
        self.counters = {p: [0] * 6 for p in every}
        self._last_tick = time.monotonic()

    # -- behaviour -----------------------------------------------------------

    @property
    def online(self) -> bool:
        return time.monotonic() >= self.offline_until

    def effective_link(self, port: int) -> int:
        """The link code the port reports, after the admin speed setting."""
        base = self.links[port - 1]
        speed, _ = self.admin[port]
        if speed == 1:
            return 0
        fixed = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5}.get(speed)
        return base if fixed is None or not base else min(base, fixed)

    def _tick(self) -> None:
        now = time.monotonic()
        elapsed, self._last_tick = now - self._last_tick, now
        if not self.traffic:
            return
        for port, code in enumerate(self.links, start=1):
            if code:
                rate = T.LINK_CODES[code][1] * 1500 * port
                self.counters[port][0] += int(elapsed * rate)
                self.counters[port][1] += int(elapsed * rate * 0.4)

    def handle(self, request: Packet) -> Packet | None:
        if not self.online or request.device_mac not in (BROADCAST_MAC, self._mac):
            return None
        if request.op == Op.READ_REQUEST:
            return self._read(request)
        if request.op == Op.WRITE_REQUEST and request.device_mac == self._mac:
            return self._write(request)
        return None

    def _reply(self, request: Packet, status=OK, failed_tag=0, tlvs=None) -> Packet:
        return Packet(
            request.op + 1, request.host_mac, self._mac, request.seq, status, failed_tag, tlvs or []
        )

    def _read(self, request: Packet) -> Packet | None:
        self._tick()
        out: list[tuple[int, bytes]] = []
        for tag, value in request.tlvs:
            values = None if tag in self.unsupported else self._get(tag, value)
            if values is None:
                if self.unknown_tag == "empty":
                    return self._reply(request)  # GS108E v1: nothing at all, but "success"
                if self.unknown_tag == "silent":
                    return None
                if self.unknown_tag == "status":
                    return self._reply(request, UNSUPPORTED, tag)
                continue
            out += [(tag, v) for v in values]
        return self._reply(request, tlvs=out)

    def _get(self, tag: int, arg: bytes) -> list[bytes] | None:
        if self.garble and tag not in (T.MAC, T.MODEL):
            import random

            rng = random.Random(self.garble * 65599 + tag)
            return [
                bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 2, 3, 5, 9, 17, 40])))
                for _ in range(rng.choice([1, 1, 2, 8]))
            ]
        return self._get_real(tag, arg)

    def _get_real(self, tag: int, arg: bytes) -> list[bytes] | None:
        n = self.ports
        every = range(1, n + 1)
        simple = {
            T.MODEL: lambda: [self.model.encode()],
            T.NAME: lambda: [self.name.encode()],
            T.MAC: lambda: [self._mac],
            T.IP: lambda: [T.pack_ipv4(self.ip)],
            T.NETMASK: lambda: [T.pack_ipv4(self.netmask)],
            T.GATEWAY: lambda: [T.pack_ipv4(self.gateway)],
            T.DHCP: lambda: [T.pack_flag(self.dhcp)],
            T.FIRMWARE: lambda: [self.firmware.encode()],
            T.PORT_COUNT: lambda: [bytes([n])],
            T.MAX_VLANS: lambda: [struct.pack(">H", self.max_vlans)],
            T.PORT_STATUS: lambda: [
                bytes([p, self.effective_link(p), int(self.admin[p][1])]) for p in every
            ],
            T.PORT_STATS: lambda: [T.pack_port_stats(p, self.counters[p]) for p in every],
            T.VLAN_MODE: lambda: [bytes([self.vlan_mode])],
            T.PVID: lambda: [T.pack_port_u16(p, self.pvid[p]) for p in every],
            T.QOS_MODE: lambda: [bytes([self.qos_mode])],
            T.QOS_PRIORITY: lambda: [bytes([p, self.priorities[p]]) for p in every],
            T.RATE_INGRESS: lambda: [T.pack_port_rate(p, self.ingress[p]) for p in every],
            T.RATE_EGRESS: lambda: [T.pack_port_rate(p, self.egress[p]) for p in every],
            T.STORM_ENABLE: lambda: [T.pack_flag(self.storm)],
            T.STORM_RATE: lambda: [T.pack_port_rate(p, self.storm_rates[p]) for p in every],
            T.MIRROR: lambda: [T.pack_mirror(self.mirror[0] or 0, self.mirror[1], n)],
            T.IGMP: lambda: [T.pack_igmp(*self.igmp)],
            T.IGMP_BLOCK_UNKNOWN: lambda: [T.pack_flag(self.block_unknown)],
            T.IGMP_VALIDATE_V3: lambda: [T.pack_flag(self.validate_v3)],
            T.LOOP_DETECTION: lambda: [T.pack_flag(self.loop_detection)],
        }
        if tag == T.LAG_COUNT:
            return [bytes([len(self.lags)])]
        if tag == T.LAG:
            return [T.pack_lag(g, e, m, n) for g, (e, m) in sorted(self.lags.items())]
        if tag == T.PORT_ADMIN:
            return [T.pack_port_admin(p, *self.admin[p]) for p in every]
        if tag in simple:
            return simple[tag]()
        if tag == T.SERIAL:
            return [b"\x01" + self.serial.encode()] if self.serial else None
        if tag == T.PASSWORD_MODE:
            flags = flags_for_mode(self.password_mode)
            return [flags] if flags else None
        if tag == T.PASSWORD_SALT:
            if not self.password_mode.needs_salt:
                return None
            self.salt = os.urandom(4)
            return [self.salt]
        if tag == T.VLAN_PORT:
            return [T.pack_port_vlan(v, m, n) for v, m in sorted(self.port_vlans.items())]
        if tag == T.VLAN_DOT1Q:
            wanted = int.from_bytes(arg, "big") if len(arg) == 2 else None
            return [
                T.pack_dot1q_vlan(v, m, t, n)
                for v, (m, t) in sorted(self.dot1q.items())
                if wanted in (None, v)
            ]
        if tag == T.CABLE_RESULT:
            port = arg[0] if arg else 1
            if not 1 <= port <= n:
                return []
            up = self.links[port - 1]
            return [T.pack_cable_result(port, 0 if up else 2, 0 if up else 3 + port)]
        return None

    def _authenticated(self, request: Packet) -> bool:
        if not request.tlvs:
            return False
        expected = credential_tlv(self.password_mode, self.password, self._mac, self.salt)
        return request.tlvs[0] == expected

    def _write(self, request: Packet) -> Packet:
        if not self._authenticated(request):
            self.failed_logins += 1
            return self._reply(request, BAD_PASSWORD, TAG_PASSWORD)
        self.writes += 1
        for tag, value in request.tlvs[1:]:
            if tag in self.unsupported:
                return self._reply(request, UNSUPPORTED, tag)
            try:
                status = self._set(tag, value)
            except (ValueError, IndexError, KeyError, struct.error):
                status = BAD_VALUE
            if status:
                return self._reply(request, status, tag)
        return self._reply(request)

    def _check_ports(self, *ports: int) -> None:
        if any(not 1 <= p <= self.ports for p in ports):
            raise ValueError("port out of range")

    def _set(self, tag: int, value: bytes) -> int:
        n = self.ports
        if tag == T.NAME:
            self.name = T.text(value)[:20]
        elif tag == T.DHCP:
            self.dhcp = T.flag(value)
        elif tag == T.IP:
            self.ip = T.ipv4(value)
        elif tag == T.NETMASK:
            self.netmask = T.ipv4(value)
        elif tag == T.GATEWAY:
            self.gateway = T.ipv4(value)
        elif tag == TAG_NEW_PASSWORD:
            raw = value if self.password_mode is PasswordMode.PLAIN else xor_password(value)
            self.password = raw.decode()
        elif tag == T.REBOOT:
            self.offline_until = time.monotonic() + self.reboot_seconds
        elif tag == T.FACTORY_RESET:
            keep = self._factory
            for key, val in keep.items():
                if key != "_factory":
                    setattr(self, key, copy.deepcopy(val))
            self._reset_config()
            self.offline_until = time.monotonic() + self.reboot_seconds
        elif tag == T.STATS_RESET:
            self.counters = {p: [0] * 6 for p in range(1, n + 1)}
        elif tag == T.CABLE_TEST:
            self._check_ports(value[0])
        elif tag == T.VLAN_MODE:
            if value[0] > 4:
                return BAD_VALUE
            if value[0] != self.vlan_mode:
                mode = value[0]
                self._reset_vlans()
                self.vlan_mode = mode
        elif tag == T.VLAN_PORT:
            vlan = T.port_vlan(value, n)
            if not 1 <= vlan["id"] <= n:
                return BAD_VALUE
            self.port_vlans[vlan["id"]] = vlan["members"]
        elif tag == T.VLAN_DOT1Q:
            vlan = T.dot1q_vlan(value, n)
            if not 1 <= vlan["id"] <= 4093:
                return BAD_VALUE
            if vlan["id"] not in self.dot1q and len(self.dot1q) >= self.max_vlans:
                return BAD_VALUE
            if set(vlan["tagged"]) - set(vlan["members"]):
                return BAD_VALUE
            if any(v == vlan["id"] and p not in vlan["members"] for p, v in self.pvid.items()):
                return BAD_VALUE  # "change its PVID first"
            self.dot1q[vlan["id"]] = (vlan["members"], vlan["tagged"])
        elif tag == T.VLAN_DELETE:
            vid = int.from_bytes(value, "big")
            if vid == 1 or vid not in self.dot1q or vid in self.pvid.values():
                return BAD_VALUE
            del self.dot1q[vid]
        elif tag == T.PVID:
            port, vid = T.port_u16(value)
            self._check_ports(port)
            if vid not in self.dot1q:
                return BAD_VALUE
            self.pvid[port] = vid
        elif tag == T.QOS_MODE:
            if value[0] not in T.QOS_MODES:
                return BAD_VALUE
            if value[0] != self.qos_mode:
                self.priorities = dict.fromkeys(range(1, n + 1), 3)
            self.qos_mode = value[0]
        elif tag == T.QOS_PRIORITY:
            port, prio = T.port_byte(value)
            self._check_ports(port)
            if prio not in T.PRIORITIES:
                return BAD_VALUE
            self.priorities[port] = prio
        elif tag in (T.RATE_INGRESS, T.RATE_EGRESS, T.STORM_RATE):
            port, code = T.port_rate(value)
            self._check_ports(port)
            if code not in T.RATE_CODES:
                return BAD_VALUE
            table = {
                T.RATE_INGRESS: self.ingress,
                T.RATE_EGRESS: self.egress,
                T.STORM_RATE: self.storm_rates,
            }[tag]
            table[port] = code
        elif tag == T.STORM_ENABLE:
            self.storm = T.flag(value)
        elif tag == T.MIRROR:
            mirror = T.mirror(value, n)
            if mirror["destination"]:
                self._check_ports(mirror["destination"])
                self.mirror = (mirror["destination"], mirror["sources"])
            else:
                self.mirror = (0, [])
        elif tag == T.IGMP:
            state = T.igmp(value)
            self.igmp = (state["enabled"], state["vlan"])
        elif tag == T.IGMP_BLOCK_UNKNOWN:
            self.block_unknown = T.flag(value)
        elif tag == T.IGMP_VALIDATE_V3:
            self.validate_v3 = T.flag(value)
        elif tag == T.LOOP_DETECTION:
            self.loop_detection = T.flag(value)
        elif tag == T.PORT_STATUS:
            port, code = value[0], value[1]
            self._check_ports(port)
            if code not in T.ADMIN_SPEEDS:
                return BAD_VALUE
            if any(port in m for e, m in self.lags.values() if e) and code == 1:
                return BAD_VALUE  # a LAG member cannot be switched off
            self.admin[port] = (code, bool(value[2]) if len(value) > 2 else False)
        elif tag == T.LAG:
            return self._set_lag(T.lag(value, n))
        elif tag == T.FIRMWARE_UPGRADE:
            self.upgrade_mode = True
        elif tag in (TAG_PASSWORD, TAG_AUTH_V2):
            pass
        else:
            return UNSUPPORTED
        return OK

    def _set_lag(self, group: dict) -> int:
        gid, members = group["id"], group["members"]
        if gid not in self.lags:
            return BAD_VALUE
        if not group["enabled"]:
            self.lags[gid] = (False, [])
            return OK
        limit = 8 if self.model.upper().startswith("XS") else 4
        if not 2 <= len(members) <= limit:
            return BAD_VALUE
        if any(set(members) & set(m) for g, (e, m) in self.lags.items() if e and g != gid):
            return BAD_VALUE
        if len({self.admin[p] for p in members}) > 1:
            return BAD_VALUE  # members must share speed and flow control
        if len({self.pvid[p] for p in members}) > 1:
            return BAD_VALUE  # and the same PVID
        dest, sources = self.mirror
        if set(members) & ({dest} | set(sources)):
            return BAD_VALUE  # LAG members cannot take part in mirroring
        self.lags[gid] = (True, members)
        return OK

    def _reset_vlans(self) -> None:
        every = list(range(1, self.ports + 1))
        self.port_vlans = {1: list(every)}
        self.dot1q = {1: (list(every), [])}
        self.pvid = dict.fromkeys(every, 1)


class SimTftp(asyncio.DatagramProtocol):
    """The TFTP server of the simulated switches: accepts one firmware upload."""

    def __init__(self, switches: list[SimSwitch]):
        self.switches = switches
        self.transport: asyncio.DatagramTransport | None = None
        self.sessions: dict[tuple, dict] = {}

    def connection_made(self, transport):
        self.transport = transport

    def _error(self, addr, code: int, text: str) -> None:
        self.transport.sendto(struct.pack(">HH", 5, code) + text.encode() + b"\0", addr)

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) < 4:
            return
        op = struct.unpack(">H", data[:2])[0]
        if op == 2:  # write request
            switch = next((s for s in self.switches if s.upgrade_mode and s.online), None)
            if switch is None:
                return self._error(addr, 4, "Illegal operation")
            self.sessions[addr] = {
                "switch": switch,
                "data": bytearray(),
                "last": 0,
                "dropped": set(),
            }
            self.transport.sendto(struct.pack(">HH", 4, 0), addr)
        elif op == 3 and addr in self.sessions:
            session = self.sessions[addr]
            block = struct.unpack(">H", data[2:4])[0]
            if block == (session["last"] + 1) & 0xFFFF:
                session["data"] += data[4:]
                session["last"] = block
                if len(data) - 4 < 512:
                    return self._finish(addr, block)
            switch = session["switch"]
            if block in switch.tftp_drop_blocks and block not in session["dropped"]:
                session["dropped"].add(block)
                return  # lose this ACK once: the client must retransmit
            self.transport.sendto(struct.pack(">HH", 4, block), addr)

    def _finish(self, addr, block: int) -> None:
        session = self.sessions.pop(addr)
        switch = session["switch"]
        try:
            image = fw.parse(bytes(session["data"]))
            if not fw.matches_model(image.board, switch.model):
                raise fw.FirmwareError("wrong board")
        except fw.FirmwareError:
            switch.upgrade_mode = False
            return self._error(addr, 0, "Firmware upgrade failure!")
        self.transport.sendto(struct.pack(">HH", 4, block), addr)
        switch.firmware = image.version
        switch.upgrade_mode = False
        switch.upgrades += 1
        switch.offline_until = time.monotonic() + switch.reboot_seconds


class Simulator(asyncio.DatagramProtocol):
    """Several simulated switches behind one UDP socket."""

    def __init__(self, switches: list[SimSwitch]):
        self.switches = switches
        self.transport: asyncio.DatagramTransport | None = None
        self.port = 0
        self.requests = 0
        self.tftp = SimTftp(switches)
        self.tftp_transport: asyncio.DatagramTransport | None = None
        self.tftp_port = 0

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: self, local_addr=(host, port)
        )
        self.port = self.transport.get_extra_info("sockname")[1]
        self.tftp_transport, _ = await loop.create_datagram_endpoint(
            lambda: self.tftp, local_addr=(host, 0)
        )
        self.tftp_port = self.tftp_transport.get_extra_info("sockname")[1]
        return self.port

    def stop(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None
        if self.tftp_transport:
            self.tftp_transport.close()
            self.tftp_transport = None

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            request = Packet.decode(data)
        except PacketError:
            return
        self.requests += 1
        for switch in self.switches:
            reply = switch.handle(request)
            if reply is not None and self.transport is not None:
                self.transport.sendto(reply.encode(), addr)

    def by_mac(self, mac: str) -> SimSwitch:
        return next(s for s in self.switches if s.mac == mac)


def demo_switches() -> list[SimSwitch]:
    """The fleet shown in demo mode and in the screenshots."""
    return [
        SimSwitch(
            "00:26:f2:aa:10:01",
            model="GS108E",
            name="office",
            ip="192.168.0.239",
            firmware="1.00.12",
            serial="2D51B3A500C1",
        ),
        SimSwitch(
            "a0:21:b7:bb:20:02",
            model="GS108Ev3",
            name="rack-top",
            ip="192.168.0.240",
            firmware="2.06.24",
            password_mode=PasswordMode.HASH8,
            links=[5, 5, 5, 5, 4, 0, 0, 5],
            serial="3V11A7B200F4",
        ),
        SimSwitch(
            "e0:91:f5:cc:30:03",
            model="GS105E",
            ports=5,
            name="",
            ip="192.168.0.241",
            dhcp=True,
            firmware="1.02.04",
            links=[5, 0, 4, 0, 5],
            unsupported={T.LOOP_DETECTION, T.SERIAL, T.PORT_ADMIN},
        ),
        SimSwitch(
            "28:c6:8e:dd:40:04",
            model="JGS516PE",
            ports=16,
            name="basement-poe",
            ip="192.168.0.242",
            firmware="2.6.0.48",
            max_vlans=64,
            serial="4KC2957P00A9",
        ),
    ]
