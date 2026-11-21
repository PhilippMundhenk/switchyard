"""Check Switchyard's MQTT discovery messages with Home Assistant's own schemas.

Run with a Python that has Home Assistant installed (it is not a Switchyard dependency):

    python tools/validate_ha_discovery.py

Builds the discovery messages for a sample 8-port switch (with and without control), validates
each one with the DISCOVERY_SCHEMA of the matching Home Assistant MQTT platform, and renders
every value template against a sample state message.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from homeassistant.const import __version__ as ha_version  # noqa: E402
from jinja2 import Environment  # noqa: E402

from switchyard.mqtt import MqttBridge  # noqa: E402

MAC = "00:26:f2:aa:10:01"


class FakeMonitor:
    def __init__(self):
        self.listeners = []
        self.state = {MAC: {"online": True, "admin": {p: {"speed_code": 0} for p in range(1, 9)}}}

    def snapshot(self, mac):
        return [
            {"port": p, "up": p % 2 == 1, "speed_mbps": 1000 if p % 2 else 0} for p in range(1, 9)
        ]

    def rates(self, mac):
        return {1: (1200.0, 800.0)}

    def counter(self, mac, port, name):
        return 0


def bridge(control: bool) -> MqttBridge:
    config = {
        "mqtt.url": "mqtt://broker:1883",
        "mqtt.topic_prefix": "switchyard",
        "mqtt.discovery_prefix": "homeassistant",
        "mqtt.allow_control": control,
        "mqtt.base_url": "http://192.168.0.5:8080",
        "mqtt.username": "",
        "mqtt.password": "",
    }
    store = SimpleNamespace(
        devices={MAC: {}},
        get=lambda mac: {
            "label": "Office",
            "port_labels": {"1": "NAS"},
            "info": {"model": "GS108E", "firmware": "1.00.12", "ports": 8, "ip": "192.168.0.239"},
        },
    )
    manager = SimpleNamespace(
        store=store, monitor=FakeMonitor(), config=SimpleNamespace(data_dir=ROOT)
    )
    return MqttBridge(manager, config)


async def validate() -> int:
    import tempfile

    from homeassistant.core import HomeAssistant

    hass = HomeAssistant(tempfile.mkdtemp())  # templates are validated against a running instance
    errors = 0
    checked = 0
    env = Environment()
    for control in (False, True):
        b = bridge(control)
        state = b.state(MAC)
        sample = json.loads(json.dumps(state))
        for topic, payload in b.discovery_configs(MAC).items():
            component = topic.split("/")[1]
            platform = importlib.import_module(f"homeassistant.components.mqtt.{component}")
            schema = platform.DISCOVERY_SCHEMA
            payload = json.loads(json.dumps(payload))  # exactly what goes over the wire
            payload["platform"] = "mqtt"
            try:
                schema(payload)
            except Exception as exc:
                errors += 1
                print(f"INVALID {topic}: {exc}")
                continue
            template = payload.get("value_template")
            if template:
                try:
                    rendered = env.from_string(template).render(value_json=sample)
                except Exception as exc:
                    errors += 1
                    print(f"TEMPLATE {topic}: {exc}")
                    continue
                if rendered in ("", "None"):
                    errors += 1
                    print(f"EMPTY {topic}: {template!r} -> {rendered!r}")
            checked += 1
    print(f"Home Assistant {ha_version}: {checked} discovery messages valid, {errors} problem(s)")
    await hass.async_stop(force=True)
    return 1 if errors else 0


def main() -> int:
    import asyncio

    return asyncio.run(validate())


if __name__ == "__main__":
    sys.exit(main())
