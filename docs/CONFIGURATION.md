# Configuration

Switchyard reads `switchyard.toml` from its data folder. The file is written with every key
commented on the first start. Every key can also be set by an environment variable. Variables
win over the file. Lists take comma separated values. Restart after a change.

The data folder is `/data` in the container, `./data` otherwise, or `--data DIR`, or
`SWITCHYARD_DATA_DIR`.

| Key | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `server.host` | `SWITCHYARD_SERVER_HOST` | `"0.0.0.0"` | Address the web interface listens on. |
| `server.port` | `SWITCHYARD_SERVER_PORT` | `8080` | Port of the web interface. |
| `auth.password` | `SWITCHYARD_AUTH_PASSWORD` | `""` | Password for the web interface. Empty: a random one is generated on first start, printed once to the log and stored hashed in the data folder. |
| `auth.disabled` | `SWITCHYARD_AUTH_DISABLED` | `false` | true = no login at all. Only for trusted networks or behind a proxy that authenticates. |
| `auth.session_hours` | `SWITCHYARD_AUTH_SESSION_HOURS` | `24` | How long a login stays valid. |
| `nsdp.interfaces` | `SWITCHYARD_NSDP_INTERFACES` | `[]` | Network interfaces to search for switches, e.g. ["eth0"]. Empty: all. |
| `nsdp.targets` | `SWITCHYARD_NSDP_TARGETS` | `[]` | Send to these addresses instead of broadcasting, e.g. ["192.168.5.255", "10.0.0.7:63322"]. For routed networks. |
| `nsdp.client_port` | `SWITCHYARD_NSDP_CLIENT_PORT` | `63321` | UDP port this side uses (the protocol expects 63321). |
| `nsdp.device_port` | `SWITCHYARD_NSDP_DEVICE_PORT` | `63322` | UDP port the switches listen on. |
| `nsdp.tftp_port` | `SWITCHYARD_NSDP_TFTP_PORT` | `69` | UDP port of the switch's TFTP server, used for firmware upgrades. |
| `nsdp.discovery_seconds` | `SWITCHYARD_NSDP_DISCOVERY_SECONDS` | `2.0` | How long a search waits for answers. |
| `nsdp.poll_seconds` | `SWITCHYARD_NSDP_POLL_SECONDS` | `60` | Search in the background this often to keep the online state fresh. 0 = never. |
| `monitor.interval_seconds` | `SWITCHYARD_MONITOR_INTERVAL_SECONDS` | `30` | Read the ports of every managed switch this often to keep history, link events and metrics. 0 = off. |
| `monitor.history_minutes` | `SWITCHYARD_MONITOR_HISTORY_MINUTES` | `60` | How much traffic history to keep in memory. |
| `metrics.token` | `SWITCHYARD_METRICS_TOKEN` | `""` | Bearer token for GET /metrics (Prometheus). Empty: /metrics needs a web session. |
| `notify.webhook_url` | `SWITCHYARD_NOTIFY_WEBHOOK_URL` | `""` | POST a JSON message to this URL for link and online/offline events. Empty: off. |
| `demo.enabled` | `SWITCHYARD_DEMO_ENABLED` | `false` | true = start four simulated switches instead of using the network. |
| `log.level` | `SWITCHYARD_LOG_LEVEL` | `"info"` | debug, info, warning or error. |

## Data folder

| File | Content |
| --- | --- |
| `switchyard.toml` | This configuration. |
| `devices.json` | The switches you manage: label, notes, fixed address, last seen info, and the switch password if you ticked "remember". Mode 0600. |
| `secrets.json` | Hash of the generated web password (scrypt). Mode 0600. Only used when `auth.password` is empty. |

Back up the folder to keep your list of switches. Treat `devices.json` as a secret.
