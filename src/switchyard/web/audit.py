"""Append-only audit log: who changed what and when. Never contains request bodies."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class Audit:
    def __init__(self, path: Path):
        self.path = path

    def record(self, role: str, method: str, route: str, mac: str | None, status: int) -> None:
        line = {
            "time": round(time.time(), 1),
            "role": role,
            "method": method,
            "route": route,
            "switch": mac,
            "status": status,
        }
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(json.dumps(line) + "\n")

    def tail(self, limit: int = 100) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for raw in self.path.read_text().splitlines()[-limit:]:
            try:
                rows.append(json.loads(raw))
            except ValueError:
                continue  # a torn last line after a crash
        return rows[::-1]
