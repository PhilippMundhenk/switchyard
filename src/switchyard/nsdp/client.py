"""One switch: typed reads and authenticated writes on top of the transport."""

from __future__ import annotations

import asyncio
import ipaddress

from . import tags as T
from .auth import PasswordMode, credential_tlv, mode_from_flags, new_password_tlv
from .packet import Op, Packet, format_mac
from .transport import Transport

STATUS_TEXT = {
    1: "The firmware does not support this protocol version.",
    2: "The switch does not support this command.",
    3: "The switch does not support this setting.",
    4: "The switch rejected the length of a value.",
    5: "The switch rejected a value.",
    6: "An access list on the switch does not allow this manager.",
    7: "Incorrect password.",
    8: "The switch is in boot mode and waits for a firmware download.",
    9: "Incorrect user name.",
    10: "This switch is configured through its own web interface only.",
    13: "Incorrect password.",
    14: "The switch is locked after 3 failed logins. Wait 30 minutes and try again.",
    15: "Plus Utility management is disabled on this switch. Enable it in the switch's web "
    "interface.",
}
AUTH_STATUSES = {7, 9, 13, 14}

DISCOVERY_TAGS = [T.MODEL, T.NAME, T.MAC, T.IP, T.NETMASK, T.GATEWAY, T.DHCP, T.FIRMWARE]


class NsdpError(Exception):
    def __init__(self, status: int, tag: int = 0, message: str | None = None):
        self.status = status
        self.tag = tag
        text = message or STATUS_TEXT.get(status, f"The switch answered with error {status}.")
        if tag and status not in AUTH_STATUSES:
            text += f" (setting: {T.NAMES.get(tag, f'0x{tag:04x}')})"
        super().__init__(text)


class AuthError(NsdpError):
    """The switch refused the password."""


class PasswordRequired(Exception):
    """A write was attempted without a stored password."""


class Unreachable(Exception):
    """The switch did not answer."""


def _empty(tag_list: list[int]) -> list[tuple[int, bytes]]:
    return [(t, b"") for t in tag_list]


def parse_identity(packet: Packet, address: str | None = None) -> dict:
    """The fields every switch reports about itself."""

    def get(tag, convert):
        value = packet.first(tag)
        return convert(value) if value else None

    return {
        "mac": format_mac(packet.device_mac),
        "model": get(T.MODEL, T.text),
        "name": get(T.NAME, T.text) or "",
        "ip": get(T.IP, T.ipv4),
        "netmask": get(T.NETMASK, T.ipv4),
        "gateway": get(T.GATEWAY, T.ipv4),
        "dhcp": get(T.DHCP, T.flag) or False,
        "firmware": get(T.FIRMWARE, T.text),
        "source_ip": address,
    }


async def discover(transport: Transport, timeout: float = 2.0) -> list[dict]:
    replies = await transport.collect(Op.READ_REQUEST, _empty(DISCOVERY_TAGS), timeout)
    return [parse_identity(packet, addr[0]) for packet, addr in replies]


async def probe(transport: Transport, address: str, timeout: float = 1.5) -> dict | None:
    """Ask the switch at one IP address who it is (no broadcast needed)."""
    seq = transport._next_seq()
    future: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_reply(packet: Packet, addr: tuple) -> None:
        if not future.done():
            future.set_result((packet, addr))

    transport._waiters[seq] = on_reply
    try:
        packet = Packet(Op.READ_REQUEST, seq=seq, tlvs=_empty(DISCOVERY_TAGS))
        for _ in range(2):
            transport._send(packet, address)
            try:
                reply, addr = await asyncio.wait_for(asyncio.shield(future), timeout)
            except TimeoutError:
                continue
            return parse_identity(reply, addr[0])
    finally:
        del transport._waiters[seq]
    return None


class SwitchClient:
    def __init__(
        self,
        transport: Transport,
        mac: bytes,
        address: str | None = None,
        password: str | None = None,
    ):
        self.transport = transport
        self.mac = mac
        self.address = address
        self.password = password
        self.lock = asyncio.Lock()
        self.unsupported: set[int] = set()
        self.ports: int | None = None
        self.model: str = ""
        self.password_mode: PasswordMode | None = None

    # -- raw access ----------------------------------------------------------

    async def _exchange(self, op: Op, tlvs: list[tuple[int, bytes]]) -> Packet:
        try:
            return await self.transport.request(op, self.mac, tlvs, self.address)
        except TimeoutError as exc:
            raise Unreachable(f"The switch {format_mac(self.mac)} does not answer.") from exc

    async def _read(self, request: list[tuple[int, bytes]]) -> Packet:
        """Read, dropping settings the switch reports as unsupported."""
        request = [r for r in request if r[0] not in self.unsupported]
        while request:
            reply = await self._exchange(Op.READ_REQUEST, request)
            if reply.status == 0:
                return reply
            wanted = {t for t, _ in request}
            if reply.status in (2, 3) and reply.failed_tag in wanted:
                self.unsupported.add(reply.failed_tag)
                request = [r for r in request if r[0] != reply.failed_tag]
                continue
            raise NsdpError(reply.status, reply.failed_tag)
        return Packet(Op.READ_RESPONSE, device_mac=self.mac)

    async def read(self, request: list[int | tuple[int, bytes]]) -> Packet:
        """Read several settings; survives switches that ignore unknown tags.

        Some firmware does not answer at all when a request holds a tag it
        does not know. If a group times out but the switch is alive, the
        group is split until the offending tags are found.
        """
        items = [(r, b"") if isinstance(r, int) else r for r in request]
        try:
            return await self._read(items)
        except Unreachable:
            if len(items) <= 1:
                raise
        await self._exchange(Op.READ_REQUEST, [(T.MODEL, b"")])  # alive at all?
        merged = Packet(Op.READ_RESPONSE, device_mac=self.mac)
        for item in items:
            try:
                merged.tlvs += (await self._read([item])).tlvs
            except Unreachable:
                self.unsupported.add(item[0])
        return merged

    async def _credentials(self, password: str) -> tuple[int, bytes]:
        if self.password_mode is None:
            try:
                reply = await self._read([(T.PASSWORD_MODE, b"")])
            except Unreachable:
                # old firmware stays silent for tags it does not know: no scheme tag, plain
                reply = Packet(Op.READ_RESPONSE, device_mac=self.mac)
            self.password_mode = mode_from_flags(reply.first(T.PASSWORD_MODE))
        salt = None
        if self.password_mode.needs_salt:
            salt = (await self._read([(T.PASSWORD_SALT, b"")])).first(T.PASSWORD_SALT)
        return credential_tlv(self.password_mode, password, self.mac, salt)

    async def _write(self, tlvs: list[tuple[int, bytes]], password: str | None = None) -> None:
        password = self.password if password is None else password
        if password is None:
            raise PasswordRequired("The switch password is needed for this change.")
        reply = await self._exchange(Op.WRITE_REQUEST, [await self._credentials(password), *tlvs])
        if reply.status in AUTH_STATUSES:
            raise AuthError(reply.status, reply.failed_tag)
        if reply.status:
            raise NsdpError(reply.status, reply.failed_tag)

    async def write(self, tlvs: list[tuple[int, bytes]]) -> None:
        async with self.lock:
            await self._write(tlvs)

    async def login(self, password: str) -> None:
        """Check a password by sending a write that changes nothing."""
        async with self.lock:
            await self._write([], password)
            self.password = password

    # -- reading state -------------------------------------------------------

    async def _port_count(self) -> int:
        if self.ports is None:
            reply = await self.read([T.PORT_COUNT, T.MODEL])
            count = reply.first(T.PORT_COUNT)
            self.model = T.text(reply.first(T.MODEL) or b"")
            self.ports = count[0] if count else _ports_from_model(self.model)
        return self.ports

    async def read_info(self) -> dict:
        async with self.lock:
            reply = await self.read(
                [*DISCOVERY_TAGS, T.LOCATION, T.FIRMWARE2, T.PORT_COUNT, T.PASSWORD_MODE, T.SERIAL]
            )
            info = parse_identity(reply, self.address)
            del info["source_ip"]
            self.model = info["model"] or ""
            count = reply.first(T.PORT_COUNT)
            self.ports = count[0] if count else _ports_from_model(self.model)
            self.password_mode = mode_from_flags(reply.first(T.PASSWORD_MODE))
            serial = reply.first(T.SERIAL)
            info.update(
                ports=self.ports,
                password_mode=str(self.password_mode),
                serial=T.text(serial.lstrip(b"\x00\x01")) if serial else None,
                firmware2=T.text(reply.first(T.FIRMWARE2) or b"") or None,
            )
            return info

    async def read_ports(self) -> list[dict]:
        async with self.lock:
            count = await self._port_count()
            reply = await self.read([T.PORT_STATUS, T.PORT_STATS])
            crc_index = 2 if self.model.upper().startswith("XS") else 5
            rows = {p: {"port": p} for p in range(1, count + 1)}
            for value in reply.values(T.PORT_STATUS):
                if value[0] in rows and len(value) >= 2:
                    rows[value[0]].update(T.port_status(value))
            for value in reply.values(T.PORT_STATS):
                if value[0] in rows:
                    rows[value[0]].update(T.port_stats(value, crc_index))
            return list(rows.values())

    async def read_vlan(self) -> dict | None:
        async with self.lock:
            count = await self._port_count()
            reply = await self.read([T.VLAN_MODE, T.MAX_VLANS])
            raw_mode = reply.first(T.VLAN_MODE)
            if raw_mode is None:
                return None
            mode = T.VLAN_MODES[raw_mode[0]] if raw_mode[0] < len(T.VLAN_MODES) else "disabled"
            limit = reply.first(T.MAX_VLANS)
            out = {
                "mode": mode,
                "max_vlans": int.from_bytes(limit, "big") if limit else None,
                "port_vlans": [],
                "dot1q": [],
                "pvid": {},
            }
            if mode.startswith("port"):
                reply = await self.read([T.VLAN_PORT])
                out["port_vlans"] = [
                    T.port_vlan(v, count) for v in reply.values(T.VLAN_PORT) if len(v) >= 3
                ]
            elif mode.startswith("dot1q"):
                reply = await self.read([T.VLAN_DOT1Q, T.PVID])
                out["dot1q"] = [
                    T.dot1q_vlan(v, count) for v in reply.values(T.VLAN_DOT1Q) if len(v) >= 4
                ]
                out["pvid"] = dict(T.port_u16(v) for v in reply.values(T.PVID) if len(v) >= 3)
            return out

    async def read_qos(self) -> dict | None:
        async with self.lock:
            reply = await self.read(
                [
                    T.QOS_MODE,
                    T.QOS_PRIORITY,
                    T.RATE_INGRESS,
                    T.RATE_EGRESS,
                    T.STORM_ENABLE,
                    T.STORM_RATE,
                ]
            )
            mode = reply.first(T.QOS_MODE)
            if mode is None:
                return None
            storm = reply.first(T.STORM_ENABLE)

            def rates(tag):
                return dict(T.port_rate(v) for v in reply.values(tag) if len(v) >= 5)

            return {
                "mode": T.QOS_MODES.get(mode[0], "dot1p"),
                "priorities": {
                    port: T.PRIORITIES.get(prio, "normal")
                    for port, prio in (
                        T.port_byte(v) for v in reply.values(T.QOS_PRIORITY) if len(v) >= 2
                    )
                },
                "ingress": rates(T.RATE_INGRESS),
                "egress": rates(T.RATE_EGRESS),
                "storm_control": None if storm is None else T.flag(storm),
                "storm_rates": rates(T.STORM_RATE),
            }

    async def read_features(self) -> dict:
        async with self.lock:
            count = await self._port_count()
            reply = await self.read(
                [T.MIRROR, T.IGMP, T.IGMP_BLOCK_UNKNOWN, T.IGMP_VALIDATE_V3, T.LOOP_DETECTION]
            )
            mirror = reply.first(T.MIRROR)
            igmp = reply.first(T.IGMP)
            block = reply.first(T.IGMP_BLOCK_UNKNOWN)
            validate = reply.first(T.IGMP_VALIDATE_V3)
            loop = reply.first(T.LOOP_DETECTION)
            return {
                "mirror": T.mirror(mirror, count) if mirror and len(mirror) >= 3 else None,
                "igmp": None
                if igmp is None
                else {
                    **T.igmp(igmp),
                    "block_unknown": T.flag(block) if block else None,
                    "validate_v3": T.flag(validate) if validate else None,
                },
                "loop_detection": T.flag(loop) if loop else None,
            }

    async def read_state(self) -> dict:
        info = await self.read_info()
        features = await self.read_features()
        return {
            "info": info,
            "ports": await self.read_ports(),
            "vlan": await self.read_vlan(),
            "qos": await self.read_qos(),
            **features,
            "rate_codes": T.RATE_CODES,
        }

    # -- changing settings ---------------------------------------------------

    async def set_network(
        self,
        name: str | None = None,
        dhcp: bool | None = None,
        ip: str | None = None,
        netmask: str | None = None,
        gateway: str | None = None,
    ) -> None:
        tlvs: list[tuple[int, bytes]] = []
        if name is not None:
            tlvs.append((T.NAME, name.encode()))
        if dhcp is True:
            tlvs.append((T.DHCP, b"\x01"))
        elif dhcp is False:
            if not (ip and netmask):
                raise ValueError("A static setup needs an IP address and a netmask.")
            gateway = gateway or "0.0.0.0"
            network = ipaddress.IPv4Interface(f"{ip}/{netmask}").network
            if gateway != "0.0.0.0" and ipaddress.IPv4Address(gateway) not in network:
                raise ValueError("The gateway must be in the same subnet as the switch.")
            tlvs += [
                (T.DHCP, b"\x00"),
                (T.IP, T.pack_ipv4(ip)),
                (T.NETMASK, T.pack_ipv4(netmask)),
                (T.GATEWAY, T.pack_ipv4(gateway)),
            ]
        if tlvs:
            await self.write(tlvs)
        if dhcp is False and self.address:
            self.address = ip

    async def set_vlan_mode(self, mode: str) -> None:
        await self.write([(T.VLAN_MODE, bytes([T.VLAN_MODES.index(mode)]))])

    async def set_port_vlans(self, vlans: list[dict]) -> None:
        count = await self._port_count()
        await self.write(
            [(T.VLAN_PORT, T.pack_port_vlan(v["id"], v["members"], count)) for v in vlans]
        )

    async def set_dot1q_vlan(self, vid: int, members: list[int], tagged: list[int]) -> None:
        count = await self._port_count()
        await self.write([(T.VLAN_DOT1Q, T.pack_dot1q_vlan(vid, members, tagged, count))])

    async def apply_dot1q(self, vlans: list[dict], pvid: dict[int, int]) -> None:
        """Bring the whole 802.1Q table to the given state.

        A switch refuses to take a port out of the VLAN its PVID points to,
        and to point a PVID at a VLAN the port is not in. So: first widen
        every VLAN, then move the PVIDs, then narrow, then delete leftovers.
        """
        count = await self._port_count()
        current = await self.read_vlan()
        if current is None or not current["mode"].startswith("dot1q"):
            raise ValueError("The switch is not in an 802.1Q VLAN mode.")
        target = {int(v["id"]): v for v in vlans}
        target.setdefault(1, {"id": 1, "members": [], "tagged": []})
        pvid = {int(p): int(v) for p, v in pvid.items()}
        validate_dot1q(target, {**current["pvid"], **pvid}, count, current["max_vlans"])
        existing = {v["id"]: v for v in current["dot1q"]}
        widen = []
        for vid, vlan in target.items():
            old = existing.get(vid, {"members": [], "tagged": []})
            members = sorted(set(old["members"]) | set(vlan["members"]))
            tagged = sorted((set(old["tagged"]) - set(vlan["members"])) | set(vlan["tagged"]))
            if vid not in existing or (members, tagged) != (old["members"], old["tagged"]):
                widen.append((T.VLAN_DOT1Q, T.pack_dot1q_vlan(vid, members, tagged, count)))
        for tlv in widen:
            await self.write([tlv])
        changed = {p: v for p, v in pvid.items() if current["pvid"].get(p) != v}
        if changed:
            await self.set_pvid(changed)
        for vid, vlan in target.items():
            await self.write(
                [
                    (
                        T.VLAN_DOT1Q,
                        T.pack_dot1q_vlan(
                            vid, sorted(vlan["members"]), sorted(vlan["tagged"]), count
                        ),
                    )
                ]
            )
        for vid in existing:
            if vid not in target:
                await self.delete_vlan(vid)

    async def delete_vlan(self, vid: int) -> None:
        await self.write([(T.VLAN_DELETE, vid.to_bytes(2, "big"))])

    async def set_pvid(self, pvid: dict[int, int]) -> None:
        await self.write([(T.PVID, T.pack_port_u16(int(p), v)) for p, v in sorted(pvid.items())])

    async def set_qos(
        self,
        mode: str | None = None,
        priorities: dict[int, str] | None = None,
        ingress: dict[int, int] | None = None,
        egress: dict[int, int] | None = None,
        storm_control: bool | None = None,
        storm_rates: dict[int, int] | None = None,
    ) -> None:
        codes = {v: k for k, v in T.QOS_MODES.items()}
        prio = {v: k for k, v in T.PRIORITIES.items()}
        if mode is not None:  # changing the mode resets priorities: do it first
            await self.write([(T.QOS_MODE, bytes([codes[mode]]))])
        tlvs = [(T.QOS_PRIORITY, bytes([int(p), prio[v]])) for p, v in (priorities or {}).items()]
        for tag, table in ((T.RATE_INGRESS, ingress), (T.RATE_EGRESS, egress)):
            tlvs += [(tag, T.pack_port_rate(int(p), c)) for p, c in (table or {}).items()]
        if storm_control is not None:
            tlvs.append((T.STORM_ENABLE, T.pack_flag(storm_control)))
        if storm_control is not False:
            tlvs += [
                (T.STORM_RATE, T.pack_port_rate(int(p), c)) for p, c in (storm_rates or {}).items()
            ]
        if tlvs:
            await self.write(tlvs)

    async def set_mirror(self, destination: int, sources: list[int]) -> None:
        count = await self._port_count()
        if destination > count:
            raise ValueError(f"The destination port must be 1 to {count}.")
        if destination and destination in sources:
            raise ValueError("The destination port cannot be a source port.")
        if destination and not sources:
            raise ValueError("Select at least one source port.")
        await self.write([(T.MIRROR, T.pack_mirror(destination, sources, count))])

    async def set_igmp(
        self,
        enabled: bool | None = None,
        vlan: int = 1,
        block_unknown: bool | None = None,
        validate_v3: bool | None = None,
    ) -> None:
        tlvs = []
        if enabled is not None:
            tlvs.append((T.IGMP, T.pack_igmp(enabled, vlan)))
        if block_unknown is not None:
            tlvs.append((T.IGMP_BLOCK_UNKNOWN, T.pack_flag(block_unknown)))
        if validate_v3 is not None:
            tlvs.append((T.IGMP_VALIDATE_V3, T.pack_flag(validate_v3)))
        if tlvs:
            await self.write(tlvs)

    async def set_loop_detection(self, enabled: bool) -> None:
        await self.write([(T.LOOP_DETECTION, T.pack_flag(enabled))])

    async def reset_stats(self) -> None:
        await self.write([(T.STATS_RESET, b"\x01")])

    async def cable_test(self, port: int) -> dict:
        async with self.lock:
            await self._write([(T.CABLE_TEST, bytes([port, 1]))])
            for _ in range(5):
                await asyncio.sleep(0.4 if self.transport.targets else 1.0)
                reply = await self.read([(T.CABLE_RESULT, bytes([port]))])
                for value in reply.values(T.CABLE_RESULT):
                    if value[0] == port and len(value) >= 9:
                        return T.cable_result(value)
            raise NsdpError(0, 0, "The switch returned no cable test result.")

    async def reboot(self) -> None:
        await self.write([(T.REBOOT, b"\x01")])

    async def factory_reset(self) -> None:
        await self.write([(T.FACTORY_RESET, b"\x01")])

    async def change_password(self, new_password: str) -> None:
        if not 1 <= len(new_password.encode()) <= 20:
            raise ValueError("The switch password must be 1 to 20 characters long.")
        async with self.lock:
            await self._write([])  # make sure the mode is known and the old one is right
            await self._write([new_password_tlv(self.password_mode, new_password)])
            self.password = new_password


def validate_dot1q(
    vlans: dict[int, dict], pvid: dict[int, int], ports: int, max_vlans: int | None
) -> None:
    if max_vlans and len(vlans) > max_vlans:
        raise ValueError(f"This switch holds at most {max_vlans} VLANs.")
    for vid, vlan in vlans.items():
        if not 1 <= vid <= 4093:
            raise ValueError(f"VLAN ID {vid} is outside 1-4093.")
        if any(not 1 <= p <= ports for p in vlan["members"]):
            raise ValueError(f"VLAN {vid} lists a port this switch does not have.")
        if set(vlan["tagged"]) - set(vlan["members"]):
            raise ValueError(f"VLAN {vid}: a tagged port must also be a member.")
    for port, vid in sorted(pvid.items()):
        if vid not in vlans:
            raise ValueError(f"Port {port}: PVID {vid} is not an existing VLAN.")
        if port not in vlans[vid]["members"]:
            raise ValueError(f"Port {port} must be a member of VLAN {vid} to use it as PVID.")


def _ports_from_model(model: str) -> int:
    digits = "".join(ch if ch.isdigit() else " " for ch in model).split()
    number = digits[0] if digits else ""
    for known in (5, 8, 16, 24, 48):
        if number.endswith(f"{known:02d}"):
            return known
    return 8
