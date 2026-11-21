"""Everything the web API needs: the registry, discovery and per-switch clients."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import time

from .backups import BackupStore
from .config import Config
from .diff import diff_config
from .drivers import AuthError, Backend, PasswordRequired, SwitchDriver, Unreachable
from .drivers.nsdp import NsdpBackend
from .monitor import Monitor
from .nsdp import Transport
from .nsdp import firmware as fw
from .store import Store

log = logging.getLogger(__name__)

BACKUP_FORMAT = "switchyard-backup"
ONLINE_GRACE = 150.0
MAX_FIRMWARE_BYTES = 16 * 1024 * 1024


class NotFound(KeyError):
    """No managed switch with that MAC address."""


class Manager:
    def __init__(
        self,
        config: Config,
        transport: Transport,
        store: Store,
        backends: list[Backend] | None = None,
    ):
        self.config = config
        self.transport = transport  # NSDP: kept for its settings and routes
        self.store = store
        self.backends: dict[str, Backend] = {
            b.protocol: b for b in [NsdpBackend(transport), *(backends or [])]
        }
        self.clients: dict[str, SwitchDriver] = {}
        self.discovered: dict[str, dict] = {}
        self.seen: dict[str, float] = {}
        self.last_scan: float | None = None
        self._scan_lock = asyncio.Lock()
        self._poller: asyncio.Task | None = None
        self.monitor = Monitor(
            self,
            config["monitor.interval_seconds"],
            config["monitor.history_minutes"],
            config["notify.webhook_url"],
        )
        self.backups = BackupStore(config.data_dir / "backups", config["backup.keep"])
        self._backup_task: asyncio.Task | None = None
        self.upgrades: dict[str, dict] = {}
        self.listeners: list = []  # called with (mac, removed) when the list of switches changes
        self._upgrade_tasks: set[asyncio.Task] = set()
        self.upgrade_settle = 2.0  # pause between 'upgrade mode' and the upload
        self.upgrade_wait = 300.0  # how long to wait for the switch to come back
        self.upgrade_poll = 3.0
        self.backup_tick = 60.0  # how often the backup scheduler looks

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        for backend in self.backends.values():
            await backend.start()
        seconds = self.config["nsdp.poll_seconds"]
        if seconds > 0:
            self._poller = asyncio.create_task(self._poll(seconds))
        self.monitor.start()
        if self.config["backup.interval_hours"] > 0:
            self._backup_task = asyncio.create_task(self._backup_loop())

    async def stop(self) -> None:
        await self.monitor.stop()
        if self._backup_task:
            self._backup_task.cancel()
        for task in list(self._upgrade_tasks):
            task.cancel()
        if self._poller:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poller
        for backend in self.backends.values():
            await backend.stop()

    async def _poll(self, seconds: float) -> None:
        while True:
            try:
                await self.scan()
            except Exception:  # keep polling whatever happens
                log.exception("background search failed")
            await asyncio.sleep(seconds)

    # -- discovery -----------------------------------------------------------

    async def scan(self) -> list[dict]:
        async with self._scan_lock:
            seconds = self.config["nsdp.discovery_seconds"]
            results = await asyncio.gather(
                *(b.discover(seconds) for b in self.backends.values()), return_exceptions=True
            )
            found: list[dict] = []
            for backend, result in zip(self.backends.values(), results, strict=True):
                if isinstance(result, BaseException):
                    log.warning("%s search failed: %s", backend.vendor, result)
                    continue
                found += [{**identity, "protocol": backend.protocol} for identity in result]
            now = time.time()
            for device in self.store.devices.values():
                backend = self._backend(device)
                if device.get("address") and device["mac"] not in {f["mac"] for f in found}:
                    identity = await backend.probe(device["address"], 1.0)
                    if identity and identity["mac"] == device["mac"]:
                        found.append({**identity, "protocol": backend.protocol})
            self.discovered = {f["mac"]: f for f in found}
            self.last_scan = now
            for identity in found:
                self._note_seen(identity)
            return self.discovery()

    def _note_seen(self, identity: dict) -> None:
        mac = identity["mac"]
        self.seen[mac] = time.time()
        device = self.store.get(mac)
        if device is None:
            return
        info = {
            **device.get("info", {}),
            **{k: v for k, v in identity.items() if k not in ("source_ip", "protocol")},
        }
        if info != device.get("info"):
            self.store.update(mac, info=info, last_seen=self.seen[mac])
            self._changed(mac)

    def discovery(self) -> list[dict]:
        return [
            {**found, "managed": mac in self.store.devices}
            for mac, found in sorted(self.discovered.items())
        ]

    # -- registry ------------------------------------------------------------

    def view(self, device: dict) -> dict:
        mac = device["mac"]
        client = self.clients.get(mac)
        backend = self.backends.get(device.get("protocol", "nsdp"))
        last_seen = self.seen.get(mac) or device.get("last_seen")
        return {
            "mac": mac,
            "label": device.get("label", ""),
            "notes": device.get("notes", ""),
            "address": device.get("address"),
            "protocol": device.get("protocol", "nsdp"),
            "vendor": backend.vendor if backend else None,
            "needs_username": bool(backend and backend.needs_username),
            "unicast": bool(backend is None or backend.unicast),
            "username": device.get("username"),
            "has_password": bool(device.get("password") or (client and client.password)),
            "remembered": bool(device.get("password")),
            "online": bool(self.seen.get(mac) and time.time() - self.seen[mac] < ONLINE_GRACE),
            "last_seen": last_seen,
            "info": device.get("info", {}),
            "ports": self.monitor.snapshot(mac),
            "port_labels": device.get("port_labels", {}),
            "vlan_names": device.get("vlan_names", {}),
        }

    def devices(self) -> list[dict]:
        rows = [self.view(d) for d in self.store.devices.values()]
        return sorted(rows, key=lambda r: (r["label"] or r["info"].get("name") or r["mac"]).lower())

    def device(self, mac: str) -> dict:
        device = self.store.get(mac)
        if device is None:
            raise NotFound(mac)
        return device

    def _backend(self, device: dict) -> Backend:
        protocol = device.get("protocol", "nsdp")
        if protocol not in self.backends:
            raise ValueError(f"Switchyard cannot talk to this switch ({protocol} is off).")
        return self.backends[protocol]

    def client(self, mac: str) -> SwitchDriver:
        device = self.device(mac)
        client = self.clients.get(mac)
        if client is None:
            client = self._backend(device).driver(
                mac, device.get("address"), device.get("password"), device.get("username")
            )
            self.clients[mac] = client
        return client

    async def add(
        self,
        mac: str | None = None,
        address: str | None = None,
        label: str = "",
        password: str | None = None,
        remember: bool = True,
        username: str | None = None,
    ) -> dict:
        identity = None
        if address:
            address = str(ipaddress.IPv4Address(address.strip()))
            identity = await self.backends["nsdp"].probe(address)
            if identity is not None:
                identity = {**identity, "protocol": "nsdp"}
            if identity is None:
                raise Unreachable(f"No switch answers at {address}.")
            if mac and identity["mac"] != mac:
                raise ValueError(f"The switch at {address} has a different MAC address.")
            mac = identity["mac"]
        elif mac:
            mac = _normalise_mac(mac)
            identity = self.discovered.get(mac)
        else:
            raise ValueError("Give a MAC address from the search or an IP address.")
        if mac in self.store.devices:
            raise ValueError("This switch is already in the list.")
        protocol = (identity or {}).get("protocol", "nsdp")
        info = {k: v for k, v in (identity or {}).items() if k not in ("source_ip", "protocol")}
        self.store.add(mac, label=label, address=address, info=info, protocol=protocol)
        if identity:
            self._note_seen(identity)
        if password:
            try:
                await self.login(mac, password, remember, username)
            except (AuthError, Unreachable) as exc:
                log.info("added %s but the password was not accepted: %s", mac, exc)
        self._changed(mac)
        return self.view(self.device(mac))

    def update(self, mac: str, **fields) -> dict:
        device = self.device(mac)
        changes = {k: v for k, v in fields.items() if v is not None and k in ("label", "notes")}
        if "address" in fields:
            address = fields["address"]
            changes["address"] = str(ipaddress.IPv4Address(address.strip())) if address else None
            self.clients.pop(mac, None)
        if fields.get("forget_password"):
            changes["password"] = None
            self.clients.pop(mac, None)
        self.store.update(device["mac"], **changes)
        self._changed(mac)
        return self.view(self.device(mac))

    def _changed(self, mac: str, removed: bool = False) -> None:
        for listener in list(self.listeners):
            try:
                listener(mac, removed)
            except Exception:
                log.exception("registry listener failed")

    def set_labels(self, mac: str, ports: dict[int, str], vlans: dict[int, str]) -> dict:
        """Local names for ports and VLANs (kept in devices.json, not on the switch)."""
        self.device(mac)
        clean = {
            "port_labels": {str(p): t.strip()[:30] for p, t in ports.items() if t.strip()},
            "vlan_names": {str(v): t.strip()[:30] for v, t in vlans.items() if t.strip()},
        }
        view = self.view(self.store.update(mac, **clean))
        self._changed(mac)
        return view

    def remove(self, mac: str) -> None:
        self.device(mac)
        self.clients.pop(mac, None)
        self.store.remove(mac)
        self._changed(mac, removed=True)

    async def login(
        self, mac: str, password: str, remember: bool, username: str | None = None
    ) -> dict:
        client = self.client(mac)
        await client.login(password, username)
        self.store.update(
            mac, password=password if remember else None, username=client.username or None
        )
        self.seen[mac] = time.time()
        return self.view(self.device(mac))

    # -- per-switch helpers --------------------------------------------------

    async def state(self, mac: str) -> dict:
        state = await self.client(mac).read_state()
        self.seen[mac] = time.time()
        identity = {
            k: state["info"][k]
            for k in ("mac", "model", "name", "ip", "netmask", "gateway", "dhcp", "firmware")
        }
        self._note_seen({**identity, "ports": state["info"]["ports"]})
        return {"device": self.view(self.device(mac)), **state}

    async def change_password(self, mac: str, new_password: str) -> None:
        client = self.client(mac)
        await client.change_password(new_password)
        if self.device(mac).get("password"):
            self.store.update(mac, password=new_password)

    async def backup(self, mac: str) -> dict:
        state = await self.client(mac).read_state()
        info = state["info"]
        return {
            "format": BACKUP_FORMAT,
            "version": 1,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "switch": {k: info[k] for k in ("model", "mac", "firmware", "ports")},
            "config": {
                "network": {k: info[k] for k in ("name", "dhcp", "ip", "netmask", "gateway")},
                "vlan": state["vlan"],
                "qos": state["qos"],
                "mirror": state["mirror"],
                "igmp": state["igmp"],
                "loop_detection": state["loop_detection"],
                "lag": state["lag"],
                "port_admin": state["port_admin"],
                "labels": {
                    "ports": self.device(mac).get("port_labels", {}),
                    "vlans": self.device(mac).get("vlan_names", {}),
                },
            },
        }

    async def restore(self, mac: str, backup: dict, include_network: bool = False) -> list[dict]:
        try:
            return await self._restore(mac, backup, include_network)
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            raise ValueError(f"The backup file is incomplete or damaged ({exc!r}).") from exc

    async def _restore(self, mac: str, backup: dict, include_network: bool) -> list[dict]:
        if backup.get("format") != BACKUP_FORMAT or backup.get("version") != 1:
            raise ValueError("This file is not a Switchyard backup.")
        client = self.client(mac)
        info = await client.read_info()
        if backup.get("switch", {}).get("ports") != info["ports"]:
            raise ValueError("The backup is from a switch with a different number of ports.")
        config = backup.get("config", {})
        steps: list[dict] = []

        async def step(name: str, action) -> None:
            try:
                await action
                steps.append({"step": name, "ok": True})
            except Exception as exc:
                steps.append({"step": name, "ok": False, "error": str(exc)})
                if isinstance(exc, (AuthError, Unreachable, PasswordRequired)):
                    raise

        def ints(table: dict | None) -> dict:
            return {int(k): v for k, v in (table or {}).items()}

        labels = config.get("labels")
        if labels:
            ports = {int(k): v for k, v in labels.get("ports", {}).items()}
            vlans = {int(k): v for k, v in labels.get("vlans", {}).items()}
            self.set_labels(mac, ports, vlans)
            steps.append({"step": "Port and VLAN names", "ok": True})
        admin = config.get("port_admin")
        if admin:
            await step(
                "Port speed and flow control",
                client.set_port_admin(
                    {
                        int(p): {"speed_code": a["speed_code"], "flow_control": a["flow_control"]}
                        for p, a in admin.items()
                    }
                ),
            )
        vlan = config.get("vlan")
        if vlan:
            current = await client.read_vlan()
            if current and current["mode"] != vlan["mode"]:
                await step("VLAN mode", client.set_vlan_mode(vlan["mode"]))
            if vlan["mode"].startswith("port") and vlan.get("port_vlans"):
                await step("Port-based VLANs", client.set_port_vlans(vlan["port_vlans"]))
            elif vlan["mode"].startswith("dot1q"):
                await step("802.1Q VLANs", client.apply_dot1q(vlan["dot1q"], ints(vlan["pvid"])))
        qos = config.get("qos")
        if qos:
            await step(
                "QoS",
                client.set_qos(
                    qos["mode"],
                    ints(qos["priorities"]),
                    ints(qos["ingress"]),
                    ints(qos["egress"]),
                    qos["storm_control"],
                    ints(qos["storm_rates"]),
                ),
            )
        lag = config.get("lag")
        if lag:
            await step("Link aggregation", client.apply_lags(lag["lags"]))
        mirror = config.get("mirror")
        if mirror:
            await step("Mirroring", client.set_mirror(mirror["destination"], mirror["sources"]))
        igmp = config.get("igmp")
        if igmp:
            await step(
                "IGMP snooping",
                client.set_igmp(
                    igmp["enabled"],
                    igmp["vlan"] or 1,
                    igmp["block_unknown"],
                    igmp["validate_v3"],
                    # TP-Link only
                    **(
                        {"report_suppression": igmp["report_suppression"]}
                        if "report_suppression" in igmp
                        else {}
                    ),
                ),
            )
        if config.get("loop_detection") is not None:
            await step("Loop detection", client.set_loop_detection(config["loop_detection"]))
        network = config.get("network")
        if include_network and network:
            await step(
                "Name and IP settings",
                client.set_network(
                    network["name"],
                    network["dhcp"],
                    network["ip"],
                    network["netmask"],
                    network["gateway"],
                ),
            )
        return steps

    # -- stored backups ------------------------------------------------------

    async def backup_now(self, mac: str) -> str:
        return self.backups.save(mac, await self.backup(mac))

    async def backup_due(self, mac: str, interval_hours: float) -> bool:
        rows = self.backups.list(mac)
        return not rows or time.time() - rows[0]["created"] >= interval_hours * 3600

    async def _backup_loop(self) -> None:
        """Every minute: back up the online switches whose last backup is too old and changed."""
        await asyncio.sleep(2)  # let discovery settle after a start
        while True:
            for mac in list(self.store.devices):
                try:
                    if not await self.backup_due(mac, self.config["backup.interval_hours"]):
                        continue
                    data = await self.backup(mac)
                    last = self.backups.latest(mac)
                    # compare as JSON: stored files turn integer keys into strings
                    if last is None or last.get("config") != json.loads(json.dumps(data["config"])):
                        self.backups.save(mac, data)
                    else:  # unchanged: touch nothing, but do not retry every minute
                        rows = self.backups.list(mac)
                        os.utime(self.backups.path(mac, rows[0]["name"]))
                except Exception as exc:
                    log.info("scheduled backup of %s skipped: %s", mac, exc)
            await asyncio.sleep(self.backup_tick)

    async def diff(self, mac: str, other: dict) -> list[dict]:
        if other.get("format") != BACKUP_FORMAT or other.get("version") != 1:
            raise ValueError("This file is not a Switchyard backup.")
        live = await self.backup(mac)
        try:
            return diff_config(live["config"], other["config"])
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"The backup file is incomplete or damaged ({exc!r}).") from exc

    # -- firmware ------------------------------------------------------------

    async def check_firmware(self, mac: str, data: bytes) -> dict:
        """Look at an image without touching the switch (only its identity is read)."""
        if len(data) > MAX_FIRMWARE_BYTES:
            raise fw.FirmwareError("The file is too large to be a firmware image.")
        image = fw.parse(data)
        info = await self.client(mac).read_info()
        return {
            "image": image.as_dict(),
            "switch": {"model": info["model"], "firmware": info["firmware"]},
            "matches": fw.matches_model(image.board, info["model"]),
            "same_version": image.version == info["firmware"],
        }

    async def start_upgrade(self, mac: str, data: bytes, force: bool, filename: str) -> dict:
        if len(data) > MAX_FIRMWARE_BYTES:
            raise fw.FirmwareError("The file is too large to be a firmware image.")
        image = fw.parse(data)
        if self.upgrades.get(mac, {}).get("state") in ("running",):
            raise ValueError("A firmware upgrade is already running on this switch.")
        client = self.client(mac)
        await client.write([])  # fail early on a wrong or missing password
        info = await client.read_info()
        if not force and not fw.matches_model(image.board, info["model"]):
            raise fw.FirmwareError(
                f"This image is for {image.board or 'another board'}, "
                f"the switch is a {info['model']}."
            )
        status = {
            "state": "running",
            "step": "starting",
            "sent": 0,
            "total": len(data),
            "from_version": info["firmware"],
            "image_version": image.version,
            "version": None,
            "error": None,
            "started": time.time(),
        }
        self.upgrades[mac] = status
        task = asyncio.create_task(self._run_upgrade(mac, client, data, force, filename, status))
        self._upgrade_tasks.add(task)
        task.add_done_callback(self._upgrade_tasks.discard)
        return status

    async def _run_upgrade(self, mac, client, data, force, filename, status) -> None:
        def progress(step: str, sent: int, total: int) -> None:
            status.update(step=step, sent=sent, total=total)

        try:
            await client.upgrade_firmware(data, filename, progress, force, self.upgrade_settle)
            status.update(step="restarting", sent=status["total"])
            deadline = time.monotonic() + self.upgrade_wait
            while time.monotonic() < deadline:
                await asyncio.sleep(self.upgrade_poll)
                try:
                    info = await client.read_info()
                except Unreachable:
                    continue
                status.update(state="done", step="finished", version=info["firmware"])
                self.seen[mac] = time.time()
                self._note_seen({**info, "mac": info["mac"]})
                return
            status.update(
                state="failed",
                error="The switch did not come back after the upgrade. Wait a few minutes, "
                "then search the network again. Do not power it off while it is flashing.",
            )
        except asyncio.CancelledError:
            status.update(state="failed", error="Cancelled because Switchyard stopped.")
            raise
        except Exception as exc:
            log.warning("firmware upgrade of %s failed: %s", mac, exc)
            status.update(state="failed", error=str(exc))

    def upgrade_status(self, mac: str) -> dict:
        self.device(mac)
        return self.upgrades.get(mac, {"state": "idle"})


def _normalise_mac(text: str) -> str:
    from .nsdp import format_mac, parse_mac

    return format_mac(parse_mac(text))
