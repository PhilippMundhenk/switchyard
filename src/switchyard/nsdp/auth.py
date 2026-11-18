"""The password encodings switches accept, selected by the flags in tag 0x0014.

None of them is real cryptography; see docs/security.md.
"""

from __future__ import annotations

from enum import StrEnum

XOR_KEY = b"NtgrSmartSwitchRock"


class PasswordMode(StrEnum):
    PLAIN = "plain"  # very old firmware: no tag 0x0014 at all
    XOR = "xor"  # flag bit 0
    HASH4 = "hash4"  # flag bit 3
    HASH8 = "hash8"  # flag bit 4, sent in tag 0x001a

    @property
    def needs_salt(self) -> bool:
        return self in (PasswordMode.HASH4, PasswordMode.HASH8)


def mode_from_flags(flags: bytes | None) -> PasswordMode:
    if not flags:
        return PasswordMode.PLAIN
    value = int.from_bytes(flags, "big")
    if value & 0x10:
        return PasswordMode.HASH8
    if value & 0x08:
        return PasswordMode.HASH4
    if value & 0x01:
        return PasswordMode.XOR
    return PasswordMode.PLAIN


def flags_for_mode(mode: PasswordMode) -> bytes | None:
    return {
        PasswordMode.PLAIN: None,
        PasswordMode.XOR: b"\x00\x00\x00\x01",
        PasswordMode.HASH4: b"\x00\x00\x00\x08",
        PasswordMode.HASH8: b"\x00\x00\x00\x10",
    }[mode]


def xor_password(password: bytes) -> bytes:
    return bytes(c ^ XOR_KEY[i % len(XOR_KEY)] for i, c in enumerate(password))


def _padded(password: bytes) -> bytes:
    return password[:20].ljust(20, b"\0")


def hash8(password: bytes, mac: bytes, salt: bytes) -> bytes:
    k, m, s = _padded(password), mac, salt
    return bytes(
        [
            s[3] ^ s[2] ^ m[1] ^ m[5] ^ k[0] ^ k[1] ^ k[2],
            s[3] ^ s[1] ^ m[4] ^ m[0] ^ k[3] ^ k[4] ^ k[5],
            s[0] ^ s[2] ^ m[3] ^ m[2] ^ k[6] ^ k[7] ^ k[8],
            s[0] ^ s[1] ^ m[4] ^ m[5] ^ k[9] ^ k[10] ^ k[11],
            s[3] ^ s[2] ^ m[1] ^ m[5] ^ k[12] ^ k[13] ^ k[14],
            s[3] ^ s[1] ^ m[4] ^ m[0] ^ k[15] ^ k[16] ^ k[17],
            s[0] ^ s[2] ^ m[3] ^ m[2] ^ k[18] ^ k[19] ^ k[0],
            s[0] ^ s[1] ^ m[4] ^ m[5] ^ k[1] ^ k[3] ^ k[5],
        ]
    )


def hash4(password: bytes, mac: bytes, salt: bytes) -> bytes:
    k, m, s = _padded(password), mac, salt
    return bytes(
        [
            m[1] ^ s[3] ^ s[2] ^ k[13] ^ k[9] ^ m[5] ^ k[7] ^ k[1],
            s[3] ^ s[1] ^ m[4] ^ k[14] ^ k[10] ^ k[6] ^ k[2] ^ m[0],
            m[3] ^ m[2] ^ s[0] ^ s[2] ^ k[12] ^ k[8] ^ k[5] ^ k[0],
            s[0] ^ s[1] ^ m[4] ^ k[15] ^ k[11] ^ k[4] ^ k[3] ^ m[5],
        ]
    )


TAG_NEW_PASSWORD = 0x0009
TAG_PASSWORD = 0x000A
TAG_AUTH_V2 = 0x001A


def credential_tlv(
    mode: PasswordMode, password: str, mac: bytes, salt: bytes | None
) -> tuple[int, bytes]:
    """The TLV that authenticates a write request."""
    raw = password.encode()
    if mode is PasswordMode.XOR:
        return TAG_PASSWORD, xor_password(raw)
    if mode is PasswordMode.HASH4:
        return TAG_PASSWORD, hash4(raw, mac, salt or bytes(4))
    if mode is PasswordMode.HASH8:
        return TAG_AUTH_V2, hash8(raw, mac, salt or bytes(4))
    return TAG_PASSWORD, raw


def new_password_tlv(mode: PasswordMode, password: str) -> tuple[int, bytes]:
    """The TLV that carries a replacement password.

    A hash cannot carry a new secret, so every mode except PLAIN uses the
    reversible XOR encoding here.
    """
    raw = password.encode()
    if mode is PasswordMode.PLAIN:
        return TAG_NEW_PASSWORD, raw
    return TAG_NEW_PASSWORD, xor_password(raw)
