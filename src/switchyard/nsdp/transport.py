"""Asyncio UDP transport for NSDP.

Switches answer a request either by unicast or by broadcast to the client
port, so one wildcard socket listens for replies. To reach switches on every
attached network, each interface additionally gets its own socket bound to
that interface's address: Linux sends a limited broadcast out of the interface
that owns the source address.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .netif import Interface, list_interfaces
from .packet import BROADCAST_MAC, Op, Packet, PacketError, format_mac
from .tags import NAMES

log = logging.getLogger(__name__)

CLIENT_PORT = 63321
DEVICE_PORT = 63322
BROADCAST = "255.255.255.255"


@dataclass
class Route:
    """One way of sending: a socket, the host MAC to claim, where broadcasts go."""

    name: str
    transport: asyncio.DatagramTransport
    mac: bytes
    broadcast: tuple[str, int]


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, owner: Transport, name: str):
        self.owner = owner
        self.name = name

    def datagram_received(self, data: bytes, addr) -> None:
        self.owner._received(data, addr, self.name)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - OS dependent
        log.debug("socket error on %s: %s", self.name, exc)


def _socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind((host, port))
    return sock


def describe(packet: Packet) -> str:
    tags = ", ".join(NAMES.get(t, f"0x{t:04x}") for t, _ in packet.tlvs) or "-"
    try:
        op = Op(packet.op).name.lower()
    except ValueError:
        op = f"op{packet.op}"
    return f"{op} seq={packet.seq} status={packet.status} [{tags}]"


class Transport:
    """Sends NSDP requests and matches the replies by sequence number."""

    def __init__(
        self,
        client_port: int = CLIENT_PORT,
        device_port: int = DEVICE_PORT,
        targets: list[str] | None = None,
        interfaces: list[str] | None = None,
        log_size: int = 500,
        timeout: float = 1.5,
        retries: int = 2,
        tftp_port: int = 69,
        tftp_timeout: float = 3.0,
        enumerate_interfaces: Callable[[], list[Interface]] = list_interfaces,
    ):
        self.tftp_port = tftp_port
        self.tftp_timeout = tftp_timeout
        self.timeout = timeout
        self.retries = retries
        self.client_port = client_port
        self.device_port = device_port
        self.targets = targets or []
        self.interface_filter = interfaces or []
        self._enumerate = enumerate_interfaces
        self.routes: list[Route] = []
        self._listener: asyncio.DatagramTransport | None = None
        self._seq = int(time.time() * 1000) & 0xFFFF
        self._waiters: dict[int, Callable[[Packet, tuple], None]] = {}
        self.packet_log: deque[dict] = deque(maxlen=log_size)
        self._log_id = 0

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        listener, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self, "any"), sock=_socket("0.0.0.0", self.client_port)
        )
        self._listener = listener
        self.client_port = listener.get_extra_info("sockname")[1]
        if self.targets:
            # explicit targets (tests, demo mode, routed networks)
            for target in self.targets:
                host, _, port = target.partition(":")
                dest = (host, int(port) if port else self.device_port)
                self.routes.append(Route(target, listener, BROADCAST_MAC, dest))
            return
        for iface in self._enumerate():
            if self.interface_filter and iface.name not in self.interface_filter:
                continue
            try:
                sock = _socket(iface.ip, self.client_port)
            except OSError as exc:
                log.warning("cannot use interface %s (%s): %s", iface.name, iface.ip, exc)
                continue
            transport, _ = await loop.create_datagram_endpoint(
                lambda n=iface.name: _Protocol(self, n), sock=sock
            )
            self.routes.append(
                Route(iface.name, transport, iface.mac, (BROADCAST, self.device_port))
            )
        if not self.routes:
            log.warning("no usable network interface found, using the default route only")
            self.routes.append(
                Route("default", listener, BROADCAST_MAC, (BROADCAST, self.device_port))
            )

    async def stop(self) -> None:
        seen = set()
        for transport in [r.transport for r in self.routes] + [self._listener]:
            if transport is not None and id(transport) not in seen:
                seen.add(id(transport))
                transport.close()
        self.routes = []
        self._listener = None
        await asyncio.sleep(0)

    def tftp_target(self, host: str | None) -> tuple[str, int]:
        """Where the firmware upload goes: the switch's own address, or the fixed target."""
        if self.targets:  # tests and demo mode: everything is on one host
            return self.targets[0].partition(":")[0], self.tftp_port
        if not host:
            raise ValueError("The switch address is unknown. Search the network first.")
        return host, self.tftp_port

    # -- packet log ----------------------------------------------------------

    def _record(self, direction: str, packet: Packet | None, raw: bytes, peer: str) -> None:
        self._log_id += 1
        self.packet_log.append(
            {
                "id": self._log_id,
                "time": time.time(),
                "direction": direction,
                "peer": peer,
                "device": format_mac(packet.device_mac) if packet else None,
                "summary": describe(packet) if packet else "not an NSDP packet",
                "bytes": len(raw),
                "hex": _redact(packet, raw).hex(),
            }
        )

    def _received(self, data: bytes, addr: tuple, route: str) -> None:
        try:
            packet = Packet.decode(data)
        except PacketError as exc:
            log.debug("ignoring datagram from %s: %s", addr, exc)
            self._record("rx", None, data, f"{addr[0]}:{addr[1]}")
            return
        if packet.op not in (Op.READ_RESPONSE, Op.WRITE_RESPONSE):
            return  # another manager's request heard on the broadcast address
        waiter = self._waiters.get(packet.seq)
        if waiter is None:
            return
        self._record("rx", packet, data, f"{addr[0]}:{addr[1]}")
        waiter(packet, addr)

    # -- requests ------------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def _send(self, packet: Packet, address: str | None) -> None:
        if not self.routes:
            raise RuntimeError("transport is not started")
        for route in self.routes:
            packet.host_mac = route.mac
            dest = (address, self.device_port) if address and not self.targets else route.broadcast
            raw = packet.encode()
            try:
                route.transport.sendto(raw, dest)
            except OSError as exc:  # pragma: no cover - OS dependent
                log.debug("send on %s failed: %s", route.name, exc)
                continue
            self._record("tx", packet, raw, f"{dest[0]}:{dest[1]} via {route.name}")
            if address and not self.targets:
                break  # unicast: the routing table picks the interface

    async def collect(
        self, op: Op, tlvs: list[tuple[int, bytes]], timeout: float = 2.0
    ) -> list[tuple[Packet, tuple]]:
        """Broadcast a request to every switch and gather all replies."""
        seq = self._next_seq()
        replies: list[tuple[Packet, tuple]] = []
        seen: set[bytes] = set()

        def on_reply(packet: Packet, addr: tuple) -> None:
            if packet.device_mac not in seen:
                seen.add(packet.device_mac)
                replies.append((packet, addr))

        self._waiters[seq] = on_reply
        try:
            packet = Packet(op, device_mac=BROADCAST_MAC, seq=seq, tlvs=tlvs)
            self._send(packet, None)
            await asyncio.sleep(timeout / 2)
            self._send(packet, None)
            await asyncio.sleep(timeout / 2)
        finally:
            del self._waiters[seq]
        return replies

    async def request(
        self,
        op: Op,
        device_mac: bytes,
        tlvs: list[tuple[int, bytes]],
        address: str | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> Packet:
        """Send to one switch and wait for its reply.

        ``address`` sends by unicast instead of broadcast. Retries reuse the
        sequence number so a switch that already answered is not asked twice
        under a new identity.
        """
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        seq = self._next_seq()
        future: asyncio.Future[Packet] = asyncio.get_running_loop().create_future()

        def on_reply(packet: Packet, addr: tuple) -> None:
            if packet.device_mac == device_mac and not future.done():
                future.set_result(packet)

        self._waiters[seq] = on_reply
        try:
            packet = Packet(op, device_mac=device_mac, seq=seq, tlvs=tlvs)
            for _ in range(retries + 1):
                self._send(packet, address)
                with contextlib.suppress(TimeoutError):
                    return await asyncio.wait_for(asyncio.shield(future), timeout)
        finally:
            del self._waiters[seq]
        raise TimeoutError(f"no answer from {format_mac(device_mac)}")


_SECRET_TAGS = {0x0009, 0x000A, 0x001A}


def _redact(packet: Packet | None, raw: bytes) -> bytes:
    """Blank credential values so the packet log never holds a password."""
    if packet is None or not any(t in _SECRET_TAGS for t, _ in packet.tlvs):
        return raw
    clean = Packet(
        packet.op,
        packet.host_mac,
        packet.device_mac,
        packet.seq,
        packet.status,
        packet.failed_tag,
        [(t, b"*" * len(v) if t in _SECRET_TAGS else v) for t, v in packet.tlvs],
    )
    return clean.encode()
