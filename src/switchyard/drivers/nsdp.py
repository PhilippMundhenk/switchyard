"""NETGEAR Plus switches over NSDP, as a Switchyard backend."""

from __future__ import annotations

from ..nsdp import SwitchClient, Transport, discover, parse_mac, probe
from .base import Backend


class NsdpBackend(Backend):
    protocol = "nsdp"
    vendor = "NETGEAR"
    unicast = True

    def __init__(self, transport: Transport):
        self.transport = transport

    async def start(self) -> None:
        await self.transport.start()

    async def stop(self) -> None:
        await self.transport.stop()

    async def discover(self, seconds: float) -> list[dict]:
        return await discover(self.transport, seconds)

    async def probe(self, address: str, timeout: float = 2.0) -> dict | None:
        return await probe(self.transport, address, timeout)

    def driver(self, mac, address, password, username=None) -> SwitchClient:
        return SwitchClient(self.transport, parse_mac(mac), address, password)

    def packet_log(self) -> list:
        return list(self.transport.packet_log)
