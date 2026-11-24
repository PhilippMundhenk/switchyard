"""Software TP-Link Easy Smart switches for tests and demo mode.

Behaves as the protocol notes describe (docs/PROTOCOL-EASYSMART.md): one setting per read
with its companions, silence for unknown settings and bad tokens, single-use tokens on older
firmware ("A"), an RSA session key on newer firmware ("B"), writes changing the running
configuration until saved, and the switch's own refusals (LAG port ranges, a full VLAN table,
flow control on fixed speeds). Answers go back to the sender instead of the broadcast address.
"""

from __future__ import annotations

import asyncio
import copy
import random
import time
from dataclasses import dataclass, field

from ..nsdp.packet import parse_mac
from . import tags as T
from .packet import RSA_EXPONENT, Op, Packet

# a small test key pair; the client is told this modulus instead of the vendor's
SIM_P, SIM_Q = 4294967291, 4294967279
SIM_MODULUS = SIM_P * SIM_Q
SIM_PRIVATE = pow(RSA_EXPONENT, -1, (SIM_P - 1) * (SIM_Q - 1))
KEY_ACCEPTED = 4098


def _cstr(v: str) -> bytes:
    return v.encode() + b"\x00"


@dataclass
class SimEasySmart:
    mac: str
    model: str = "TL-SG108PE"
    hardware: str = "TL-SG108PE 3.0"
    firmware: str = "1.0.0 Build 20201030 Rel.62149"
    generation: str = "A"
    # newer firmware that still hands out tokens and drops writes without one
    # (seen on a TL-SG108PE 5.0, firmware 1.0.0 Build 20230218)
    session_token: bool = False
    ports: int = 8
    name: str = "TL-SG108PE"
    ip: str = "192.168.0.1"
    netmask: str = "255.255.255.0"
    gateway: str = "0.0.0.0"
    username: str = "admin"
    password: str = "admin"
    auto_save: bool | None = None  # None: the switch does not report it (needs 'save')
    links: list[int] = field(default_factory=list)
    lag_groups: int = 2
    reboot_seconds: float = 3.0
    write_delay: float = 0.0  # answer changes this late (some take the real switch seconds)
    key_delay: float = 0.0  # answer the session key exchange this late
    ignore_keys: bool = False  # stay silent on key exchanges (a switch that is busy)
    traffic: bool = True

    def __post_init__(self):
        self._mac = parse_mac(self.mac)
        if not self.links:
            self.links = ([6, 6, 0, 5, 0, 6, 0, 3] * 4)[: self.ports]
        self.offline_until = 0.0
        self.tokens: set[int] = set()
        self.session_key: bytes | None = None
        self.writes = 0
        self.saves = 0
        self._reset()
        self.saved = copy.deepcopy(self.config)
        self.counters = {p: [0, 0, 0, 0] for p in range(1, self.ports + 1)}
        self._last_tick = time.monotonic()

    # -- configuration -------------------------------------------------------

    def _reset(self) -> None:
        all_ports = list(range(1, self.ports + 1))
        self.config = {
            "name": self.name,
            "dhcp": False,
            "ip": self.ip,
            "netmask": self.netmask,
            "gateway": self.gateway,
            "led": True,
            "loop": False,
            "igmp": False,
            "igmp_suppress": False,
            "dot1q": False,
            "port_vlan": True,  # the default on hardware: port-based, every port in group 1
            "vlans": {1: {"members": all_ports, "tagged": [], "name": "Default_VLAN"}},
            "pvid": dict.fromkeys(all_ports, 1),
            "port_groups": {1: all_ports},
            "mtu_vlan": False,
            "mtu_uplink": 1,
            "qos_mode": 0,
            "priority": dict.fromkeys(all_ports, 0),
            "ingress": dict.fromkeys(all_ports, 0),
            "egress": dict.fromkeys(all_ports, 0),
            "storm": {p: [0, 0, 0, 0] for p in all_ports},
            "mirror": [0, 0, [], []],
            "lags": {g: [] for g in range(1, self.lag_groups + 1)},
            "port": {p: {"enabled": True, "speed": 1, "flow": False} for p in all_ports},
            # PoE on ports 1-4 (TL-SG108PE): auto recovery and extend mode
            "poe_recovery_on": False,
            "poe_recovery": {
                p: {
                    "port": p,
                    "ip": "0.0.0.0",
                    "startup_delay": 60,
                    "interval": 60,
                    "threshold": 5,
                    "break_time": 15,
                    "enabled": False,
                    "_rest": "00" * 6,
                }
                for p in range(1, 5)
            }
            if self.model.endswith("PE")
            else {},
            "poe_extend": dict.fromkeys(range(1, 5), False) if self.model.endswith("PE") else {},
            "username": self.username,
            "password": self.password,
        }

    def _lag_range(self, group: int) -> list[int]:
        if self.lag_groups != 2:
            return list(range(1, self.ports + 1))
        half = (self.ports + 1) // 2
        return list(range(1, half + 1)) if group == 1 else list(range(half + 1, self.ports + 1))

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        if not self.traffic:
            return
        for port, code in enumerate(self.links, start=1):
            if code > 1 and self.config["port"][port]["enabled"]:
                rate = {2: 800, 3: 1500, 4: 8000, 5: 15000, 6: 80000}.get(code, 0) * dt
                c = self.counters[port]
                c[0] += int(rate * 0.4)
                c[2] += int(rate + random.random())

    def _link(self, port: int) -> int:
        setting = self.config["port"][port]
        if not setting["enabled"] or not self.links[port - 1]:
            return 0
        return self.links[port - 1] if setting["speed"] == 1 else setting["speed"]

    # -- reading -------------------------------------------------------------

    def _identity(self) -> list[tuple[int, bytes]]:
        c = self.config
        out = [
            (T.MODEL, _cstr(self.model)),
            (T.DESCRIPTION, _cstr(c["name"])),
            (T.MAC, self._mac),
            (T.FIRMWARE, _cstr(self.firmware)),
            (T.HARDWARE, _cstr(self.hardware)),
            (T.DHCP, bytes([c["dhcp"]])),
            (T.IP, T.pack_ipv4(c["ip"])),
            (T.NETMASK, T.pack_ipv4(c["netmask"])),
            (T.GATEWAY, T.pack_ipv4(c["gateway"])),
        ]
        if self.auto_save is not None:
            out.append((T.AUTO_SAVE, bytes([self.auto_save])))
        return out

    def _get(self, tag: int) -> list[tuple[int, bytes]] | None:
        c = self.config
        ports = range(1, self.ports + 1)
        if tag in (T.DESCRIPTION, T.MODEL, T.MAC, T.IP):
            return self._identity()
        if tag == T.PORT_COUNT:
            return [(tag, bytes([self.ports]))]
        if tag == T.AUTO_SAVE and self.auto_save is not None:
            return [(tag, bytes([self.auto_save]))]
        if tag == T.LED:
            return [(tag, bytes([c["led"]]))]
        if tag == T.LOOP_PREVENTION:
            return [(tag, bytes([c["loop"]]))]
        if tag == T.IGMP:
            return [
                (tag, bytes([c["igmp"]])),
                (T.IGMP_REPORT_SUPPRESSION, bytes([c["igmp_suppress"]])),
            ]
        if tag == T.PORT_STATS:
            self._tick()
            return [
                (
                    tag,
                    T.pack_port_stats(p, c["port"][p]["enabled"], self._link(p), *self.counters[p]),
                )
                for p in ports
            ]
        if tag == T.PORT_SETTINGS:
            out = []
            for p in ports:
                s = c["port"][p]
                lag = next((g for g, m in c["lags"].items() if p in m), 0)
                link = self._link(p)
                flow = s["flow"] and link > 1
                out.append((tag, bytes([p, s["enabled"], lag, s["speed"], link, s["flow"], flow])))
            return out
        if tag == T.TRUNK:
            return [(tag, T.pack_group_mask(g, m)) for g, m in sorted(c["lags"].items())]
        if tag == T.MIRROR:
            on, dest, ingress, egress = c["mirror"]
            return [(tag, T.pack_mirror(dest, ingress, egress) if on else bytes(10))]
        if tag == T.QOS_MODE:
            return [(tag, bytes([c["qos_mode"]]))]
        if tag == T.QOS_PRIORITY:
            return [(tag, bytes([p, c["priority"][p]])) for p in ports]
        if tag in (T.RATE_INGRESS, T.RATE_EGRESS):
            table = c["ingress" if tag == T.RATE_INGRESS else "egress"]
            return [(tag, T.pack_port_rate(p, table[p])) for p in ports]
        if tag == T.STORM:
            return [
                (
                    tag,
                    bytes([p, 1 if s[3] else 0, *(1 if s[3] and x else 0 for x in s[:3])])
                    + s[3].to_bytes(4, "big"),
                )
                for p, s in sorted(c["storm"].items())
            ]
        if tag in (T.POE_RECOVERY, T.POE_GLOBAL_RECOVERY, T.POE_EXTEND) and c["poe_recovery"]:
            # like the TL-SG108PE: the global switch comes along with the recovery rows (#119)
            if tag == T.POE_EXTEND:
                return [(tag, bytes([p, c["poe_extend"][p]])) for p in sorted(c["poe_extend"])]
            rows = [
                (T.POE_RECOVERY, T.pack_poe_recovery(r))
                for _, r in sorted(c["poe_recovery"].items())
            ]
            return [(T.POE_GLOBAL_RECOVERY, bytes([c["poe_recovery_on"]])), *rows]
        if tag == T.MTU_VLAN:
            return [(tag, bytes([c["mtu_vlan"], c["mtu_uplink"]]))]
        if tag == T.PORT_VLAN_ENABLED:
            rows = [
                (T.PORT_VLAN, T.pack_group_mask(g, m)) for g, m in sorted(c["port_groups"].items())
            ]
            return [(tag, bytes([c["port_vlan"]])), *rows, (T.PORT_VLAN_MAX, b"\x08")]
        if tag == T.DOT1Q_ENABLED:
            return [(tag, bytes([c["dot1q"]]))]
        if tag == T.DOT1Q_VLAN:
            rows = [
                (tag, T.pack_dot1q_vlan(vid, v["members"], v["tagged"], v["name"]))
                for vid, v in sorted(c["vlans"].items())
            ]
            return [(T.DOT1Q_ENABLED, bytes([c["dot1q"]])), *rows, (T.VLAN_LIMITS, b"\x20")]
        if tag == T.PVID:
            rows = [(tag, bytes([p]) + v.to_bytes(2, "big")) for p, v in sorted(c["pvid"].items())]
            return [*rows, (T.VLAN_LIMITS, b"\x20")]
        return None  # unknown here: the switch stays silent

    # -- writing -------------------------------------------------------------

    def _set(self, tlvs: list[tuple[int, bytes]]) -> int:
        """Apply a write; return the error code (0 = done)."""
        c = self.config
        values = dict(tlvs)
        system = {T.DESCRIPTION, T.DHCP, T.IP, T.NETMASK, T.GATEWAY}
        if system & values.keys():
            if not system <= values.keys():
                return 3
            c.update(
                name=T.text(values[T.DESCRIPTION]),
                dhcp=values[T.DHCP][0] == 1,
                ip=T.ipv4(values[T.IP]),
                netmask=T.ipv4(values[T.NETMASK]),
                gateway=T.ipv4(values[T.GATEWAY]),
            )
        # PoE goes in groups (#119): recovery rows of every PoE port together with the global
        # switch, and the extend mode of every PoE port
        tags = [t for t, _ in tlvs]
        poe_ports = len(c["poe_recovery"])
        if T.POE_RECOVERY in tags and (
            T.POE_GLOBAL_RECOVERY not in tags or tags.count(T.POE_RECOVERY) != poe_ports
        ):
            return 3
        if T.POE_EXTEND in tags and tags.count(T.POE_EXTEND) != poe_ports:
            return 3
        for tag, v in tlvs:
            if tag in system or tag in (T.USERNAME, T.PASSWORD):
                continue
            error = self._set_one(tag, v)
            if error:
                return error
        return 0

    def _set_one(self, tag: int, v: bytes) -> int:
        c = self.config
        if tag == T.LED:
            c["led"] = v[0] == 1
        elif tag == T.LOOP_PREVENTION:
            c["loop"] = v[0] == 1
        elif tag == T.IGMP:
            c["igmp"] = v[0] == 1
        elif tag == T.IGMP_REPORT_SUPPRESSION:
            c["igmp_suppress"] = v[0] == 1
        elif tag == T.SAVE:
            self.saved = copy.deepcopy(c)
            self.saves += 1
        elif tag == T.REBOOT:
            if v[:1] == b"\x01":
                self.saved = copy.deepcopy(c)
            self.config = copy.deepcopy(self.saved)
            self.offline_until = time.monotonic() + self.reboot_seconds
            self.tokens.clear()
            self.session_key = None
        elif tag == T.FACTORY_RESET:
            self._reset()
            self.saved = copy.deepcopy(self.config)
            self.offline_until = time.monotonic() + self.reboot_seconds
            self.session_key = None
        elif tag == T.NEW_PASSWORD:
            c["password"] = T.text(v)
        elif tag == T.NEW_USERNAME:
            c["username"] = T.text(v) or c["username"]
        elif tag == T.PORT_SETTINGS:
            port, enabled, _, speed, _, flow, _ = v[:7]
            if port not in c["port"] or speed not in range(1, 7):
                return 7503
            in_lag = any(port in m for m in c["lags"].values())
            if in_lag and speed in (2, 4):
                return 7407
            # flow control only sticks with auto or 1000M full
            c["port"][port] = {
                "enabled": enabled == 1,
                "speed": speed,
                "flow": bool(flow) and speed in (1, 6),
            }
        elif tag == T.TRUNK:
            group, members = T.group_mask(v, self.ports)
            if group not in c["lags"]:
                return 7503
            if members and len(members) == 1:
                return 7415
            if members and (len(members) > 4 or set(members) - set(self._lag_range(group))):
                return 4
            if any(p == c["mirror"][1] for p in members) and c["mirror"][0]:
                return 7205
            c["lags"][group] = members
        elif tag == T.MIRROR:
            mirror = T.mirror(v, self.ports)
            c["mirror"] = [
                1 if v[0] else 0,
                mirror["destination"],
                mirror["ingress"],
                mirror["egress"],
            ]
        elif tag == T.QOS_MODE:
            c["qos_mode"] = v[0]
        elif tag == T.QOS_PRIORITY:
            if c["qos_mode"] == 0 and v[0] in c["priority"]:
                c["priority"][v[0]] = min(v[1], 3)
        elif tag in (T.RATE_INGRESS, T.RATE_EGRESS):
            port, kbps = T.port_rate(v)
            c["ingress" if tag == T.RATE_INGRESS else "egress"][port] = kbps
            if tag == T.RATE_INGRESS and kbps:
                c["storm"][port] = [0, 0, 0, 0]  # one limiter per port
        elif tag == T.STORM:
            s = T.storm(v)
            if s["enabled"]:
                c["storm"][s["port"]] = [
                    s["unknown_unicast"],
                    s["multicast"],
                    s["broadcast"],
                    s["kbps"],
                ]
                c["ingress"][s["port"]] = 0
            else:
                c["storm"][s["port"]] = [0, 0, 0, 0]
        elif tag == T.POE_GLOBAL_RECOVERY and c["poe_recovery"]:
            c["poe_recovery_on"] = v[0] == 1
        elif tag == T.POE_RECOVERY and c["poe_recovery"]:
            row = T.poe_recovery(v)
            if row["port"] not in c["poe_recovery"]:
                return 7503
            c["poe_recovery"][row["port"]] = row
        elif tag == T.POE_EXTEND and c["poe_extend"]:
            if v[0] not in c["poe_extend"]:
                return 7503
            c["poe_extend"][v[0]] = v[1] == 1
        elif tag == T.MTU_VLAN:
            if not 1 <= v[1] <= self.ports:
                return 7503
            c["mtu_vlan"], c["mtu_uplink"] = v[0] == 1, v[1]
            if c["mtu_vlan"]:
                c.update(dot1q=False, port_vlan=False)
        elif tag == T.DOT1Q_ENABLED:
            if v[0] == 1 and not c["dot1q"]:
                all_ports = list(range(1, self.ports + 1))
                c.update(
                    dot1q=True,
                    mtu_vlan=False,
                    port_vlan=False,
                    vlans={1: {"members": all_ports, "tagged": [], "name": "Default_VLAN"}},
                    pvid=dict.fromkeys(all_ports, 1),
                )
            elif v[0] == 0:
                # the switch resets every PVID to 1 when 802.1Q is switched off
                c["dot1q"] = False
                c["pvid"] = dict.fromkeys(c["pvid"], 1)
        elif tag == T.PORT_VLAN_ENABLED:
            if v[0] == 1:
                c.update(
                    port_vlan=True,
                    dot1q=False,
                    mtu_vlan=False,
                    port_groups={1: list(range(1, self.ports + 1))},
                    pvid=dict.fromkeys(c["pvid"], 1),  # leaving 802.1Q resets the PVIDs
                )
            else:
                c["port_vlan"] = False
        elif tag == T.PORT_VLAN:
            group, members = T.group_mask(v, self.ports)
            if not 1 <= group <= 8:
                return 7503
            for other in c["port_groups"].values():  # membership is exclusive
                other[:] = [p for p in other if p not in members]
            if members:
                c["port_groups"][group] = members
            else:
                c["port_groups"].pop(group, None)
            used = {p for m in c["port_groups"].values() for p in m}
            c["port_groups"].setdefault(1, [])
            c["port_groups"][1] = sorted(
                set(c["port_groups"][1]) | (set(range(1, self.ports + 1)) - used)
            )
        elif tag == T.DOT1Q_VLAN:
            vlan = T.dot1q_vlan(v, self.ports)
            vid = vlan["id"]
            if not c["dot1q"]:
                return 0  # accepted and dropped
            if not vlan["members"]:
                if vid not in c["vlans"] or vid in c["pvid"].values():
                    return 7503
                del c["vlans"][vid]
            else:
                if vid not in c["vlans"] and len(c["vlans"]) >= 32:
                    return 7505
                c["vlans"][vid] = {k: vlan[k] for k in ("members", "tagged", "name")}
        elif tag == T.PVID:
            port, vid = v[0], int.from_bytes(v[1:3], "big")
            if vid not in c["vlans"] or port not in c["pvid"]:
                return 7503
            c["pvid"][port] = vid
        else:
            return 7503
        return 0

    # -- packets -------------------------------------------------------------

    def handle(self, request: Packet) -> Packet | None:
        if time.monotonic() < self.offline_until:
            return None
        if request.op == Op.DISCOVERY:
            return self._reply(request, Op.REPLY, self._identity())
        if request.switch_mac != self._mac:
            return None
        if request.op == Op.GET:
            if len(request.tlvs) != 1:
                return (
                    self._reply(request, Op.REPLY, [], error=1) if self.generation == "B" else None
                )
            tag = request.tlvs[0][0]
            if tag == T.TOKEN and (self.generation == "A" or self.session_token):
                token = random.randint(1, 0xFFFF)
                self.tokens.add(token)
                return self._reply(request, Op.REPLY, [], token=token)
            if tag in T.SECRET_TAGS:
                return None
            values = self._get(tag)
            return None if values is None else self._reply(request, Op.REPLY, values)
        if request.op == Op.LOGIN:
            tags = [t for t, _ in request.tlvs]
            if tags == [T.KEY_EXCHANGE]:
                if self.generation != "B" or self.ignore_keys:
                    return None
                raw = request.tlvs[0][1]
                cipher = int.from_bytes(raw[2:10], "little")
                self.session_key = pow(cipher, SIM_PRIVATE, SIM_MODULUS).to_bytes(8, "little")
                return self._reply(request, Op.RETURN, [], error=KEY_ACCEPTED)
            if self.generation == "A" or self.session_token:
                if request.token not in self.tokens:
                    return None  # a missing or spent token is dropped without an answer
                self.tokens.discard(request.token)
            values = dict(request.tlvs)
            user, password = values.get(T.USERNAME), values.get(T.PASSWORD)
            if (
                user is None
                or password is None
                or T.text(user) != self.config["username"]
                or T.text(password) != self.config["password"]
            ):
                return self._reply(request, Op.RETURN, [], error=7)
            rest = [(t, v) for t, v in request.tlvs if t not in (T.USERNAME, T.PASSWORD)]
            if rest:
                self.writes += 1
            error = self._set(rest)
            return self._reply(request, Op.RETURN, [], error=error)
        return None

    def _reply(self, request: Packet, op: Op, tlvs, error: int = 0, token: int = 0) -> Packet:
        return Packet(op, self._mac, request.host_mac, request.seq, error, token, tlvs)


class _SimProtocol(asyncio.DatagramProtocol):
    def __init__(self, sim: EasySmartSimulator):
        self.sim = sim

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        for switch in self.sim.switches:
            request, key = self._decode(switch, data)
            if request is None:
                continue
            reply = switch.handle(request)
            if reply is not None:
                encrypted = key or (switch.session_key if request.op == Op.LOGIN else None)
                raw = reply.encode_session(encrypted) if encrypted else reply.encode()
                if (
                    request.op == Op.LOGIN
                    and request.tlvs[:1]
                    and (request.tlvs[0][0] == T.KEY_EXCHANGE)
                ):
                    delay = switch.key_delay
                else:
                    delay = 0.0
                delay = delay or (
                    switch.write_delay
                    if request.op == Op.LOGIN
                    and request.tlvs[:1] != []
                    and request.tlvs[0][0] != T.KEY_EXCHANGE
                    else 0
                )
                if delay:
                    asyncio.get_running_loop().call_later(delay, self.transport.sendto, raw, addr)
                else:
                    self.transport.sendto(raw, addr)

    @staticmethod
    def _decode(switch: SimEasySmart, data: bytes) -> tuple[Packet | None, bytes | None]:
        try:
            plain = Packet.decode(data)
            if plain.op == Op.DISCOVERY or not switch.session_key:
                return plain, None
            if plain.tlvs[:1] and plain.tlvs[0][0] == T.KEY_EXCHANGE:
                return plain, None
        except ValueError:
            pass
        if switch.session_key:
            try:
                return Packet.decode(data, switch.session_key), switch.session_key
            except ValueError:
                pass
        return None, None


class EasySmartSimulator:
    def __init__(self, switches: list[SimEasySmart]):
        self.switches = switches
        self.port = 0
        self._transport = None

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _SimProtocol(self), local_addr=(host, port)
        )
        self.port = self._transport.get_extra_info("sockname")[1]
        return self.port

    def stop(self) -> None:
        if self._transport:
            self._transport.close()


def demo_easysmart() -> list[SimEasySmart]:
    return [
        SimEasySmart("50:c7:bf:5e:00:01", name="garage", ip="192.168.0.250"),
        SimEasySmart(
            "50:c7:bf:5e:00:02",
            model="TL-SG105E",
            hardware="TL-SG105E 5.0",
            firmware="1.0.0 Build 20230218 Rel.50633",
            generation="B",
            ports=5,
            name="attic",
            ip="192.168.0.251",
            auto_save=True,
            lag_groups=1,
        ),
    ]
