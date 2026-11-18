"""Stored configuration backups: one JSON file per backup under data/backups/<mac>/."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

NAME = re.compile(r"^\d{8}-\d{6}-\d{3}\.json$")


class BackupStore:
    def __init__(self, root: Path, keep: int):
        self.root = root
        self.keep = keep

    def _dir(self, mac: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac):
            raise ValueError("Invalid MAC address.")
        return self.root / mac.replace(":", "")

    def path(self, mac: str, name: str) -> Path:
        if not NAME.match(name):
            raise ValueError("Invalid backup name.")
        return self._dir(mac) / name

    def save(self, mac: str, data: dict) -> str:
        folder = self._dir(mac)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        used = [int(f.name[16:19]) for f in folder.glob(f"{stamp}-*.json") if NAME.match(f.name)]
        name = f"{stamp}-{max(used, default=0) + 1:03d}.json"
        fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
        self.prune(mac)
        return name

    def list(self, mac: str) -> list[dict]:
        folder = self._dir(mac)
        if not folder.is_dir():
            return []
        rows = []
        for item in sorted(folder.iterdir(), reverse=True):
            if NAME.match(item.name):
                stat = item.stat()
                rows.append({"name": item.name, "size": stat.st_size, "created": stat.st_mtime})
        return rows

    def read(self, mac: str, name: str) -> dict:
        path = self.path(mac, name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return json.loads(path.read_text())

    def delete(self, mac: str, name: str) -> None:
        path = self.path(mac, name)
        if not path.is_file():
            raise FileNotFoundError(name)
        path.unlink()

    def prune(self, mac: str) -> None:
        for row in self.list(mac)[self.keep :]:
            self.path(mac, row["name"]).unlink(missing_ok=True)

    def latest(self, mac: str) -> dict | None:
        rows = self.list(mac)
        return self.read(mac, rows[0]["name"]) if rows else None
