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
        self.listeners: list = []  # called with the MAC after every sample or state change
        self.read_admin = False  # also read port settings (for the MQTT port switches)

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
        if self.read_admin:
            with contextlib.suppress(Unreachable, NsdpError, PasswordRequired):
                self.state[mac]["admin"] = await self.manager.client(mac).read_port_admin()
        self._notify(mac)

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
                        p.get("rx_bytes", 0),  # None: the switch counts packets only
                        p.get("tx_bytes", 0),
                        p.get("errors", 0) if p.get("crc_errors") is None else p["crc_errors"],
                        p.get("up", False),
                        p.get("speed_mbps", 0),
                        p.get("rx_packets"),
                        p.get("tx_packets"),
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
        if was is not None and was != online and not online:
            self._notify(mac)
        if was is not None and was != online:
            self.emit(
                mac,
                "online" if online else "offline",
                "Switch answers again" if online else f"Switch stopped answering ({why})",
            )

    def _notify(self, mac: str) -> None:
        for listener in list(self.listeners):
            try:
                listener(mac)
            except Exception:  # a broken listener must not stop monitoring
                log.exception("monitor listener failed")

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

    def rates(self, mac: str) -> dict[int, tuple[float, float]]:
        """bit/s received and sent per port between the last two samples."""
        buf = self.samples.get(mac)
        if not buf or len(buf) < 2:
            return {}
        before, after = buf[-2], buf[-1]
        dt = after["t"] - before["t"]
        out = {}
        for port, row in after["ports"].items():
            old = before["ports"].get(port)
            if row[0] is None or old is None or old[0] is None:
                continue  # packet counters only: no bit rate
            if dt > 0 and row[0] >= old[0] and row[1] >= old[1]:
                out[port] = ((row[0] - old[0]) * 8 / dt, (row[1] - old[1]) * 8 / dt)
        return out

    def counter(self, mac: str, port: int, name: str) -> int | None:
        buf = self.samples.get(mac)
        row = buf[-1]["ports"].get(port) if buf else None
        index = {"rx": 0, "tx": 1, "crc": 2, "rx_packets": 5, "tx_packets": 6}[name]
        return None if row is None or len(row) <= index else row[index]

    def history(self, mac: str, minutes: float = 60) -> dict:
        """Rates between consecutive samples, per port and summed (port 0).

        bit/s; packets/s for switches that only count packets (TP-Link Easy Smart).
        """
        buf = list(self.samples.get(mac, ()))
        packets = bool(buf) and any(r[0] is None for r in buf[-1]["ports"].values())
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
                (a, b, factor) = (5, 6, 1) if packets else (0, 1, 8)
                if old is None or len(row) <= b or len(old) <= b:
                    continue
                if None in (row[a], row[b], old[a], old[b]) or row[a] < old[a] or row[b] < old[b]:
                    continue  # counters were cleared or the switch restarted
                rx, tx = (row[a] - old[a]) * factor / dt, (row[b] - old[b]) * factor / dt
                series.setdefault(port, []).append([round(after["t"], 1), rx, tx])
                total_rx, total_tx = total_rx + rx, total_tx + tx
            series.setdefault(0, []).append([round(after["t"], 1), total_rx, total_tx])
        unit = "packets/s" if packets else "bit/s"
        return {"interval": self.interval, "series": series, "unit": unit}

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
                for port, row in sorted(buf[-1]["ports"].items()):
                    rx, tx, crc, link, speed = row[:5]
                    rx_packets, tx_packets = (*row, None, None)[5:7]
                    labels = f'mac="{mac}",port="{port}"'
                    candidates = [
                        ("switchyard_port_up", "gauge", labels, int(link)),
                        ("switchyard_port_speed_mbps", "gauge", labels, speed),
                        ("switchyard_port_received_bytes_total", "counter", labels, rx),
                        ("switchyard_port_sent_bytes_total", "counter", labels, tx),
                        ("switchyard_port_crc_errors_total", "counter", labels, crc),
                        ("switchyard_port_received_packets_total", "counter", labels, rx_packets),
                        ("switchyard_port_sent_packets_total", "counter", labels, tx_packets),
                    ]
                    rows += [r for r in candidates if r[3] is not None]
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
