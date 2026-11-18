"""Everything the web API needs: the registry, discovery and per-switch clients."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import time

from .config import Config
from .nsdp import SwitchClient, Transport, discover, parse_mac, probe
from .nsdp.client import AuthError, Unreachable
from .store import Store

log = logging.getLogger(__name__)

BACKUP_FORMAT = "switchyard-backup"
ONLINE_GRACE = 150.0


class NotFound(KeyError):
    """No managed switch with that MAC address."""


class Manager:
    def __init__(self, config: Config, transport: Transport, store: Store):
        self.config = config
        self.transport = transport
        self.store = store
        self.clients: dict[str, SwitchClient] = {}
        self.discovered: dict[str, dict] = {}
        self.seen: dict[str, float] = {}
        self.last_scan: float | None = None
        self._scan_lock = asyncio.Lock()
        self._poller: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        await self.transport.start()
        seconds = self.config["nsdp.poll_seconds"]
        if seconds > 0:
            self._poller = asyncio.create_task(self._poll(seconds))

    async def stop(self) -> None:
        if self._poller:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poller
        await self.transport.stop()

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
            found = await discover(self.transport, self.config["nsdp.discovery_seconds"])
            now = time.time()
            for device in self.store.devices.values():
                if device.get("address") and device["mac"] not in {f["mac"] for f in found}:
                    identity = await probe(self.transport, device["address"], 1.0)
                    if identity and identity["mac"] == device["mac"]:
                        found.append(identity)
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
        info = {**device.get("info", {}), **{k: v for k, v in identity.items() if k != "source_ip"}}
        if info != device.get("info"):
            self.store.update(mac, info=info, last_seen=self.seen[mac])

    def discovery(self) -> list[dict]:
        return [
            {**found, "managed": mac in self.store.devices}
            for mac, found in sorted(self.discovered.items())
        ]

    # -- registry ------------------------------------------------------------

    def view(self, device: dict) -> dict:
        mac = device["mac"]
        client = self.clients.get(mac)
        last_seen = self.seen.get(mac) or device.get("last_seen")
        return {
            "mac": mac,
            "label": device.get("label", ""),
            "notes": device.get("notes", ""),
            "address": device.get("address"),
            "has_password": bool(device.get("password") or (client and client.password)),
            "remembered": bool(device.get("password")),
            "online": bool(self.seen.get(mac) and time.time() - self.seen[mac] < ONLINE_GRACE),
            "last_seen": last_seen,
            "info": device.get("info", {}),
        }

    def devices(self) -> list[dict]:
        rows = [self.view(d) for d in self.store.devices.values()]
        return sorted(rows, key=lambda r: (r["label"] or r["info"].get("name") or r["mac"]).lower())

    def device(self, mac: str) -> dict:
        device = self.store.get(mac)
        if device is None:
            raise NotFound(mac)
        return device

    def client(self, mac: str) -> SwitchClient:
        device = self.device(mac)
        client = self.clients.get(mac)
        if client is None:
            client = SwitchClient(
                self.transport, parse_mac(mac), device.get("address"), device.get("password")
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
    ) -> dict:
        identity = None
        if address:
            address = str(ipaddress.IPv4Address(address.strip()))
            identity = await probe(self.transport, address)
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
        info = {k: v for k, v in (identity or {}).items() if k != "source_ip"}
        self.store.add(mac, label=label, address=address, info=info)
        if identity:
            self._note_seen(identity)
        if password:
            try:
                await self.login(mac, password, remember)
            except (AuthError, Unreachable) as exc:
                log.info("added %s but the password was not accepted: %s", mac, exc)
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
        return self.view(self.device(mac))

    def remove(self, mac: str) -> None:
        self.device(mac)
        self.clients.pop(mac, None)
        self.store.remove(mac)

    async def login(self, mac: str, password: str, remember: bool) -> dict:
        client = self.client(mac)
        await client.login(password)
        self.store.update(mac, password=password if remember else None)
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
            },
        }

    async def restore(self, mac: str, backup: dict, include_network: bool = False) -> list[dict]:
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
                if isinstance(exc, (AuthError, Unreachable)):
                    raise

        def ints(table: dict | None) -> dict:
            return {int(k): v for k, v in (table or {}).items()}

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
        mirror = config.get("mirror")
        if mirror:
            await step("Mirroring", client.set_mirror(mirror["destination"], mirror["sources"]))
        igmp = config.get("igmp")
        if igmp:
            await step(
                "IGMP snooping",
                client.set_igmp(
                    igmp["enabled"], igmp["vlan"] or 1, igmp["block_unknown"], igmp["validate_v3"]
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


def _normalise_mac(text: str) -> str:
    from .nsdp import format_mac

    return format_mac(parse_mac(text))
