"""The list of managed switches, kept in one JSON file."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.devices: dict[str, dict] = {}
        if path.exists():
            self.devices = json.loads(path.read_text()).get("devices", {})

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({"version": 1, "devices": self.devices}, handle, indent=2)
        os.replace(tmp, self.path)

    def get(self, mac: str) -> dict | None:
        return self.devices.get(mac)

    def add(self, mac: str, **fields) -> dict:
        device = self.devices.setdefault(
            mac,
            {
                "mac": mac,
                "label": "",
                "notes": "",
                "address": None,
                "password": None,
                "added": time.time(),
                "last_seen": None,
                "info": {},
            },
        )
        device.update(fields)
        self.save()
        return device

    def update(self, mac: str, **fields) -> dict:
        device = self.devices[mac]
        device.update(fields)
        self.save()
        return device

    def remove(self, mac: str) -> None:
        self.devices.pop(mac, None)
        self.save()
