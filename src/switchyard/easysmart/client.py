"""One TP-Link Easy Smart switch: reads, authenticated writes, both protocol generations.

Older firmware ("generation A") takes a fresh token before every write; firmware from 2023 on
("generation B") first gets a session key by an RSA key exchange and then encrypts every
packet with it. Writes always carry user name and password. See docs/PROTOCOL-EASYSMART.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import string

from ..drivers.base import AuthError, PasswordRequired, SwitchDriver, SwitchError, Unreachable
from ..nsdp.packet import format_mac
from . import tags as T
from .packet import RSA_MODULUS, Op, Packet, key_exchange_value
from .transport import EasySmartTransport

ERRORS = {
    1: "The switch accepts only one setting per read.",
    -2: "The switch could not save the configuration or refused the read.",
    3: "Name, DHCP, IP address, netmask and gateway must be changed together.",
    4: "These ports cannot be in this link aggregation group.",
    6: "The IP address is not valid.",
    7: "Incorrect user name or password.",
    8: "Access denied.",
    9: "The netmask is not valid.",
    10: "The gateway is not valid.",
    7205: "A port of the group is the mirroring destination.",
    7206: "A port of the group is mirrored.",
    7401: "A port of the group is in a port-based VLAN other than the default.",
    7403: "A port of the group has a bandwidth limit.",
    7404: "A port of the group has storm control.",
    7405: "The ports of the group have different QoS priorities.",
    7406: "The ports of the group have different fixed speeds.",
    7407: "Half duplex is not allowed for this port (link aggregation).",
    7410: "The ports of the group mix automatic and fixed speeds.",
    7415: "A link aggregation group needs at least two ports.",
    7417: "The ports of the group have different flow control settings.",
    7418: "Flow control must be on or off for all ports of the group.",
    7503: "The switch refused this setting.",
    7505: "The 802.1Q VLAN table is full.",
}
AUTH_ERRORS = {7, 8}
# the same code means something else in the answer to a change
WRITE_ERRORS = {
    -2: "The switch refused this change; it may not support this combination of settings."
}
KEY_ACCEPTED = 4098
# A change is never resent (it could be applied twice), so wait long for its answer: some
# changes take the switch seconds (a VLAN mode switch rewrites the table) (#83).
WRITE_TIMEOUT = 5.0
# the key exchange: first contact (older firmware stays silent), and a switch known to take keys
KEY_PROBE_TIMEOUT = 2.0
KEY_TIMEOUT = 3.0
DEFAULT_USER = "admin"


SYSTEM_TAGS = {2, 4, 5, 6, 9}  # name, IP, netmask, gateway, DHCP


class EasySmartError(SwitchError):
    def __init__(self, code: int, tag: int = 0, write: bool = False):
        self.status = code
        self.tag = tag
        text = (WRITE_ERRORS.get(code) if write else None) or ERRORS.get(code)
        if code == 3 and tag not in SYSTEM_TAGS:
            text = "The switch wants related settings changed together and refused this change."
        super().__init__(text or f"The switch answered with error {code}.")


class EasySmartAuthError(EasySmartError, AuthError):
    pass


def parse_identity(packet: Packet) -> dict:
    def get(tag, convert, default=None):
        value = packet.first(tag)
        return convert(value) if value is not None else default

    return {
        "mac": format_mac(packet.switch_mac),
        "model": get(T.MODEL, T.text, ""),
        "name": get(T.DESCRIPTION, T.text, ""),
        "ip": get(T.IP, T.ipv4),
        "netmask": get(T.NETMASK, T.ipv4),
        "gateway": get(T.GATEWAY, T.ipv4),
        "dhcp": get(T.DHCP, lambda v: bool(v[0]) if v else False, False),
        "firmware": get(T.FIRMWARE, T.text, ""),
        "hardware": get(T.HARDWARE, T.text, ""),
    }


def _storm_types(rows: list[dict]) -> dict[str, bool]:
    """The frame types of the first port with storm control on; all three when none is on."""
    on = next((r for r in rows if r["enabled"]), None)
    if on is None:
        return {"unknown_unicast": True, "multicast": True, "broadcast": True}
    return {k: on[k] for k in ("unknown_unicast", "multicast", "broadcast")}


class EasySmartClient(SwitchDriver):
    protocol = "easysmart"
    vendor = "TP-Link"
    unicast = False
    needs_username = True

    def __init__(
        self,
        transport: EasySmartTransport,
        mac: bytes,
        password: str | None = None,
        username: str | None = None,
        rsa_modulus: int = RSA_MODULUS,
    ):
        self.transport = transport
        self.mac = mac
        self.address = None
        self.password = password
        self.username = username or DEFAULT_USER
        self.rsa_modulus = rsa_modulus
        self.lock = asyncio.Lock()
        self.generation: str | None = None  # "A" (token) or "B" (session key)
        # newer firmware that also wants a token per write (seen on a TL-SG108PE 5.0)
        self.session_token: bool | None = None
        # True once the switch accepted a session key: it is never treated as older firmware again
        self.session_capable: bool | None = None
        self.ports: int | None = None
        self.auto_save: bool | None = None
        self.unsupported: set[int] = set()
        self.rate_codes = dict(RATE_CODES)

    def _code(self, kbps: int) -> int:
        """The rate code for a limit read from the switch (adds unusual values to the table)."""
        for code, value in self.rate_codes.items():
            if value == kbps:
                return code
        code = max(self.rate_codes) + 1
        self.rate_codes[code] = kbps
        return code

    def rate_kbps(self, code: int) -> int:
        code = int(code)
        if code not in self.rate_codes:
            raise ValueError(f"unknown rate code {code}")
        return self.rate_codes[code]

    # -- raw access ----------------------------------------------------------

    async def _exchange(self, op: Op, tlvs, token: int = 0, **kw) -> Packet:
        try:
            parts = await self.transport.request(op, self.mac, tlvs, token=token, **kw)
        except TimeoutError as exc:
            raise Unreachable(f"The switch {format_mac(self.mac)} does not answer.") from exc
        first = parts[0]
        if len(parts) > 1:  # fragmented answer: one packet with all values
            first.tlvs = [tlv for p in parts for tlv in p.tlvs]
        return first

    async def _session(self) -> None:
        """Find out the protocol generation; on newer firmware set up the session key."""
        if self.generation:
            return
        alphabet = string.ascii_letters + string.digits
        key = "".join(alphabet[b % len(alphabet)] for b in os.urandom(8)).encode()
        # the answer already uses the session key: the transport must know it beforehand
        self.transport.sessions[self.mac] = key
        # A switch that accepted a key before gets more patience: its answer can take a second,
        # and silence then means "busy" or "dropped the session", not older firmware (#95).
        known = self.session_capable
        try:
            reply = await self.transport.request(
                Op.LOGIN,
                self.mac,
                [(T.KEY_EXCHANGE, key_exchange_value(key, self.rsa_modulus))],
                timeout=KEY_TIMEOUT if known else KEY_PROBE_TIMEOUT,
                retries=2 if known else 0,
                session=False,
            )
        except TimeoutError:
            self.transport.sessions.pop(self.mac, None)
            if known:
                raise Unreachable(
                    f"The switch {format_mac(self.mac)} does not answer the session key exchange."
                ) from None
            self.generation = "A"  # older firmware ignores the key exchange
            return
        if reply[0].error != KEY_ACCEPTED:
            self.transport.sessions.pop(self.mac, None)
            raise EasySmartError(reply[0].error, T.KEY_EXCHANGE)
        self.generation = "B"
        self.session_capable = True

    async def _get(self, tag: int, **kw) -> Packet:
        """Read one setting (the switch answers one per request, plus related ones)."""
        await self._session()
        try:
            reply = await self._exchange(Op.GET, [(tag, b"")], **kw)
        except Unreachable:
            if self.generation != "B":
                raise
            self.forget_session()  # the switch restarted and lost the session: once more
            await self._session()
            reply = await self._exchange(Op.GET, [(tag, b"")], **kw)
        if reply.error:
            raise EasySmartError(reply.error, tag)
        return reply

    async def _read(self, tag: int) -> Packet | None:
        """Like _get, but None when the switch does not have the setting."""
        if tag in self.unsupported:
            return None
        await self._port_count()  # the switch itself answers: silence below means "unknown"
        try:
            # firmware stays silent for settings it does not know: do not wait long for those
            reply = await self._get(tag, timeout=min(self.transport.timeout, 0.8), retries=1)
        except Unreachable:
            self.unsupported.add(tag)
            return None
        except EasySmartError:
            self.unsupported.add(tag)
            return None
        if not reply.has(tag):
            self.unsupported.add(tag)
            return None
        return reply

    def forget_session(self) -> None:
        self.generation = None
        self.session_token = None
        self.transport.sessions.pop(self.mac, None)

    async def _write(self, tlvs: list[tuple[int, bytes]], password: str | None = None) -> Packet:
        password = password if password is not None else self.password
        if not password:
            raise PasswordRequired("Enter the switch password first.")
        await self._session()
        token = await self._token()
        credentials = [(T.USERNAME, T.cstr(self.username)), (T.PASSWORD, T.cstr(password))]
        reply = await self._exchange(
            Op.LOGIN,
            credentials + tlvs,
            token,
            retries=0,
            timeout=max(self.transport.timeout, WRITE_TIMEOUT),
        )
        if reply.error in AUTH_ERRORS:
            raise EasySmartAuthError(reply.error)
        if reply.error:
            raise EasySmartError(reply.error, tlvs[0][0] if tlvs else 0, write=True)
        return reply

    async def _token(self) -> int:
        """A fresh token for the next write.

        Older firmware needs one for every write. Newer firmware with a session key may want one
        too: ask once with a short wait, and remember whether the switch hands out tokens.
        """
        if self.generation == "A":
            return (await self._exchange(Op.GET, [(T.TOKEN, b"")])).token
        if self.session_token is False:
            return 0
        try:
            reply = await self._exchange(
                Op.GET, [(T.TOKEN, b"")], timeout=min(self.transport.timeout, 0.8), retries=1
            )
        except Unreachable:
            self.session_token = False
            return 0
        self.session_token = True
        return reply.token

    async def write(self, tlvs: list[tuple[int, bytes]], save: bool = True) -> Packet:
        async with self.lock:
            reply = await self._write(tlvs)
            if save:
                await self._save()
            return reply

    async def _save(self) -> None:
        """Keep the change after a restart, unless the switch saves by itself."""
        if self.auto_save is None:
            reply = await self._read(T.AUTO_SAVE)
            value = reply.first(T.AUTO_SAVE) if reply else None
            self.auto_save = bool(value and value[0] == 1)
        if not self.auto_save:
            await self._write([(T.SAVE, b"")])

    async def login(self, password: str, username: str | None = None) -> None:
        """Check user name and password by a write that changes nothing."""
        async with self.lock:
            previous = self.username
            self.username = username or self.username or DEFAULT_USER
            try:
                await self._write([], password)
            except Exception:
                self.username = previous
                raise
            self.password = password

    # -- reading -------------------------------------------------------------

    async def _port_count(self) -> int:
        if self.ports is None:
            reply = await self._get(T.PORT_COUNT)
            count = reply.first(T.PORT_COUNT)
            self.ports = count[0] if count else 8
        return self.ports

    async def read_info(self) -> dict:
        async with self.lock:
            reply = await self._get(T.DESCRIPTION)  # answers with the whole identity
            info = parse_identity(reply)
            info["ports"] = await self._port_count()
            if reply.has(T.AUTO_SAVE):
                self.auto_save = reply.first(T.AUTO_SAVE)[:1] == b"\x01"
            info.update(
                password_mode="user and password",
                protocol_generation="token" if self.generation == "A" else "session key",
                serial=None,
                firmware2=None,
            )
            return info

    async def read_ports(self) -> list[dict]:
        async with self.lock:
            count = await self._port_count()
            reply = await self._get(T.PORT_STATS)
            rows = {p: {"port": p, **T.link(0)} for p in range(1, count + 1)}
            for value in reply.values(T.PORT_STATS):
                if len(value) >= 19 and value[0] in rows:
                    rows[value[0]].update(T.port_stats(value))
            for row in rows.values():
                # these switches count packets, not bytes, and have no CRC counter
                row.setdefault("rx_bytes", None)
                row.setdefault("tx_bytes", None)
                row.setdefault("crc_errors", None)
                row.setdefault("flow_control", None)
                if "rx_bad" in row:
                    row["errors"] = row["rx_bad"] + row["tx_bad"]
            return list(rows.values())

    async def _port_settings(self) -> dict[int, dict]:
        reply = await self._read(T.PORT_SETTINGS)
        if reply is None:
            return {}
        return {
            s["port"]: s
            for s in (T.port_settings(v) for v in reply.values(T.PORT_SETTINGS) if len(v) >= 7)
        }

    async def read_port_admin(self) -> dict | None:
        async with self.lock:
            settings = await self._port_settings()
        if not settings:
            return None
        return {port: self._admin_view(s) for port, s in settings.items()}

    @staticmethod
    def _admin_view(s: dict) -> dict:
        # shared codes: 0 auto, 1 disabled, 2..6 fixed speeds (same labels as NETGEAR)
        code = 1 if not s["enabled"] else (0 if s["speed_setting"] == 1 else s["speed_setting"])
        labels = {0: "auto", 1: "disabled"}
        return {
            "port": s["port"],
            "speed_code": code,
            "speed": labels.get(code) or T.SPEEDS.get(code, (f"code {code}",))[0],
            "flow_control": s["flow_setting"],
            "lag": s["lag"],
        }

    async def read_vlan(self) -> dict | None:
        count = await self._port_count()
        dot1q = await self._read(T.DOT1Q_ENABLED)
        port_based = await self._read(T.PORT_VLAN_ENABLED)
        mtu = await self._read(T.MTU_VLAN)
        if dot1q is None and port_based is None:
            return None
        out = {
            "mode": "disabled",
            "max_vlans": None,
            "port_vlans": [],
            "dot1q": [],
            "pvid": {},
            # one port-based and one 802.1Q mode (no basic/advanced as on NETGEAR), and MTU VLAN
            # VLANs cannot be switched off: the default is port-based with one group (#96)
            "modes": ["port_basic", "dot1q_advanced"] + (["mtu"] if mtu else []),
            # a port is in exactly one port-based group; group 1 holds the ports of no other
            "exclusive_groups": True,
            # the switch resets every PVID to 1 when 802.1Q is switched off
            "pvid_reset_on_disable": True,
        }
        mtu_value = mtu.first(T.MTU_VLAN) if mtu else None
        if mtu_value and len(mtu_value) >= 2:
            out["mtu_uplink"] = mtu_value[1]
            if mtu_value[0] == 1:
                out["mode"] = "mtu"
                return out
        if dot1q is not None and (dot1q.first(T.DOT1Q_ENABLED) or b"\x00")[:1] == b"\x01":
            out["mode"] = "dot1q_advanced"
            vlans = await self._get(T.DOT1Q_VLAN)
            out["dot1q"] = [
                T.dot1q_vlan(v, count) for v in vlans.values(T.DOT1Q_VLAN) if len(v) >= 10
            ]
            limit = vlans.first(T.VLAN_LIMITS)
            pvids = await self._get(T.PVID)
            out["pvid"] = {
                v[0]: int.from_bytes(v[1:3], "big") for v in pvids.values(T.PVID) if len(v) >= 3
            }
            limit = limit or pvids.first(T.VLAN_LIMITS)
            out["max_vlans"] = limit[0] if limit else 32
        elif (
            port_based is not None
            and (port_based.first(T.PORT_VLAN_ENABLED) or b"\x00")[:1] == b"\x01"
        ):
            out["mode"] = "port_basic"
            groups = [T.group_mask(v, count) for v in port_based.values(T.PORT_VLAN) if len(v) >= 5]
            out["port_vlans"] = [{"id": g, "members": m} for g, m in groups if m]
            limit = port_based.first(T.PORT_VLAN_MAX)
            out["max_vlans"] = limit[0] if limit else 8
        return out

    async def read_qos(self) -> dict | None:
        mode = await self._read(T.QOS_MODE)
        if mode is None:
            return None
        raw = mode.first(T.QOS_MODE)
        priorities = await self._read(T.QOS_PRIORITY)
        ingress = await self._read(T.RATE_INGRESS)
        egress = await self._read(T.RATE_EGRESS)
        storm = await self._read(T.STORM)

        def rates(reply, tag):
            if reply is None:
                return {}
            pairs = (T.port_rate(v) for v in reply.values(tag) if len(v) >= 6)
            return {port: self._code(kbps) for port, kbps in pairs}

        storm_rows = [T.storm(v) for v in storm.values(T.STORM) if len(v) >= 9] if storm else []
        return {
            "mode": T.QOS_MODES.get(raw[0], "port") if raw else "port",
            "priorities": {
                v[0]: T.PRIORITIES.get(v[1], "normal")
                for v in (priorities.values(T.QOS_PRIORITY) if priorities else [])
                if len(v) >= 2
            },
            "ingress": rates(ingress, T.RATE_INGRESS),
            "egress": rates(egress, T.RATE_EGRESS),
            "storm_control": any(r["enabled"] for r in storm_rows) if storm else None,
            "storm_rates": {r["port"]: self._code(r["kbps"]) for r in storm_rows},
            # frame types storm control counts (the same for every port in Switchyard)
            "storm_types": _storm_types(storm_rows) if storm else None,
            # per port, as the switch keeps it: rate (0 = off) and the frame types (#111)
            "storm_ports": {
                r["port"]: {
                    "rate": self._code(r["kbps"]) if r["enabled"] else 0,
                    "unknown_unicast": r["unknown_unicast"],
                    "multicast": r["multicast"],
                    "broadcast": r["broadcast"],
                }
                for r in storm_rows
            }
            if storm
            else None,
            "modes": list(T.QOS_MODES.values()),
        }

    async def read_lag(self) -> dict | None:
        count = await self._port_count()
        reply = await self._read(T.TRUNK)
        if reply is None:
            return None
        groups = [T.group_mask(v, count) for v in reply.values(T.TRUNK) if len(v) >= 5]
        if not groups:
            return None
        half = (count + 1) // 2
        ranges = (
            {1: list(range(1, half + 1)), 2: list(range(half + 1, count + 1))}
            if len(groups) == 2
            else {}
        )
        return {
            "groups": len(groups),
            "max_members": 4,
            "lags": [{"id": g, "enabled": bool(m), "members": m} for g, m in groups],
            "ranges": ranges,
        }

    async def read_features(self) -> dict:
        count = await self._port_count()
        mirror = await self._read(T.MIRROR)
        igmp = await self._read(T.IGMP)
        loop = await self._read(T.LOOP_PREVENTION)
        led = await self._read(T.LED)
        mirror_value = mirror.first(T.MIRROR) if mirror else None
        igmp_value = igmp.first(T.IGMP) if igmp else None
        suppression = igmp.first(T.IGMP_REPORT_SUPPRESSION) if igmp else None
        loop_value = loop.first(T.LOOP_PREVENTION) if loop else None
        led_value = led.first(T.LED) if led else None
        return {
            # these switches mirror one source port (received, sent or both) (#86)
            "mirror": {**T.mirror(mirror_value, count), "max_sources": 1}
            if mirror_value and len(mirror_value) >= 10
            else None,
            "igmp": None
            if not igmp_value
            else {
                "enabled": igmp_value[0] == 1,
                "vlan": None,
                "report_suppression": bool(suppression and suppression[0] == 1),
                "block_unknown": None,
                "validate_v3": None,
            },
            "loop_detection": bool(loop_value[0]) if loop_value else None,
            "led": bool(led_value[0]) if led_value else None,
        }

    async def read_state(self) -> dict:
        info = await self.read_info()
        async with self.lock:
            features = await self.read_features()
            state = {
                "info": info,
                "vlan": await self.read_vlan(),
                "qos": await self.read_qos(),
                "lag": await self.read_lag(),
                **features,
            }
        state["ports"] = await self.read_ports()
        state["port_admin"] = await self.read_port_admin()
        state.update(
            rate_codes=self.rate_codes,
            admin_speeds={0: "auto", 1: "disabled", **{c: T.SPEEDS[c][0] for c in range(2, 7)}},
            poe=await self.read_poe(),
            # no protocol layout is known for these on Easy Smart switches
            capabilities={"firmware_upgrade": False, "cable_test": False},
        )
        return state

    async def _peek(self, tag: int) -> Packet | None:
        """Read a setting the switch sometimes does not answer (not remembered as unsupported):
        the TL-SG108PE stays silent on its PoE recovery and extend settings while they are on."""
        try:
            reply = await self._get(tag, timeout=min(self.transport.timeout, 0.8), retries=1)
        except (Unreachable, EasySmartError):
            return None
        return reply if reply.has(tag) else None

    async def read_poe(self) -> dict | None:
        """PoE auto recovery and extend mode (#114); None on switches without PoE."""
        async with self.lock:
            recovery = await self._read(T.POE_RECOVERY)
            if recovery is None:
                return None
            rows = [T.poe_recovery(v) for v in recovery.values(T.POE_RECOVERY) if len(v) >= 20]
            # the global switch comes along with the recovery rows (TL-SG108PE, #119)
            glob_value = recovery.first(T.POE_GLOBAL_RECOVERY)
            if glob_value is None:
                glob = await self._peek(T.POE_GLOBAL_RECOVERY)
                glob_value = glob.first(T.POE_GLOBAL_RECOVERY) if glob else None
            extend = await self._peek(T.POE_EXTEND)
        extend_rows = extend.values(T.POE_EXTEND) if extend else []
        ports = [r["port"] for r in rows]
        return {
            "ports": ports,
            # None: the switch did not answer
            "recovery_enabled": (glob_value[0] == 1) if glob_value else None,
            "recovery": {r["port"]: r for r in rows},
            "extend": {
                p: next((v[1] == 1 for v in extend_rows if len(v) >= 2 and v[0] == p), None)
                for p in ports
            },
        }

    async def set_poe(self, settings: dict) -> None:
        """``{"recovery_enabled": bool, "recovery": {port: {...}}, "extend": {port: bool}}``.

        The switch takes these as groups (#119): the global recovery switch together with the
        recovery rows of every PoE port in one change, and the extend mode of every PoE port in
        another. A single port's row alone is refused with error 3.
        """
        current = await self.read_poe()
        if current is None:
            self._unsupported("PoE settings")
        ports = current["ports"]
        recovery = settings.get("recovery") or {}
        extend = settings.get("extend") or {}
        for port in [*map(int, recovery), *map(int, extend)]:
            if port not in ports:
                raise ValueError(f"Port {port} has no PoE.")
        if settings.get("recovery_enabled") is not None or recovery:
            glob = settings.get("recovery_enabled")
            if glob is None:
                glob = bool(current["recovery_enabled"])
            tlvs = [(T.POE_GLOBAL_RECOVERY, bytes([1 if glob else 0]))]
            for port in ports:
                row = {
                    **current["recovery"][port],
                    **recovery.get(port, recovery.get(str(port), {})),
                }
                row["port"] = port
                ipaddress.IPv4Address(row["ip"])  # raises ValueError for a bad address
                for key, low, high in (
                    ("startup_delay", 0, 65535),
                    ("interval", 1, 65535),
                    ("threshold", 1, 65535),
                    ("break_time", 0, 65535),
                ):
                    if not low <= int(row[key]) <= high:
                        name = key.replace("_", " ")
                        raise ValueError(f"Port {port}: {name} must be {low} to {high}.")
                if row["enabled"] and row["ip"] == "0.0.0.0":
                    raise ValueError(f"Port {port}: auto recovery needs an address to ping.")
                tlvs.append((T.POE_RECOVERY, T.pack_poe_recovery(row)))
            await self.write(tlvs, save=False)
        if extend:
            merged = {p: bool(current["extend"].get(p)) for p in ports}
            merged.update({int(p): bool(on) for p, on in extend.items()})
            await self.write(
                [(T.POE_EXTEND, bytes([p, 1 if on else 0])) for p, on in sorted(merged.items())],
                save=False,
            )
        async with self.lock:
            await self._save()

    async def read_part(self, part: str):
        if part in ("vlan", "qos", "lag", "features"):
            async with self.lock:  # these readers expect the caller to hold the lock
                return await super().read_part(part)
        return await super().read_part(part)

    # -- diagnostics ---------------------------------------------------------

    def diagnostic_tags(self) -> list[int]:
        return [t for t in sorted(T.NAMES) if t not in T.SECRET_TAGS | T.ACTION_TAGS]

    def tag_allowed(self, tag: int) -> bool:
        return 0 < tag < 0xFFFF and tag not in T.SECRET_TAGS | T.ACTION_TAGS

    async def scan_tags(self, tags: list[int], timeout: float = 0.8) -> list[dict]:
        out = []
        async with self.lock:
            await self._session()
            for tag in tags:
                row = {"tag": tag, "name": T.NAMES.get(tag), "answered": False, "values": []}
                try:
                    reply = await self._exchange(Op.GET, [(tag, b"")], timeout=timeout, retries=0)
                    row["status"] = reply.error
                    row["answered"] = reply.error == 0 and reply.has(tag)
                    row["values"] = [v.hex() for v in reply.values(tag)][:16]
                    if tag == T.TOKEN:
                        row["values"] = [f"token {reply.token}"]
                    others = sorted({t for t, _ in reply.tlvs if t != tag})
                    if others:
                        row["also"] = others
                except Unreachable:
                    row["status"] = None
                out.append(row)
        return out

    # -- changing settings ---------------------------------------------------

    async def set_network(
        self,
        name: str | None = None,
        dhcp: bool | None = None,
        ip: str | None = None,
        netmask: str | None = None,
        gateway: str | None = None,
    ) -> None:
        # the switch wants name, DHCP, IP, netmask and gateway in one write
        async with self.lock:
            current = parse_identity(await self._get(T.DESCRIPTION))
        name = current["name"] if name is None else name
        if dhcp is None:
            dhcp = current["dhcp"]
        if not dhcp:
            ip, netmask = ip or current["ip"], netmask or current["netmask"]
            gateway = gateway or current["gateway"] or "0.0.0.0"
            if not (ip and netmask):
                raise ValueError("A static setup needs an IP address and a netmask.")
            network = ipaddress.IPv4Interface(f"{ip}/{netmask}").network
            if gateway != "0.0.0.0" and ipaddress.IPv4Address(gateway) not in network:
                raise ValueError("The gateway must be in the same subnet as the switch.")
        else:
            ip = netmask = gateway = "0.0.0.0"
        if len(name) > 32 or (
            name and not all(c.isascii() and (c.isalnum() or c == "-") for c in name)
        ):
            raise ValueError("The name has at most 32 letters, digits and hyphens.")
        await self.write(
            [
                (T.DESCRIPTION, T.cstr(name)),
                (T.DHCP, bytes([1 if dhcp else 0])),
                (T.IP, T.pack_ipv4(ip)),
                (T.NETMASK, T.pack_ipv4(netmask)),
                (T.GATEWAY, T.pack_ipv4(gateway)),
            ]
        )

    async def set_vlan_mode(self, mode: str, uplink: int | None = None) -> None:
        if mode == "mtu":
            # MTU VLAN: every port talks to the uplink port only (layout from the protocol
            # notes, writing it is not verified on hardware yet)
            count = await self._port_count()
            if uplink is None or not 1 <= uplink <= count:
                raise ValueError(f"MTU VLAN needs an uplink port, 1 to {count}.")
            await self.write([(T.MTU_VLAN, bytes([1, uplink]))])
            return
        current = await self.read_vlan() or {}
        if current.get("mode") == "mtu":  # leave MTU VLAN first
            await self.write([(T.MTU_VLAN, bytes([0, current.get("mtu_uplink") or 1]))], save=False)
        if mode.startswith("dot1q"):
            await self.write([(T.DOT1Q_ENABLED, b"\x01")])
        elif mode.startswith("port"):
            await self.write([(T.PORT_VLAN_ENABLED, b"\x01")])
        elif mode == "disabled":
            # VLANs cannot be switched off on these switches: their default is port-based mode
            # with every port in group 1, which separates nothing (#96)
            await self.write([(T.PORT_VLAN_ENABLED, b"\x01")])
        else:
            raise ValueError(f"unknown VLAN mode {mode}")

    async def set_port_vlans(self, vlans: list[dict]) -> None:
        current = await self.read_vlan() or {}
        wanted = {int(v["id"]): sorted(v["members"]) for v in vlans}
        for old in current.get("port_vlans", []):
            if old["id"] not in wanted and old["id"] != 1:
                await self.write([(T.PORT_VLAN, T.pack_group_mask(old["id"], []))], save=False)
        for gid, members in sorted(wanted.items()):
            if gid == 1:
                continue  # group 1 holds every port of no other group: it follows by itself
            await self.write([(T.PORT_VLAN, T.pack_group_mask(gid, members))], save=False)
        async with self.lock:
            await self._save()

    async def apply_dot1q(self, vlans: list[dict], pvid: dict[int, int]) -> None:
        """Bring the 802.1Q table to ``vlans`` and the PVIDs to ``pvid``, one VLAN per write."""
        count = await self._port_count()
        current = await self.read_vlan()
        if current is None:
            raise SwitchError("This switch has no 802.1Q VLANs.")
        if current["mode"] != "dot1q_advanced":
            await self.write([(T.DOT1Q_ENABLED, b"\x01")], save=False)
            current = await self.read_vlan() or current
        have = {v["id"]: v for v in current["dot1q"]}
        want = {int(v["id"]): v for v in vlans}
        if len(want) > (current.get("max_vlans") or 32):
            raise ValueError(f"This switch has room for {current.get('max_vlans')} VLANs.")
        for p, vid in pvid.items():
            if not 1 <= int(p) <= count:
                raise ValueError(f"Port {p} does not exist.")
            if int(vid) not in want:
                raise ValueError(f"PVID {vid} of port {p} is not a VLAN in the table.")
        # 1) add and change VLANs, 2) move PVIDs, 3) delete VLANs nobody uses any more
        for vid, v in sorted(want.items()):
            members, tagged = sorted(map(int, v["members"])), sorted(map(int, v.get("tagged", [])))
            old = have.get(vid)
            if old and old["members"] == members and old["tagged"] == tagged:
                continue
            name = v.get("name") or (old or {}).get("name") or ""
            await self.write(
                [(T.DOT1Q_VLAN, T.pack_dot1q_vlan(vid, members, tagged, name))], save=False
            )
        changed = {
            int(p): int(vid) for p, vid in pvid.items() if current["pvid"].get(int(p)) != int(vid)
        }
        if changed:
            await self.write(
                [(T.PVID, bytes([p]) + v.to_bytes(2, "big")) for p, v in sorted(changed.items())],
                save=False,
            )
        for vid in sorted(set(have) - set(want)):
            if vid != 1:
                await self.write([(T.DOT1Q_VLAN, T.pack_dot1q_vlan(vid, [], [], ""))], save=False)
        async with self.lock:
            await self._save()

    async def delete_vlan(self, vid: int) -> None:
        await self.write([(T.DOT1Q_VLAN, T.pack_dot1q_vlan(vid, [], [], ""))])

    async def set_qos(
        self,
        mode: str | None = None,
        priorities: dict[int, str] | None = None,
        ingress: dict[int, int] | None = None,
        egress: dict[int, int] | None = None,
        storm_control: bool | None = None,
        storm_rates: dict[int, int] | None = None,
        storm_types: dict[str, bool] | None = None,
        storm_ports: dict[int, dict] | None = None,
    ) -> None:
        """Rates here are codes of ``RATE_CODES`` like on NETGEAR; the switch takes kbit/s."""
        if storm_ports:
            count = await self._port_count()
            writes = []
            for p, cfg in sorted((int(k), v) for k, v in storm_ports.items()):
                if not 1 <= p <= count:
                    raise ValueError(f"Port {p} does not exist.")
                kbps = self.rate_kbps(cfg.get("rate", 0))
                flags = {
                    "unicast": cfg.get("unknown_unicast", True),
                    "multicast": cfg.get("multicast", True),
                    "broadcast": cfg.get("broadcast", True),
                }
                if kbps and not any(flags.values()):
                    raise ValueError(f"Port {p}: choose at least one frame type for storm control.")
                writes.append((p, T.pack_storm(p, kbps, **flags)))
            for _, value in writes:
                await self.write([(T.STORM, value)], save=False)
        modes = {v: k for k, v in T.QOS_MODES.items()}
        prio = {v: k for k, v in T.PRIORITIES.items()}
        if mode is not None:
            if mode not in modes:
                raise ValueError(f"unknown QoS mode {mode}")
            await self.write([(T.QOS_MODE, bytes([modes[mode]]))], save=False)
        if priorities:
            count = await self._port_count()
            current = (await self.read_qos() or {}).get("priorities", {})
            merged = {p: current.get(p, "low") for p in range(1, count + 1)}
            merged.update({int(p): v for p, v in priorities.items()})
            # the vendor tool always writes every port
            await self.write(
                [(T.QOS_PRIORITY, bytes([p, prio[v]])) for p, v in sorted(merged.items())],
                save=False,
            )
        for tag, table in ((T.RATE_INGRESS, ingress), (T.RATE_EGRESS, egress)):
            for p, code in sorted((table or {}).items()):
                kbps = self.rate_kbps(code)
                await self.write([(tag, T.pack_port_rate(int(p), kbps))], save=False)
        if storm_control is not None or storm_rates or storm_types is not None:
            count = await self._port_count()
            current = await self.read_qos() or {}
            rates = storm_rates or {}
            types = {**(current.get("storm_types") or {}), **(storm_types or {})}
            flags = {
                "unicast": types.get("unknown_unicast", True),
                "multicast": types.get("multicast", True),
                "broadcast": types.get("broadcast", True),
            }
            if storm_types is not None and not any(flags.values()):
                raise ValueError("Choose at least one frame type for storm control.")
            for p in range(1, count + 1):
                if storm_control is False:
                    kbps = 0
                elif p in rates or str(p) in rates:
                    kbps = self.rate_kbps(rates.get(p, rates.get(str(p))))
                elif storm_types is not None and current.get("storm_rates", {}).get(p):
                    kbps = self.rate_kbps(current["storm_rates"][p])  # new types, same rate
                else:
                    continue
                await self.write([(T.STORM, T.pack_storm(p, kbps, **flags))], save=False)
        async with self.lock:
            await self._save()

    async def set_mirror(
        self,
        destination: int,
        sources: list[int],
        ingress: list[int] | None = None,
        egress: list[int] | None = None,
    ) -> None:
        """Mirror ``sources`` both ways, or received (ingress) and sent (egress) traffic apart."""
        if ingress is not None or egress is not None:
            ingress, egress = sorted(set(ingress or [])), sorted(set(egress or []))
            sources = sorted(set(ingress) | set(egress))
        else:
            ingress = egress = sources
        count = await self._port_count()
        if destination > count:
            raise ValueError(f"The destination port must be 1 to {count}.")
        if destination and destination in sources:
            raise ValueError("The destination port cannot be a source port.")
        if destination and not sources:
            raise ValueError("Select at least one source port.")
        if any(not 1 <= p <= count for p in sources):
            raise ValueError(f"Source ports must be 1 to {count}.")
        if len(sources) > 1:
            raise ValueError("This switch mirrors one source port only.")
        await self.write([(T.MIRROR, T.pack_mirror(destination, ingress, egress))])

    async def set_igmp(
        self,
        enabled: bool | None = None,
        vlan: int = 1,
        block_unknown: bool | None = None,
        validate_v3: bool | None = None,
        report_suppression: bool | None = None,
    ) -> None:
        if block_unknown is not None or validate_v3 is not None:
            raise SwitchError("This switch has only IGMP snooping and report suppression.")
        current = (await self.read_features())["igmp"] or {}
        on = current.get("enabled", False) if enabled is None else enabled
        suppress = (
            current.get("report_suppression", False)
            if report_suppression is None
            else report_suppression
        )
        await self.write(
            [(T.IGMP, bytes([1 if on else 0])), (T.IGMP_REPORT_SUPPRESSION, bytes([suppress]))]
        )

    async def set_loop_detection(self, enabled: bool) -> None:
        await self.write([(T.LOOP_PREVENTION, bytes([1 if enabled else 0]))])

    async def set_led(self, enabled: bool) -> None:
        await self.write([(T.LED, bytes([1 if enabled else 0]))])

    async def apply_lags(self, lags: list[dict]) -> None:
        current = await self.read_lag()
        if current is None:
            raise SwitchError("This switch has no link aggregation.")
        ranges = current["ranges"]
        have = {x["id"]: x for x in current["lags"]}
        wanted = {int(x["id"]): x for x in lags}
        for gid, x in wanted.items():
            members = sorted(map(int, x["members"])) if x.get("enabled") else []
            if gid not in have:
                raise ValueError(f"The LAG number must be 1 to {current['groups']}.")
            if members and not 2 <= len(members) <= 4:
                raise ValueError("A LAG has 2 to 4 member ports on this switch.")
            if members and ranges and set(members) - set(ranges[gid]):
                first, last = ranges[gid][0], ranges[gid][-1]
                raise ValueError(f"LAG {gid} can only use ports {first} to {last}.")
        for gid, old in sorted(have.items()):  # first remove, then add, like the vendor tool
            new = wanted.get(gid)
            members = sorted(map(int, new["members"])) if new and new.get("enabled") else []
            if old["members"] and old["members"] != members:
                await self.write([(T.TRUNK, T.pack_group_mask(gid, []))], save=False)
        for gid, new in sorted(wanted.items()):
            members = sorted(map(int, new["members"])) if new.get("enabled") else []
            if members and have[gid]["members"] != members:
                await self.write([(T.TRUNK, T.pack_group_mask(gid, members))], save=False)
        async with self.lock:
            await self._save()

    async def set_port_admin(self, settings: dict[int, dict]) -> None:
        count = await self._port_count()
        async with self.lock:
            current = await self._port_settings()
        tlvs = []
        for port, setting in sorted(settings.items()):
            if not 1 <= port <= count:
                raise ValueError(f"Port {port} does not exist.")
            code = int(setting["speed_code"])
            flow = bool(setting.get("flow_control"))
            if code == 1:  # disabled: keep the speed setting
                speed = current.get(port, {}).get("speed_setting", 1) or 1
                tlvs.append((T.PORT_SETTINGS, T.pack_port_settings(port, False, speed, flow)))
            else:
                speed = 1 if code == 0 else code
                if flow and speed not in (1, 6):
                    raise ValueError(
                        f"Port {port}: flow control works only with auto or 1G full on this switch."
                    )
                tlvs.append((T.PORT_SETTINGS, T.pack_port_settings(port, True, speed, flow)))
        if tlvs:
            await self.write(tlvs)

    async def reboot(self) -> None:
        async with self.lock:
            with contextlib.suppress(Unreachable):  # it may restart before it answers
                await self._write([(T.REBOOT, b"\x01")])
            self.forget_session()

    async def factory_reset(self) -> None:
        async with self.lock:
            with contextlib.suppress(Unreachable):
                await self._write([(T.FACTORY_RESET, b"")])
            self.forget_session()

    async def change_password(self, new_password: str) -> None:
        if not 1 <= len(new_password) <= 16 or not new_password.isascii():
            raise ValueError("The password has 1 to 16 characters (ASCII).")
        async with self.lock:
            await self._write(
                [
                    (T.PASSWORD, T.cstr(self.password or "")),
                    (T.NEW_USERNAME, T.cstr(self.username)),
                    (T.NEW_PASSWORD, T.cstr(new_password)),
                ]
            )
            self.password = new_password
            await self._save()


# the rate choices offered for TP-Link (kbit/s, multiples of 64); same codes as NETGEAR
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
    12: 1000000,
}
