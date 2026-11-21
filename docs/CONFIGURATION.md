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
| `server.allowed_hosts` | `SWITCHYARD_SERVER_ALLOWED_HOSTS` | `[]` | Host names the web interface may be reached by, e.g. ["switchyard.lan"]. IP addresses and localhost always work. Empty: any name while a password is required; only IP addresses and localhost when auth.disabled is true (protection against DNS rebinding). |
| `server.forwarded_allow_ips` | `SWITCHYARD_SERVER_FORWARDED_ALLOW_IPS` | `"127.0.0.1"` | Addresses of reverse proxies whose X-Forwarded-* headers are trusted (comma separated, or * for all). Needed for the secure cookie flag behind a TLS proxy. |
| `auth.password` | `SWITCHYARD_AUTH_PASSWORD` | `""` | Password for the web interface. Empty: a random one is generated on first start, printed once to the log and stored hashed in the data folder. |
| `auth.disabled` | `SWITCHYARD_AUTH_DISABLED` | `false` | true = no login at all. Only for trusted networks or behind a proxy that authenticates. |
| `auth.viewer_password` | `SWITCHYARD_AUTH_VIEWER_PASSWORD` | `""` | Optional second password for a read-only login: it can see everything but change nothing. |
| `auth.session_hours` | `SWITCHYARD_AUTH_SESSION_HOURS` | `24` | How long a login stays valid. |
| `nsdp.interfaces` | `SWITCHYARD_NSDP_INTERFACES` | `[]` | Network interfaces to search for switches, e.g. ["eth0"]. Empty: all except the bridges of Docker, Podman, libvirt and Kubernetes (docker0, br-..., veth..., virbr...). |
| `nsdp.targets` | `SWITCHYARD_NSDP_TARGETS` | `[]` | Send to these addresses instead of broadcasting, e.g. ["192.168.5.255", "10.0.0.7:63322"]. For routed networks. |
| `nsdp.client_port` | `SWITCHYARD_NSDP_CLIENT_PORT` | `63321` | UDP port this side uses (the protocol expects 63321). |
| `nsdp.device_port` | `SWITCHYARD_NSDP_DEVICE_PORT` | `63322` | UDP port the switches listen on. |
| `nsdp.tftp_port` | `SWITCHYARD_NSDP_TFTP_PORT` | `69` | UDP port of the switch's TFTP server, used for firmware upgrades. |
| `nsdp.discovery_seconds` | `SWITCHYARD_NSDP_DISCOVERY_SECONDS` | `2.0` | How long a search waits for answers. |
| `nsdp.poll_seconds` | `SWITCHYARD_NSDP_POLL_SECONDS` | `60` | Search in the background this often to keep the online state fresh. 0 = never. |
| `easysmart.enabled` | `SWITCHYARD_EASYSMART_ENABLED` | `true` | Also search for and manage TP-Link Easy Smart switches (TL-SG105E, TL-SG108E, TL-SG108PE, TL-SG1016DE, ...). They answer by broadcast: same network segment only. |
| `easysmart.client_port` | `SWITCHYARD_EASYSMART_CLIENT_PORT` | `29809` | UDP port this side uses (the switches answer to 29809). |
| `easysmart.device_port` | `SWITCHYARD_EASYSMART_DEVICE_PORT` | `29808` | UDP port the switches listen on. |
| `easysmart.targets` | `SWITCHYARD_EASYSMART_TARGETS` | `[]` | Send to these addresses instead of broadcasting, e.g. ["127.0.0.1:29808"] (tests). |
| `backup.interval_hours` | `SWITCHYARD_BACKUP_INTERVAL_HOURS` | `24` | Store a configuration backup of every managed switch this often (only when it changed). 0 = off. |
| `backup.keep` | `SWITCHYARD_BACKUP_KEEP` | `14` | How many stored backups to keep per switch. |
| `monitor.interval_seconds` | `SWITCHYARD_MONITOR_INTERVAL_SECONDS` | `30` | Read the ports of every managed switch this often to keep history, link events and metrics. 0 = off. |
| `monitor.history_minutes` | `SWITCHYARD_MONITOR_HISTORY_MINUTES` | `60` | How much traffic history to keep in memory. |
| `metrics.token` | `SWITCHYARD_METRICS_TOKEN` | `""` | Bearer token for GET /metrics (Prometheus). Empty: /metrics needs a web session. |
| `notify.webhook_url` | `SWITCHYARD_NOTIFY_WEBHOOK_URL` | `""` | POST a JSON message to this URL for link and online/offline events. Empty: off. |
| `mqtt.url` | `SWITCHYARD_MQTT_URL` | `""` | MQTT broker for Home Assistant, e.g. mqtt://broker:1883 or mqtts://broker:8883. Empty: off. Needs the monitor (monitor.interval_seconds > 0) for live values. |
| `mqtt.username` | `SWITCHYARD_MQTT_USERNAME` | `""` | MQTT user name (or put it into the URL). |
| `mqtt.password` | `SWITCHYARD_MQTT_PASSWORD` | `""` | MQTT password (or put it into the URL). |
| `mqtt.topic_prefix` | `SWITCHYARD_MQTT_TOPIC_PREFIX` | `"switchyard"` | Topics are <prefix>/<mac>/state and so on. |
| `mqtt.discovery_prefix` | `SWITCHYARD_MQTT_DISCOVERY_PREFIX` | `"homeassistant"` | Home Assistant's MQTT discovery prefix. |
| `mqtt.allow_control` | `SWITCHYARD_MQTT_ALLOW_CONTROL` | `false` | true = Home Assistant gets a restart button per switch and an on/off switch per port. Anyone who can publish to the broker can then use them. Needs stored switch passwords. |
| `mqtt.base_url` | `SWITCHYARD_MQTT_BASE_URL` | `""` | Address of this web interface, linked from the Home Assistant device page. |
| `demo.enabled` | `SWITCHYARD_DEMO_ENABLED` | `false` | true = start simulated switches (four NETGEAR, two TP-Link) instead of the network. |
| `log.level` | `SWITCHYARD_LOG_LEVEL` | `"info"` | debug, info, warning or error. |

## Data folder

| File | Content |
| --- | --- |
| `switchyard.toml` | This configuration. |
| `devices.json` | The switches you manage: label, notes, fixed address, last seen info, and the switch password if you ticked "remember". Mode 0600. |
| `backups/<mac>/*.json` | Automatic configuration backups (mode 0600). No passwords inside. |
| `audit.log` | One JSON line per successful change: time, role, route, switch. Mode 0600. |
| `secrets.json` | Hash of the generated web password (scrypt). Mode 0600. Only used when `auth.password` is empty. |

Back up the folder to keep your list of switches. Treat `devices.json` as a secret.
