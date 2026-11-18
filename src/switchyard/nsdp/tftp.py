"""A small asyncio TFTP client (write request, octet mode, RFC 1350)."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable

BLOCK = 512


class TftpError(Exception):
    """The server answered with an error or stopped answering."""


class _Client(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue: asyncio.Queue[tuple[bytes, tuple]] = asyncio.Queue()
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.queue.put_nowait((data, addr))


async def put(
    host: str,
    port: int,
    filename: str,
    data: bytes,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = 3.0,
    retries: int = 5,
) -> None:
    """Upload ``data``; raises TftpError on an error packet or after ``retries`` timeouts."""
    loop = asyncio.get_running_loop()
    protocol = _Client()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("0.0.0.0", 0), family=2
    )
    server: tuple = (host, port)

    async def exchange(packet: bytes, expected_block: int) -> tuple:
        for _ in range(retries):
            transport.sendto(packet, server)
            try:
                while True:
                    reply, addr = await asyncio.wait_for(protocol.queue.get(), timeout)
                    if len(reply) < 4:
                        continue
                    op, number = struct.unpack(">HH", reply[:4])
                    if op == 5:
                        text = reply[4:].split(b"\0")[0].decode(errors="replace")
                        raise TftpError(f"TFTP error {number}: {text}")
                    if op == 4 and number == expected_block:
                        return addr
            except TimeoutError:
                continue
        raise TftpError("The switch stopped answering during the upload.")

    try:
        request = b"\x00\x02" + filename.encode() + b"\x00octet\x00"
        server = await exchange(request, 0)
        total = len(data)
        block = 1
        offset = 0
        while True:
            chunk = data[offset : offset + BLOCK]
            await exchange(struct.pack(">HH", 3, block & 0xFFFF) + chunk, block & 0xFFFF)
            offset += len(chunk)
            if progress:
                progress(offset, total)
            block += 1
            if len(chunk) < BLOCK:
                break
    finally:
        transport.close()
