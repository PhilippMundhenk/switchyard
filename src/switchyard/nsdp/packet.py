"""NSDP packet codec: 32-byte header followed by a chain of TLV records."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

SIGNATURE = b"NSDP"
HEADER_LEN = 32
END_TAG = 0xFFFF
BROADCAST_MAC = bytes(6)


class Op(IntEnum):
    READ_REQUEST = 1
    READ_RESPONSE = 2
    WRITE_REQUEST = 3
    WRITE_RESPONSE = 4


class PacketError(ValueError):
    """Raised when bytes are not a well-formed NSDP packet."""


@dataclass
class Packet:
    op: int
    host_mac: bytes = BROADCAST_MAC
    device_mac: bytes = BROADCAST_MAC
    seq: int = 0
    status: int = 0
    failed_tag: int = 0
    tlvs: list[tuple[int, bytes]] = field(default_factory=list)

    def encode(self) -> bytes:
        if len(self.host_mac) != 6 or len(self.device_mac) != 6:
            raise PacketError("MAC addresses must be 6 bytes")
        head = struct.pack(
            ">BBBxHxx6s6sxxH4sxxxx",
            1,
            self.op,
            self.status & 0xFF,
            self.failed_tag & 0xFFFF,
            self.host_mac,
            self.device_mac,
            self.seq & 0xFFFF,
            SIGNATURE,
        )
        body = b"".join(struct.pack(">HH", tag, len(value)) + value for tag, value in self.tlvs)
        return head + body + struct.pack(">HH", END_TAG, 0)

    @classmethod
    def decode(cls, data: bytes) -> Packet:
        if len(data) < HEADER_LEN:
            raise PacketError("packet shorter than the NSDP header")
        version, op, status, failed_tag, host, device, seq, sig = struct.unpack_from(
            ">BBBxHxx6s6sxxH4sxxxx", data
        )
        if sig != SIGNATURE:
            raise PacketError("missing NSDP signature")
        if version != 1:
            raise PacketError(f"unsupported protocol version {version}")
        tlvs: list[tuple[int, bytes]] = []
        pos = HEADER_LEN
        while pos + 4 <= len(data):
            tag, length = struct.unpack_from(">HH", data, pos)
            pos += 4
            if tag == END_TAG:
                break
            if length == 0xFFFF:  # some firmware marks "empty" this way
                length = 0
            if pos + length > len(data):
                raise PacketError(f"TLV 0x{tag:04x} runs past the end of the packet")
            tlvs.append((tag, data[pos : pos + length]))
            pos += length
        return cls(op, host, device, seq, status, failed_tag, tlvs)

    def values(self, tag: int) -> list[bytes]:
        """All non-empty values of ``tag`` in wire order."""
        return [v for t, v in self.tlvs if t == tag and v]

    def first(self, tag: int) -> bytes | None:
        vals = self.values(tag)
        return vals[0] if vals else None

    def has(self, tag: int) -> bool:
        return any(t == tag for t, _ in self.tlvs)


def parse_mac(text: str) -> bytes:
    cleaned = text.replace(":", "").replace("-", "").replace(".", "").strip().lower()
    if len(cleaned) != 12:
        raise ValueError(f"not a MAC address: {text!r}")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError(f"not a MAC address: {text!r}") from exc


def format_mac(mac: bytes) -> str:
    return ":".join(f"{b:02x}" for b in mac)
