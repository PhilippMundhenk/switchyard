"""TP-Link Easy Smart switches, as a Switchyard backend."""

from __future__ import annotations

from ..easysmart.client import EasySmartClient, parse_identity
from ..easysmart.packet import RSA_MODULUS, Op
from ..easysmart.transport import EasySmartTransport
from ..nsdp.packet import parse_mac
from .base import Backend


class EasySmartBackend(Backend):
    protocol = "easysmart"
    vendor = "TP-Link"
    unicast = False  # switches answer by broadcast: same network segment only
    needs_username = True

    def __init__(self, transport: EasySmartTransport, rsa_modulus: int = RSA_MODULUS):
        self.transport = transport
        self.rsa_modulus = rsa_modulus
        self.drivers: dict[str, EasySmartClient] = {}

    async def start(self) -> None:
        await self.transport.start()

    async def stop(self) -> None:
        await self.transport.stop()

    async def discover(self, seconds: float) -> list[dict]:
        replies = await self.transport.collect(Op.DISCOVERY, [], seconds)
        return [{**parse_identity(packet), "source_ip": addr[0]} for packet, addr in replies]

    def driver(self, mac, address, password, username=None) -> EasySmartClient:
        return EasySmartClient(
            self.transport, parse_mac(mac), password, username, rsa_modulus=self.rsa_modulus
        )

    def packet_log(self) -> list:
        return list(self.transport.packet_log)
