"""Publish switch and port state to MQTT, with Home Assistant discovery.

Optional: active only when ``mqtt.url`` is set. State comes from the background monitor;
commands (reboot, port on/off) are only accepted when ``mqtt.allow_control`` is true.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
import time
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import paho.mqtt.client as paho

from . import __version__

if TYPE_CHECKING:
    from .manager import Manager

log = logging.getLogger(__name__)
SUPPORT_URL = "https://github.com/PhilippMundhenk/switchyard"


def parse_url(url: str) -> dict:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("mqtt", "mqtts") or not parts.hostname:
        raise ValueError("mqtt.url must look like mqtt://broker:1883 or mqtts://broker:8883")
    return {
        "host": parts.hostname,
        "port": parts.port or (8883 if parts.scheme == "mqtts" else 1883),
        "tls": parts.scheme == "mqtts",
        "username": unquote(parts.username) if parts.username else None,
        "password": unquote(parts.password) if parts.password else None,
    }


def mac_id(mac: str) -> str:
    return mac.replace(":", "").lower()


class MqttBridge:
    def __init__(
        self,
        manager: Manager,
        config,
        audit: Callable[[str, str, str | None, int], None] | None = None,
    ):
        self.manager = manager
        self.url = config["mqtt.url"]
        self.prefix = config["mqtt.topic_prefix"].strip("/") or "switchyard"
        self.discovery = config["mqtt.discovery_prefix"].strip("/") or "homeassistant"
        self.allow_control = config["mqtt.allow_control"]
        self.base_url = config["mqtt.base_url"]
        self.username = config["mqtt.username"] or None
        self.password = config["mqtt.password"] or None
        self.audit = audit
        self.client: paho.Client | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sent_topics: dict[str, set[str]] = {}
        self.port_counts: dict[str, int] = {}
        self.status = {"state": "off", "broker": None, "error": None, "published": 0}
        if self.url:
            target = parse_url(self.url)
            self.target = target
            self.username = self.username or target["username"]
            self.password = self.password or target["password"]
            self.status["broker"] = f"{target['host']}:{target['port']}"

    # -- lifecycle -----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def start(self) -> None:
        if not self.enabled:
            return
        self.loop = asyncio.get_running_loop()
        seed = str(self.manager.config.data_dir.resolve()).encode()
        client = paho.Client(
            paho.CallbackAPIVersion.VERSION2,
            client_id=f"switchyard-{hashlib.sha256(seed).hexdigest()[:10]}",
        )
        if self.username:
            client.username_pw_set(self.username, self.password)
        if self.target["tls"]:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.will_set(f"{self.prefix}/status", "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(1, 60)
        self.client = client
        self.status["state"] = "connecting"
        client.connect_async(self.target["host"], self.target["port"], keepalive=60)
        client.loop_start()
        self.manager.monitor.listeners.append(self._on_monitor)
        self.manager.listeners.append(self._on_registry)

    async def stop(self) -> None:
        if not self.client:
            return
        try:
            self.client.publish(f"{self.prefix}/status", "offline", qos=1, retain=True)
            self.client.disconnect()
        finally:
            await asyncio.to_thread(self.client.loop_stop)
            self.client = None
            self.status["state"] = "off"

    # -- paho callbacks (network thread) -------------------------------------

    def _on_connect(self, client, userdata, flags, reason, properties=None) -> None:
        if reason.is_failure:
            self.status.update(state="error", error=str(reason))
            log.warning("MQTT connection refused: %s", reason)
            return
        self.status.update(state="connected", error=None)
        log.info("MQTT connected to %s", self.status["broker"])
        client.publish(f"{self.prefix}/status", "online", qos=1, retain=True)
        if self.allow_control:
            client.subscribe(f"{self.prefix}/+/reboot/press", qos=1)
            client.subscribe(f"{self.prefix}/+/port/+/set", qos=1)
        self.loop.call_soon_threadsafe(self.publish_all)

    def _on_disconnect(self, client, userdata, flags, reason, properties=None) -> None:
        if self.status["state"] != "off":
            self.status.update(state="connecting", error=str(reason) if reason else None)

    def _on_message(self, client, userdata, message) -> None:
        asyncio.run_coroutine_threadsafe(
            self.handle_command(message.topic, message.payload.decode(errors="replace")),
            self.loop,
        )

    # -- publishing ----------------------------------------------------------

    def _publish(self, topic: str, payload, retain: bool = True) -> None:
        if not self.client:
            return
        if not isinstance(payload, str):
            payload = json.dumps(payload, separators=(",", ":"))
        self.client.publish(topic, payload, qos=1, retain=retain)
        self.status["published"] += 1
        self.status["last_publish"] = time.time()

    def publish_all(self) -> None:
        for mac in list(self.manager.store.devices):
            self.publish_discovery(mac)
            self.publish_state(mac)

    def _on_registry(self, mac: str, removed: bool) -> None:
        if removed:
            self.remove(mac)
        else:
            self.publish_discovery(mac)
            self.publish_state(mac)

    def _on_monitor(self, mac: str) -> None:
        if self._port_count(mac) != self.port_counts.get(mac):
            self.publish_discovery(mac)  # the first sample tells how many ports there are
        self.publish_state(mac)

    def _port_count(self, mac: str) -> int:
        info = (self.manager.store.get(mac) or {}).get("info", {})
        return int(info.get("ports") or len(self.manager.monitor.snapshot(mac) or []) or 0)

    def remove(self, mac: str) -> None:
        for topic in self.sent_topics.pop(mac, set()):
            self._publish(topic, "")  # an empty retained config removes the entity
        self._publish(f"{self.prefix}/{mac_id(mac)}/state", "")
        self._publish(f"{self.prefix}/{mac_id(mac)}/availability", "")

    def state(self, mac: str) -> dict:
        device = self.manager.store.get(mac) or {}
        info = device.get("info", {})
        monitor = self.manager.monitor
        online = bool(monitor.state.get(mac, {}).get("online"))
        rates = monitor.rates(mac)
        admin = monitor.state.get(mac, {}).get("admin") or {}
        ports = {}
        for row in monitor.snapshot(mac) or []:
            port = row["port"]
            rx, tx = rates.get(port, (None, None))
            entry = {
                "link": row["up"],
                "speed": row["speed_mbps"] if row["up"] else 0,
                "rx_bps": None if rx is None else round(rx),
                "tx_bps": None if tx is None else round(tx),
                "crc": monitor.counter(mac, port, "crc"),
            }
            if port in admin:
                entry["enabled"] = admin[port]["speed_code"] != 1
            ports[str(port)] = entry
        return {
            "online": online,
            "model": info.get("model"),
            "firmware": info.get("firmware"),
            "ip": info.get("ip"),
            "ports_up": sum(1 for p in ports.values() if p["link"]),
            "ports": ports,
            "updated": round(time.time()),
        }

    def publish_state(self, mac: str) -> None:
        if not self.client or mac not in self.manager.store.devices:
            return
        state = self.state(mac)
        self._publish(f"{self.prefix}/{mac_id(mac)}/state", state)
        self._publish(
            f"{self.prefix}/{mac_id(mac)}/availability", "online" if state["online"] else "offline"
        )

    def discovery_configs(self, mac: str) -> dict[str, dict]:
        """Topic -> Home Assistant MQTT discovery config for one switch."""
        device = self.manager.store.get(mac) or {}
        info = device.get("info", {})
        node = f"switchyard_{mac_id(mac)}"
        base = f"{self.prefix}/{mac_id(mac)}"
        labels = device.get("port_labels", {})
        name = device.get("label") or info.get("name") or info.get("model") or mac
        dev = {
            "identifiers": [node],
            "connections": [["mac", mac]],
            "name": name,
            "manufacturer": "NETGEAR",
            "model": info.get("model") or "ProSAFE Plus switch",
            "sw_version": info.get("firmware"),
        }
        if self.base_url:
            dev["configuration_url"] = f"{self.base_url.rstrip('/')}/#/switch/{mac}"
        origin = {"name": "Switchyard", "sw_version": __version__, "support_url": SUPPORT_URL}
        bridge = {"topic": f"{self.prefix}/status"}
        switch_up = {"topic": f"{base}/availability"}
        out: dict[str, dict] = {}

        def add(component: str, object_id: str, config: dict, follow_switch: bool = True) -> None:
            entity = {
                "unique_id": f"{node}_{object_id}",
                "default_entity_id": f"{component}.{node}_{object_id}",
                "state_topic": f"{base}/state",
                "device": dev,
                "origin": origin,
                "availability": [bridge, switch_up] if follow_switch else [bridge],
                "availability_mode": "all",
                **config,
            }
            out[f"{self.discovery}/{component}/{node}/{object_id}/config"] = entity

        add(
            "binary_sensor",
            "online",
            {
                "name": "Online",
                "device_class": "connectivity",
                "value_template": "{{ 'ON' if value_json.online else 'OFF' }}",
            },
            follow_switch=False,
        )
        add(
            "sensor",
            "firmware",
            {
                "name": "Firmware",
                "icon": "mdi:chip",
                "entity_category": "diagnostic",
                "value_template": "{{ value_json.firmware }}",
            },
        )
        add(
            "sensor",
            "ports_up",
            {
                "name": "Ports up",
                "icon": "mdi:ethernet",
                "state_class": "measurement",
                "value_template": "{{ value_json.ports_up }}",
            },
        )
        for port in range(1, self._port_count(mac) + 1):
            label = labels.get(str(port))
            title = f"Port {port}" + (f" ({label})" if label else "")
            path = f"value_json.ports['{port}']"
            add(
                "binary_sensor",
                f"port{port}_link",
                {
                    "name": f"{title} link",
                    "device_class": "connectivity",
                    "value_template": f"{{{{ 'ON' if {path}.link else 'OFF' }}}}",
                },
            )
            add(
                "sensor",
                f"port{port}_speed",
                {
                    "name": f"{title} speed",
                    "device_class": "data_rate",
                    "unit_of_measurement": "Mbit/s",
                    "state_class": "measurement",
                    "value_template": f"{{{{ {path}.speed }}}}",
                },
            )
            for key, word in (("rx_bps", "received"), ("tx_bps", "sent")):
                add(
                    "sensor",
                    f"port{port}_{key[:2]}",
                    {
                        "name": f"{title} {word}",
                        "device_class": "data_rate",
                        "unit_of_measurement": "bit/s",
                        "state_class": "measurement",
                        "suggested_display_precision": 0,
                        "value_template": (
                            f"{{{{ {path}.{key} if {path}.{key} is not none else 'unknown' }}}}"
                        ),
                    },
                )
            add(
                "sensor",
                f"port{port}_crc",
                {
                    "name": f"{title} CRC errors",
                    "icon": "mdi:alert-circle-outline",
                    "state_class": "total_increasing",
                    "entity_category": "diagnostic",
                    "value_template": f"{{{{ {path}.crc }}}}",
                },
            )
            if self.allow_control:
                add(
                    "switch",
                    f"port{port}_enabled",
                    {
                        "name": f"{title} enabled",
                        "icon": "mdi:ethernet-cable",
                        "entity_category": "config",
                        "command_topic": f"{base}/port/{port}/set",
                        "payload_on": "ON",
                        "payload_off": "OFF",
                        "value_template": f"{{{{ 'ON' if {path}.enabled else 'OFF' }}}}",
                    },
                )
        if self.allow_control:
            add(
                "button",
                "reboot",
                {
                    "name": "Restart",
                    "device_class": "restart",
                    "entity_category": "config",
                    "command_topic": f"{base}/reboot/press",
                    "payload_press": "PRESS",
                },
            )
            del out[f"{self.discovery}/button/{node}/reboot/config"]["state_topic"]
        return out

    def publish_discovery(self, mac: str) -> None:
        if not self.client or mac not in self.manager.store.devices:
            return
        configs = self.discovery_configs(mac)
        old = self.sent_topics.get(mac, set())
        for topic in old - set(configs):
            self._publish(topic, "")  # entity no longer exists (port count or control changed)
        for topic, config in configs.items():
            self._publish(topic, config)
        self.sent_topics[mac] = set(configs)
        self.port_counts[mac] = self._port_count(mac)

    # -- commands ------------------------------------------------------------

    async def handle_command(self, topic: str, payload: str) -> str:
        """Run a command from MQTT. Returns a short result (also logged)."""
        parts = topic.split("/")
        prefix_len = len(self.prefix.split("/"))
        rest = parts[prefix_len:]
        if not self.allow_control:
            return "ignored: control is off"
        macs = {mac_id(m): m for m in self.manager.store.devices}
        if not rest or rest[0] not in macs:
            return "ignored: unknown switch"
        mac = macs[rest[0]]
        client = self.manager.client(mac)
        try:
            if rest[1:] == ["reboot", "press"] and payload == "PRESS":
                await client.reboot()
                result, route = "reboot", "MQTT reboot"
            elif (
                len(rest) == 4
                and rest[1] == "port"
                and rest[3] == "set"
                and payload in ("ON", "OFF")
            ):
                port = int(rest[2])
                admin = await client.read_port_admin() or {}
                if port not in admin:
                    return "ignored: this switch has no port settings"
                flow = admin[port]["flow_control"]
                await client.set_port_admin(
                    {port: {"speed_code": 0 if payload == "ON" else 1, "flow_control": flow}}
                )
                result, route = f"port {port} {payload}", f"MQTT port/{port} {payload}"
                await self.manager.monitor.sample(mac)
            else:
                return "ignored: unknown command"
        except Exception as exc:  # report, never crash the bridge
            log.warning("MQTT command %s for %s failed: %s", topic, mac, exc)
            self.status["error"] = f"{topic}: {exc}"
            return f"failed: {exc}"
        if self.audit:
            self.audit("mqtt", "MQTT", route, mac, 200)
        log.info("MQTT command for %s: %s", mac, result)
        return result
