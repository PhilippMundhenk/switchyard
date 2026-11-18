"""Background monitor: samples the ports of managed switches, keeps history and events."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from .notify import post_json
from .nsdp.client import NsdpError, PasswordRequired, Unreachable

if TYPE_CHECKING:
    from .manager import Manager

log = logging.getLogger(__name__)
EVENT_LIMIT = 500


class Monitor:
    def __init__(
        self, manager: Manager, interval: float, history_minutes: float, webhook_url: str = ""
    ):
        self.manager = manager
        self.interval = interval
        self.webhook_url = webhook_url
        size = int(max(history_minutes * 60 / max(interval, 1), 10)) if interval else 10
        self.samples: dict[str, deque] = {}
        self._size = size
        self.events: deque[dict] = deque(maxlen=EVENT_LIMIT)
        self.state: dict[str, dict] = {}
        self._event_id = 0
        self._task: asyncio.Task | None = None
        self._hooks: set[asyncio.Task] = set()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self.interval > 0:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        for task in [self._task, *self._hooks]:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _run(self) -> None:
        while True:
            try:
                await self.sample_all()
            except Exception:  # never let the loop die
                log.exception("monitor round failed")
            await asyncio.sleep(self.interval)

    async def sample_all(self) -> None:
        macs = list(self.manager.store.devices)
        await asyncio.gather(*(self.sample(mac) for mac in macs))

    # -- sampling ------------------------------------------------------------

    async def sample(self, mac: str) -> None:
        try:
            ports = await self.manager.client(mac).read_ports()
        except (Unreachable, NsdpError, PasswordRequired) as exc:
            self._set_online(mac, False, str(exc))
            return
        except KeyError:  # removed meanwhile
            return
        now = time.time()
        self.manager.seen[mac] = now
        self._set_online(mac, True)
        self.record(mac, ports, now)

    def record(self, mac: str, ports: list[dict], now: float) -> None:
        buf = self.samples.setdefault(mac, deque(maxlen=self._size))
        previous = self.state.setdefault(mac, {}).get("links")
        links = {p["port"]: (p.get("up", False), p.get("speed_mbps", 0)) for p in ports}
        if previous is not None:
            for port, (up, speed) in sorted(links.items()):
                was_up, was_speed = previous.get(port, (up, speed))
                if up != was_up:
                    kind = "link_up" if up else "link_down"
                    detail = f" at {_speed(speed)}" if up else ""
                    self.emit(mac, kind, f"Port {port} {'came up' if up else 'went down'}{detail}")
                elif up and speed != was_speed:
                    self.emit(
                        mac,
                        "speed_change",
                        f"Port {port} changed from {_speed(was_speed)} to {_speed(speed)}",
                    )
        self.state[mac]["links"] = links
        buf.append(
            {
                "t": now,
                "ports": {
                    p["port"]: (
                        p.get("rx_bytes", 0),
                        p.get("tx_bytes", 0),
                        p.get("crc_errors", 0),
                        p.get("up", False),
                        p.get("speed_mbps", 0),
                    )
                    for p in ports
                },
            }
        )

    def _set_online(self, mac: str, online: bool, why: str = "") -> None:
        state = self.state.setdefault(mac, {})
        was = state.get("online")
        state["online"] = online
        if was is None and online:
            return
        if was is not None and was != online:
            self.emit(
                mac,
                "online" if online else "offline",
                "Switch answers again" if online else f"Switch stopped answering ({why})",
            )

    # -- events --------------------------------------------------------------

    def emit(self, mac: str, kind: str, text: str) -> None:
        device = self.manager.store.get(mac) or {}
        info = device.get("info", {})
        self._event_id += 1
        event = {
            "id": self._event_id,
            "time": time.time(),
            "mac": mac,
            "switch": device.get("label") or info.get("name") or info.get("model") or mac,
            "type": kind,
            "text": text,
        }
        self.events.append(event)
        if self.webhook_url:
            task = asyncio.create_task(post_json(self.webhook_url, {"event": kind, **event}))
            self._hooks.add(task)
            task.add_done_callback(self._hooks.discard)

    def list_events(self, after: int = 0, mac: str | None = None) -> list[dict]:
        return [e for e in self.events if e["id"] > after and (mac is None or e["mac"] == mac)]

    # -- readers -------------------------------------------------------------

    def snapshot(self, mac: str) -> list[dict] | None:
        buf = self.samples.get(mac)
        if not buf:
            return None
        return [
            {"port": port, "up": row[3], "speed_mbps": row[4]}
            for port, row in sorted(buf[-1]["ports"].items())
        ]

    def history(self, mac: str, minutes: float = 60) -> dict:
        """Rates in bit/s between consecutive samples, per port and summed (port 0)."""
        buf = list(self.samples.get(mac, ()))
        cutoff = time.time() - minutes * 60
        series: dict[int, list[list[float]]] = {}
        for before, after in itertools.pairwise(buf):
            if after["t"] < cutoff:
                continue
            dt = after["t"] - before["t"]
            if dt <= 0:
                continue
            total_rx = total_tx = 0.0
            for port, row in after["ports"].items():
                old = before["ports"].get(port)
                if old is None or row[0] < old[0] or row[1] < old[1]:
                    continue  # counters were cleared or the switch restarted
                rx, tx = (row[0] - old[0]) * 8 / dt, (row[1] - old[1]) * 8 / dt
                series.setdefault(port, []).append([round(after["t"], 1), rx, tx])
                total_rx, total_tx = total_rx + rx, total_tx + tx
            series.setdefault(0, []).append([round(after["t"], 1), total_rx, total_tx])
        return {"interval": self.interval, "series": series}

    def metrics(self) -> str:
        lines = [
            "# HELP switchyard_switch_up 1 when the switch answered the last poll.",
            "# TYPE switchyard_switch_up gauge",
        ]
        rows = []
        for mac, device in self.manager.store.devices.items():
            info = device.get("info", {})
            model = _esc(info.get("model") or "")
            name = _esc(device.get("label") or info.get("name") or "")
            base = f'mac="{mac}",model="{model}",name="{name}"'
            up = 1 if self.state.get(mac, {}).get("online") else 0
            lines.append(f"switchyard_switch_up{{{base}}} {up}")
            buf = self.samples.get(mac)
            if buf:
                for port, (rx, tx, crc, link, speed) in sorted(buf[-1]["ports"].items()):
                    labels = f'mac="{mac}",port="{port}"'
                    rows += [
                        ("switchyard_port_up", "gauge", labels, int(link)),
                        ("switchyard_port_speed_mbps", "gauge", labels, speed),
                        ("switchyard_port_received_bytes_total", "counter", labels, rx),
                        ("switchyard_port_sent_bytes_total", "counter", labels, tx),
                        ("switchyard_port_crc_errors_total", "counter", labels, crc),
                    ]
        seen = set()
        for name, kind, labels, value in sorted(rows, key=lambda r: r[0]):
            if name not in seen:
                seen.add(name)
                lines.append(f"# TYPE {name} {kind}")
            lines.append(f"{name}{{{labels}}} {value}")
        return "\n".join(lines) + "\n"


def _speed(mbps: int) -> str:
    return f"{mbps // 1000} Gbit/s" if mbps >= 1000 else f"{mbps} Mbit/s"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
