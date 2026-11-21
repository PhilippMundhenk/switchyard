"""Enumerate IPv4 broadcast-capable interfaces (Linux, stdlib only)."""

from __future__ import annotations

import fcntl
import re
import socket
import struct
from dataclasses import dataclass
from pathlib import Path

SIOCGIFFLAGS = 0x8913
SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891B
IFF_UP = 0x1
IFF_BROADCAST = 0x2
IFF_LOOPBACK = 0x8


@dataclass(frozen=True)
class Interface:
    name: str
    ip: str
    mac: bytes
    netmask: str = ""


def _ioctl(sock: socket.socket, request: int, name: str) -> bytes:
    return fcntl.ioctl(sock.fileno(), request, struct.pack("256s", name.encode()[:15]))


def read_mac(name: str, sys_root: str = "/sys/class/net") -> bytes:
    try:
        text = (Path(sys_root) / name / "address").read_text().strip()
        return bytes.fromhex(text.replace(":", ""))[:6].rjust(6, b"\0")
    except (OSError, ValueError):
        return bytes(6)


def list_interfaces() -> list[Interface]:
    found = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _, name in socket.if_nameindex():
            try:
                flags = struct.unpack_from("H", _ioctl(sock, SIOCGIFFLAGS, name), 16)[0]
                if not flags & IFF_UP or flags & IFF_LOOPBACK or not flags & IFF_BROADCAST:
                    continue
                ip = socket.inet_ntoa(_ioctl(sock, SIOCGIFADDR, name)[20:24])
            except OSError:
                continue  # interface without an IPv4 address
            try:
                mask = socket.inet_ntoa(_ioctl(sock, SIOCGIFNETMASK, name)[20:24])
            except OSError:  # pragma: no cover - every IPv4 interface has a mask
                mask = ""
            found.append(Interface(name, ip, read_mac(name), mask))
    return found


# Bridges and tunnels of container and VM managers: switches are never behind them.
VIRTUAL = re.compile(r"^(docker\d*|br-[0-9a-f]+|veth|virbr|cni|flannel|vxlan|lxc|lxd|podman|kube)")


def is_virtual(name: str) -> bool:
    return bool(VIRTUAL.match(name))
