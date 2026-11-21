"""Packets of the TP-Link Easy Smart protocol: header, TLVs and the fixed RC4 scramble.

Layout and key as published by the community (see docs/PROTOCOL-EASYSMART.md). Every packet,
header included, is RC4-encrypted with one key that is the same on every switch, so this is
obfuscation, not security.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

HEADER = struct.Struct("!BB6s6sHiHHHHi")  # 32 bytes
END = b"\xff\xff\x00\x00"
MAX_PACKET = 1500

# The RC4 state after the key schedule with the vendor's key (the same on every switch).
RC4_STATE = bytes(
    [
        191, 155, 227, 202, 99, 162, 79, 104, 49, 18, 190, 164, 30, 76, 189, 131,
        23, 52, 86, 106, 207, 125, 126, 169, 196, 28, 172, 58, 188, 132, 160, 3,
        36, 120, 144, 168, 12, 231, 116, 44, 41, 97, 108, 213, 42, 198, 32, 148,
        218, 107, 247, 112, 204, 14, 66, 68, 91, 224, 206, 235, 33, 130, 203, 178,
        1, 134, 199, 78, 249, 123, 7, 145, 73, 208, 209, 100, 74, 115, 72, 118,
        8, 22, 243, 147, 64, 96, 5, 87, 60, 113, 233, 152, 31, 219, 143, 174,
        232, 153, 245, 158, 254, 70, 170, 75, 77, 215, 211, 59, 71, 133, 214, 157,
        151, 6, 46, 81, 94, 136, 166, 210, 4, 43, 241, 29, 223, 176, 67, 63,
        186, 137, 129, 40, 248, 255, 55, 15, 62, 183, 222, 105, 236, 197, 127, 54,
        179, 194, 229, 185, 37, 90, 237, 184, 25, 156, 173, 26, 187, 220, 2, 225,
        0, 240, 50, 251, 212, 253, 167, 17, 193, 205, 177, 21, 181, 246, 82, 226,
        38, 101, 163, 182, 242, 92, 20, 11, 95, 13, 230, 16, 121, 124, 109, 195,
        117, 39, 98, 239, 84, 56, 139, 161, 47, 201, 51, 135, 250, 10, 19, 150,
        45, 111, 27, 24, 142, 80, 85, 83, 234, 138, 216, 57, 93, 65, 154, 141,
        122, 34, 140, 128, 238, 88, 89, 9, 146, 171, 149, 53, 102, 61, 114, 69,
        217, 175, 103, 228, 35, 180, 252, 200, 192, 165, 159, 221, 244, 110, 119, 48,
    ]
)  # fmt: skip


def _keystream(length: int) -> bytes:
    s = bytearray(RC4_STATE)
    out = bytearray(length)
    j = 0
    for k in range(length):
        i = (k + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[k] = s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


_STREAM = _keystream(MAX_PACKET)


def scramble(data: bytes) -> bytes:
    """Encrypt or decrypt (RC4 is symmetric) one packet."""
    if len(data) > MAX_PACKET:
        raise ValueError("packet too long")
    return bytes(a ^ b for a, b in zip(data, _STREAM, strict=False))


def session_scramble(data: bytes, key: bytes) -> bytes:
    """The session cipher of newer firmware: RC4 with a state of only 8 entries, keyed by the
    8-character session key, fresh for every packet."""
    s = list(range(8))
    j = 0
    for i in range(8):
        j = (j + s[i] + key[i % len(key)]) % 8
        s[i], s[j] = s[j], s[i]
    out = bytearray(data)
    i = j = 0
    for k in range(len(out)):
        i = (i + 1) % 8
        j = (j + s[i]) % 8
        s[i], s[j] = s[j], s[i]
        out[k] ^= s[(s[i] + s[j]) % 8]
    return bytes(out)


# RSA public key of the TL-SG108E v6 for the session key exchange (textbook RSA, no padding)
RSA_MODULUS = 0xB3FAB9D6646D4EBF
RSA_EXPONENT = 65537


def key_exchange_value(session_key: bytes, modulus: int = RSA_MODULUS) -> bytes:
    """Value of TLV 528: 0, word count, the ciphertext as little-endian u16 words."""
    cipher = pow(int.from_bytes(session_key, "little"), RSA_EXPONENT, modulus)
    raw = cipher.to_bytes(8, "little")
    words = 4
    while words > 1 and raw[2 * words - 2 : 2 * words] == b"\x00\x00":
        words -= 1
    return bytes([0, words]) + raw


class Op(IntEnum):
    DISCOVERY = 0
    GET = 1
    REPLY = 2  # answer to DISCOVERY and GET
    LOGIN = 3  # login, and every write (user name and password go along)
    RETURN = 4  # answer to LOGIN


@dataclass
class Packet:
    op: int
    switch_mac: bytes
    host_mac: bytes
    seq: int
    error: int = 0
    token: int = 0
    tlvs: list[tuple[int, bytes]] = field(default_factory=list)
    fragment: int = 0
    flag: int = 0
    version: int = 1

    def encode(self) -> bytes:
        return scramble(self.encode_plain())

    def encode_plain(self) -> bytes:
        body = b"".join(struct.pack("!HH", t, len(v)) + v for t, v in self.tlvs) + END
        length = HEADER.size + len(body)
        header = HEADER.pack(
            self.version,
            self.op,
            self.switch_mac,
            self.host_mac,
            self.seq,
            self.error,
            length,
            self.fragment,
            self.flag,
            self.token,
            0,
        )
        return header + body

    def encode_session(self, key: bytes) -> bytes:
        return session_scramble(self.encode_plain(), key)

    @classmethod
    def decode(cls, raw: bytes, session_key: bytes | None = None) -> Packet:
        """Decrypt and parse; ``session_key`` for packets of a key-exchange session."""
        if len(raw) > MAX_PACKET:
            raise ValueError("packet too long")
        data = session_scramble(raw, session_key) if session_key else scramble(raw)
        if len(data) < HEADER.size + len(END):
            raise ValueError("packet too short")
        version, op, switch, host, seq, error, length, fragment, flag, token, _ = HEADER.unpack(
            data[: HEADER.size]
        )
        if version != 1 or op > 4 or not HEADER.size + len(END) <= length <= len(data):
            raise ValueError("not an Easy Smart packet")
        data = data[:length]  # some firmware pads the datagram
        tlvs: list[tuple[int, bytes]] = []
        pos = HEADER.size
        while pos + 4 <= len(data):
            tag, size = struct.unpack_from("!HH", data, pos)
            pos += 4
            if tag == 0xFFFF:
                break
            if pos + size > len(data):
                raise ValueError(f"TLV 0x{tag:04x} runs past the end of the packet")
            tlvs.append((tag, data[pos : pos + size]))
            pos += size
        return cls(op, switch, host, seq, error, token, tlvs, fragment, flag, version)

    def values(self, tag: int) -> list[bytes]:
        return [v for t, v in self.tlvs if t == tag]

    def first(self, tag: int) -> bytes | None:
        for t, v in self.tlvs:
            if t == tag:
                return v
        return None

    def has(self, tag: int) -> bool:
        return any(t == tag for t, _ in self.tlvs)
