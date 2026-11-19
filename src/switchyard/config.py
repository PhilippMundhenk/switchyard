"""Configuration: a commented TOML file, every key overridable by environment."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ENV_PREFIX = "SWITCHYARD_"

# (section, key, default, comment)
SCHEMA: list[tuple[str, str, object, str]] = [
    ("server", "host", "0.0.0.0", "Address the web interface listens on."),
    ("server", "port", 8080, "Port of the web interface."),
    (
        "server",
        "allowed_hosts",
        [],
        'Host names the web interface may be reached by, e.g. ["switchyard.lan"]. IP addresses\n'
        "# and localhost always work. Empty: any name while a password is required; only IP\n"
        "# addresses and localhost when auth.disabled is true (protection against DNS rebinding).",
    ),
    (
        "server",
        "forwarded_allow_ips",
        "127.0.0.1",
        "Addresses of reverse proxies whose X-Forwarded-* headers are trusted (comma\n"
        "# separated, or * for all). Needed for the secure cookie flag behind a TLS proxy.",
    ),
    (
        "auth",
        "password",
        "",
        "Password for the web interface. Empty: a random one is generated on\n"
        "# first start, printed once to the log and stored hashed in the data folder.",
    ),
    (
        "auth",
        "disabled",
        False,
        "true = no login at all. Only for trusted networks or behind a proxy\n"
        "# that authenticates.",
    ),
    (
        "auth",
        "viewer_password",
        "",
        "Optional second password for a read-only login: it can see everything but change nothing.",
    ),
    ("auth", "session_hours", 24, "How long a login stays valid."),
    (
        "nsdp",
        "interfaces",
        [],
        'Network interfaces to search for switches, e.g. ["eth0"]. Empty: all.',
    ),
    (
        "nsdp",
        "targets",
        [],
        "Send to these addresses instead of broadcasting, e.g.\n"
        '# ["192.168.5.255", "10.0.0.7:63322"]. For routed networks.',
    ),
    ("nsdp", "client_port", 63321, "UDP port this side uses (the protocol expects 63321)."),
    ("nsdp", "device_port", 63322, "UDP port the switches listen on."),
    ("nsdp", "tftp_port", 69, "UDP port of the switch's TFTP server, used for firmware upgrades."),
    ("nsdp", "discovery_seconds", 2.0, "How long a search waits for answers."),
    (
        "nsdp",
        "poll_seconds",
        60,
        "Search in the background this often to keep the online state fresh.\n# 0 = never.",
    ),
    (
        "backup",
        "interval_hours",
        24,
        "Store a configuration backup of every managed switch this often (only when it changed).\n"
        "# 0 = off.",
    ),
    ("backup", "keep", 14, "How many stored backups to keep per switch."),
    (
        "monitor",
        "interval_seconds",
        30,
        "Read the ports of every managed switch this often to keep history, link events and\n"
        "# metrics. 0 = off.",
    ),
    ("monitor", "history_minutes", 60, "How much traffic history to keep in memory."),
    (
        "metrics",
        "token",
        "",
        "Bearer token for GET /metrics (Prometheus). Empty: /metrics needs a web session.",
    ),
    (
        "notify",
        "webhook_url",
        "",
        "POST a JSON message to this URL for link and online/offline events. Empty: off.",
    ),
    (
        "demo",
        "enabled",
        False,
        "true = start four simulated switches instead of using the network.",
    ),
    ("log", "level", "info", "debug, info, warning or error."),
]


@dataclass
class Config:
    data_dir: Path
    values: dict[str, object] = field(default_factory=dict)

    def __getitem__(self, key: str):
        return self.values[key]

    @property
    def path(self) -> Path:
        return self.data_dir / "switchyard.toml"


def _format(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_format(v) for v in value) + "]"
    return str(value)


def default_file() -> str:
    lines = [
        "# Switchyard configuration.",
        "# Every key can also be set by an environment variable:",
        "# SWITCHYARD_<SECTION>_<KEY>, for example SWITCHYARD_SERVER_PORT=9000.",
        "# Lists take comma separated values. Restart after a change.",
    ]
    section = None
    for sec, key, default, comment in SCHEMA:
        if sec != section:
            lines += ["", f"[{sec}]"]
            section = sec
        lines += [f"# {comment}", f"{key} = {_format(default)}"]
    return "\n".join(lines) + "\n"


def _from_env(raw: str, default: object) -> object:
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return raw


def load(data_dir: str | Path | None = None, env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    data = Path(data_dir or env.get(ENV_PREFIX + "DATA_DIR") or "data")
    data.mkdir(parents=True, exist_ok=True)
    config = Config(data)
    if not config.path.exists():
        config.path.write_text(default_file())
    with config.path.open("rb") as handle:
        file_values = tomllib.load(handle)
    for section, key, default, _ in SCHEMA:
        value = file_values.get(section, {}).get(key, default)
        if isinstance(default, float) and isinstance(value, int):
            value = float(value)
        if type(value) is not type(default):
            raise ValueError(
                f"{config.path}: [{section}] {key} must be of type {type(default).__name__}"
            )
        raw = env.get(f"{ENV_PREFIX}{section}_{key}".upper())
        if raw is not None:
            value = _from_env(raw, default)
        config.values[f"{section}.{key}"] = value
    return config
