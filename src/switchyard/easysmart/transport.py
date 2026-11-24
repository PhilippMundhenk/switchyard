"""Asyncio UDP transport for the TP-Link Easy Smart protocol.

Requests go to the broadcast address, port 29808, from port 29809; switches answer by
broadcast to port 29809. A wildcard socket on 29809 hears every answer; one socket per
interface, bound to that interface's address, makes the broadcast leave there. Answers are
matched by sequence number, switch MAC and our host MAC.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ..nsdp.netif import Interface, is_virtual, list_interfaces
from ..nsdp.packet import format_mac
from ..nsdp.transport import LOG_IDS, _Protocol, _socket
from .packet import Op, Packet
from .tags import NAMES, SECRET_TAGS

log = logging.getLogger(__name__)

CLIENT_PORT = 29809
DEVICE_PORT = 29808
BROADCAST = "255.255.255.255"
ZERO_MAC = b"\x00" * 6


@dataclass
class Route:
    name: str
    transport: asyncio.DatagramTransport
    mac: bytes
    destination: tuple[str, int]


def describe(packet: Packet) -> str:
    tags = ", ".join(NAMES.get(t, f"0x{t:04x}") for t, _ in packet.tlvs) or "-"
    try:
        op = Op(packet.op).name.lower()
    except ValueError:
        op = f"op{packet.op}"
    return f"{op} seq={packet.seq} error={packet.error} token={packet.token} [{tags}]"


def redact(packet: Packet) -> Packet:
    """A copy without credential values, for the packet log."""
    return Packet(
        packet.op,
        packet.switch_mac,
        packet.host_mac,
        packet.seq,
        packet.error,
        packet.token,
        [(t, b"*" * len(v) if t in SECRET_TAGS else v) for t, v in packet.tlvs],
        packet.fragment,
        packet.flag,
        packet.version,
    )


class EasySmartTransport:
    def __init__(
        self,
        client_port: int = CLIENT_PORT,
        device_port: int = DEVICE_PORT,
        targets: list[str] | None = None,
        interfaces: list[str] | None = None,
        timeout: float = 1.5,
        retries: int = 2,
        log_size: int = 500,
        enumerate_interfaces: Callable[[], list[Interface]] = list_interfaces,
    ):
        self.client_port = client_port
        self.device_port = device_port
        self.targets = targets or []
        self.interface_filter = interfaces or []
        self.timeout = timeout
        self.retries = retries
        self._enumerate = enumerate_interfaces
        self.routes: list[Route] = []
        self._listener: asyncio.DatagramTransport | None = None
        self._seq = int(time.time()) % 1000
        self._discovery_seq = 1000
        self.sessions: dict[bytes, bytes] = {}  # switch MAC -> session key (newer firmware)
        self._waiters: dict[int, Callable[[Packet, tuple], None]] = {}
        self.packet_log: deque[dict] = deque(maxlen=log_size)
        self._sent: dict[int, float] = {}  # sequence -> first send (monotonic)
        self.home: dict[bytes, str] = {}

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        listener, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self, "any"), sock=_socket("0.0.0.0", self.client_port)
        )
        self._listener = listener
        self.client_port = listener.get_extra_info("sockname")[1]
        if self.targets:  # tests and demo mode: a fixed address instead of the broadcast
            for target in self.targets:
                host, _, port = target.partition(":")
                dest = (host, int(port) if port else self.device_port)
                self.routes.append(Route(target, listener, b"\x02\x00\x00\x00\x00\x01", dest))
            return
        for iface in self._enumerate():
            if self.interface_filter and iface.name not in self.interface_filter:
                continue
            if not self.interface_filter and is_virtual(iface.name):
                continue
            try:
                sock = _socket(iface.ip, self.client_port)
            except OSError as exc:
                log.warning("Easy Smart: cannot use interface %s: %s", iface.name, exc)
                continue
            transport, _ = await loop.create_datagram_endpoint(
                lambda n=iface.name: _Protocol(self, n), sock=sock
            )
            self.routes.append(
                Route(iface.name, transport, iface.mac, (BROADCAST, self.device_port))
            )
        if not self.routes:
            self.routes.append(Route("default", listener, ZERO_MAC, (BROADCAST, self.device_port)))

    async def stop(self) -> None:
        seen = set()
        for transport in [r.transport for r in self.routes] + [self._listener]:
            if transport is not None and id(transport) not in seen:
                seen.add(id(transport))
                transport.close()
        self.routes = []
        self._listener = None
        await asyncio.sleep(0)

    # -- packet log ----------------------------------------------------------

    def _record(self, direction: str, packet: Packet | None, raw: bytes, peer: str) -> None:
        # how long the answer took since the request was first sent (#123): slow answers and
        # resends show in the protocol log
        waited = ""
        if packet is not None:
            if direction == "tx":
                self._sent.setdefault(packet.seq, time.monotonic())
            elif packet.seq in self._sent:
                ms = (time.monotonic() - self._sent.pop(packet.seq)) * 1000
                waited = f", answered after {ms:.0f} ms"
                if len(self._sent) > 1000:
                    self._sent.clear()
        self.packet_log.append(
            {
                "id": next(LOG_IDS),
                "time": time.time(),
                "direction": direction,
                "peer": peer,
                "device": format_mac(packet.switch_mac) if packet else None,
                "summary": ("TP-Link " + describe(packet) + waited)
                if packet
                else "not a TP-Link packet",
                "bytes": len(raw),
                # decrypted, credentials blanked: the scramble hides nothing anyway
                "hex": redact(packet).encode_plain().hex() if packet else raw.hex(),
            }
        )

    def _decode(self, data: bytes) -> Packet | None:
        try:
            return Packet.decode(data)
        except ValueError:
            pass
        for key in list(self.sessions.values()):
            try:
                return Packet.decode(data, key)
            except ValueError:
                continue
        return None

    def _received(self, data: bytes, addr: tuple, route: str) -> None:
        packet = self._decode(data)
        if packet is None:
            log.debug("ignoring datagram from %s", addr)
            return
        if packet.op not in (Op.REPLY, Op.RETURN):
            return  # a request, ours or another manager's, heard on the broadcast address
        if packet.host_mac not in {r.mac for r in self.routes}:
            return  # an answer for another manager
        waiter = self._waiters.get(packet.seq)
        if waiter is None:
            return
        self._record("rx", packet, data, f"{addr[0]}:{addr[1]}")
        self.home[packet.switch_mac] = (
            route if route != "any" else self.home.get(packet.switch_mac, "")
        )
        waiter(packet, addr)

    # -- requests ------------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) % 1000  # the vendor tool counts to 999
        return self._seq

    def _send(self, packet: Packet, routes: list[Route], key: bytes | None = None) -> None:
        if not self.routes:
            raise RuntimeError("transport is not started")
        for route in routes:
            packet.host_mac = route.mac
            raw = packet.encode_session(key) if key else packet.encode()
            try:
                route.transport.sendto(raw, route.destination)
            except OSError as exc:  # pragma: no cover - OS dependent
                log.debug("send on %s failed: %s", route.name, exc)
                continue
            self._record("tx", packet, raw, f"{route.destination[0]} via {route.name}")

    async def collect(
        self, op: Op, tlvs: list[tuple[int, bytes]], timeout: float = 2.0
    ) -> list[tuple[Packet, tuple]]:
        """Broadcast a request (discovery) and gather one reply per switch."""
        # discoveries use their own range, so a late answer is never taken for a request's
        self._discovery_seq = 1000 + (self._discovery_seq + 1) % 1000
        seq = self._discovery_seq
        replies: list[tuple[Packet, tuple]] = []
        seen: set[bytes] = set()

        def on_reply(packet: Packet, addr: tuple) -> None:
            if packet.switch_mac not in seen:
                seen.add(packet.switch_mac)
                replies.append((packet, addr))

        self._waiters[seq] = on_reply
        try:
            packet = Packet(op, ZERO_MAC, ZERO_MAC, seq, tlvs=tlvs)
            self._send(packet, self.routes)
            await asyncio.sleep(timeout / 2)
            self._send(packet, self.routes)
            await asyncio.sleep(timeout / 2)
        finally:
            del self._waiters[seq]
        return replies

    async def request(
        self,
        op: Op,
        switch_mac: bytes,
        tlvs: list[tuple[int, bytes]],
        token: int = 0,
        timeout: float | None = None,
        retries: int | None = None,
        session: bool = True,
    ) -> list[Packet]:
        """Send to one switch; return its answer (several packets if it was fragmented).

        Every attempt gets a new sequence number (the vendor tool does the same), so a late
        answer to an earlier attempt cannot be mistaken for the current one.
        """
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        key = self.sessions.get(switch_mac) if session else None
        home = self.home.get(switch_mac)
        for attempt in range(retries + 1):
            seq = self._next_seq()
            done: asyncio.Future[list[Packet]] = asyncio.get_running_loop().create_future()
            parts: list[Packet] = []

            def on_reply(packet: Packet, addr: tuple, done=done, parts=parts) -> None:
                if packet.switch_mac != switch_mac or done.done():
                    return
                parts.append(packet)
                if packet.fragment == 0 or len(parts) >= 64:  # 0 = last (or only) part
                    done.set_result(list(parts))

            self._waiters[seq] = on_reply
            try:
                routes = self.routes
                if home and attempt == 0:
                    routes = [r for r in self.routes if r.name == home] or self.routes
                packet = Packet(op, switch_mac, ZERO_MAC, seq, token=token, tlvs=tlvs)
                self._send(packet, routes, key)
                with contextlib.suppress(TimeoutError):
                    return await asyncio.wait_for(done, timeout)
            finally:
                del self._waiters[seq]
        raise TimeoutError(f"no answer from {format_mac(switch_mac)}")
