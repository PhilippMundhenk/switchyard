"""Firmware image checks for the vendor's ``UMHD`` container.

Layout (from public reverse-engineering notes): 64-byte header, then the body.
``0x00`` magic ``UMHD``; ``0x04`` body length (8 ASCII hex digits); ``0x10`` checksum
(4 ASCII hex digits, low 16 bits of the byte sum of the body); ``0x14`` board name
(NUL padded); ``0x34`` version (4 ASCII characters). The checks refuse anything that
does not look right: a wrong image can brick a switch.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

HEADER = 64


class FirmwareError(ValueError):
    """The file is not a firmware image this switch should receive."""


@dataclass
class Image:
    size: int
    body_length: int
    checksum: int
    board: str
    version_raw: str
    version: str

    def as_dict(self) -> dict:
        return asdict(self)


def build_image(board: str, version_raw: str, body: bytes) -> bytes:
    """Make a valid image (used by tests, the simulator and the demo)."""
    header = bytearray(HEADER)
    header[0:4] = b"UMHD"
    header[4:12] = f"{len(body):08X}".encode()
    header[0x10:0x14] = f"{sum(body) & 0xFFFF:04X}".encode()
    header[0x14 : 0x14 + len(board)] = board.encode()
    header[0x34:0x38] = version_raw.encode()
    return bytes(header) + body


def guess_version(raw: str) -> str:
    """``100C`` -> ``1.00.12``. A best guess: the format is not documented."""
    try:
        return f"{raw[0]}.{raw[1:3]}.{int(raw[3], 16)}"
    except (ValueError, IndexError):
        return raw


def parse(data: bytes) -> Image:
    if len(data) < HEADER + 1024:
        raise FirmwareError("The file is too small to be a firmware image.")
    if data[:4] != b"UMHD":
        raise FirmwareError("This is not a NETGEAR firmware image (missing UMHD header).")
    try:
        length = int(data[4:12].decode(), 16)
        checksum = int(data[0x10:0x14].decode(), 16)
    except ValueError as exc:
        raise FirmwareError("The firmware header is damaged.") from exc
    if length != len(data) - HEADER:
        raise FirmwareError("The firmware file is truncated or has extra data.")
    actual = sum(data[HEADER:]) & 0xFFFF
    if actual != checksum:
        raise FirmwareError("The firmware checksum does not match: the file is damaged.")
    board = data[0x14:0x34].split(b"\0", 1)[0].decode("ascii", "replace").strip()
    raw = data[0x34:0x38].decode("ascii", "replace")
    return Image(len(data), length, checksum, board, raw, guess_version(raw))


def matches_model(board: str, model: str) -> bool:
    """Does the image's board name fit the switch model (``GS108Ev3`` accepts ``GS108E``)?"""
    b, m = board.upper(), model.upper()
    return bool(b) and bool(m) and (m.startswith(b) or b.startswith(m))
