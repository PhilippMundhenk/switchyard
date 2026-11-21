<img src="docs/assets/icon.svg" alt="" width="96" align="right">

# Switchyard

A web interface for managing NETGEAR ProSAFE Plus ("Smart Managed Plus", "Easy Smart") switches
such as the GS108E. It replaces the Windows-only *ProSAFE Plus Utility* with something you can
run in a container and open from any browser: find your switches, add them to a list, and change
VLANs, QoS, mirroring and more.

> Switchyard is an independent open-source project. It is not affiliated with, endorsed or
> supported by NETGEAR. Product names are used only to say what it works with.

![Switches](docs/screenshots/switches.png)

**Status:** version 0.1. Confirmed on a real GS108E by the maintainer: discovery, reading and
changing settings work. Firmware upgrade, link aggregation and port speed settings have only been
tested against the built-in simulator so far. Read [docs/HARDWARE.md](docs/HARDWARE.md) before you
use those, and please report what you find.

## Which switches

Switches that the old utility managed, over its UDP protocol (NSDP). Several models have no web
interface at all, so this is the only way to configure them.

| Switch | Status |
| --- | --- |
| GS108E v1 (firmware 1.00.09) | works: discovery, status, VLANs, QoS, name and IP settings |
| GS105E (first generation), other GS108E revisions | expected to work, reports welcome |
| GS105E v2, GS110EMX, JGS516PE, JGS524PE, XS-series | may work, reports welcome |
| Newer firmware with the Plus Utility turned off | needs a setting change, see [troubleshooting](docs/TROUBLESHOOTING.md) |

## Getting started

1. Run it on a machine in the **same network segment** as the switches.
   The container runs as user 10001: for a bind-mounted `./data` folder first run
   `mkdir -p data && sudo chown 10001:10001 data` (or set `user:` to your own ID, see
   [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#data-folder)). Discovery uses UDP
   broadcast, so the container needs host networking.

   ```yaml
   services:
     switchyard:
       image: ghcr.io/philippmundhenk/switchyard:latest
       network_mode: host
       restart: unless-stopped
       volumes:
         - ./data:/data
       environment:
         SWITCHYARD_AUTH_PASSWORD: "choose-a-password"
   ```

   Or without Docker: `pip install .` then `switchyard serve`.

2. Open `http://<host>:8080` and sign in. Without `SWITCHYARD_AUTH_PASSWORD` a password is
   generated on the first start and printed once, in a framed block on the console
   (`docker compose logs switchyard`). Lost it? Run `switchyard reset-password`
   (`docker compose run --rm switchyard reset-password`) and restart.
3. Press *Search network* and add your switches. A new switch has the password `password`.
   Switch on another subnet, or the search finds nothing? Use *Add by IP address*: Switchyard then
   talks to that address directly, also through a router.
4. Reads need no password. When you change something, Switchyard asks for the switch password.

Want to look around first? `docker run --rm -p 8080:8080 -e SWITCHYARD_DEMO_ENABLED=true -e SWITCHYARD_AUTH_PASSWORD=demo ghcr.io/philippmundhenk/switchyard` starts four simulated switches.

## What it does

| Area | Features |
| --- | --- |
| Discovery | Broadcast search on all interfaces, add by IP for other subnets, several switches in one list |
| Status | Port panel with link speed, live statistics and rates, CRC errors |
| Network | Name, DHCP or static IP |
| VLAN | Port-based and 802.1Q, matrix editor, PVID, safe "apply the whole table" |
| QoS | 802.1p or port based priority, ingress/egress rate limits, broadcast storm control |
| Ports | Speed and flow control per port, link aggregation (static LAG) |
| Other | Port mirroring, IGMP snooping, loop detection, cable test |
| Maintenance | Switch password, reboot, factory reset, JSON backup and restore, automatic stored backups, compare a backup with the live switch, port and VLAN names, **firmware upgrade** (checked image, typed confirmation, progress) |
| Monitoring | Background port polling: dashboard summary, traffic history chart with CSV download, link and online/offline events, live port dots on every card, Prometheus `/metrics`, optional webhook |
| Home Assistant | Every switch as a device over MQTT discovery: online, firmware, link, speed, traffic and CRC errors per port; optional restart button and port on/off |
| Access | Admin password, optional read-only viewer password, audit log of every change |
| Debugging | Live protocol log with passwords blanked |

Not included: LACP (these switches only do static aggregation), LED and power saving options. Link aggregation, port speed and firmware upgrade are new and **not yet verified on real hardware**; the firmware upgrade is the riskiest function, read [docs/HARDWARE.md](docs/HARDWARE.md) first.

## Screenshots

| | |
| --- | --- |
| ![VLAN](docs/screenshots/vlan.png) | ![QoS](docs/screenshots/qos.png) |
| ![Overview](docs/screenshots/overview.png) | ![Dark](docs/screenshots/overview-dark.png) |

More in [docs/screenshots.md](docs/screenshots.md).

## Options

Most settings have a sensible default. The common ones:

| Variable | Meaning |
| --- | --- |
| `SWITCHYARD_AUTH_PASSWORD` | Password of the web interface |
| `SWITCHYARD_AUTH_DISABLED` | `true` turns the login off (only behind something that authenticates, or on a trusted network; then only IP addresses, `localhost` and `SWITCHYARD_SERVER_ALLOWED_HOSTS` are accepted as host names) |
| `SWITCHYARD_SERVER_ALLOWED_HOSTS` | Host names allowed in the browser address, e.g. `switchyard.lan` |
| `SWITCHYARD_SERVER_PORT` | Web port, default 8080 |
| `SWITCHYARD_NSDP_INTERFACES` | Search only these interfaces, e.g. `eth0` |
| `SWITCHYARD_NSDP_TARGETS` | Directed broadcast or switch addresses for routed networks |
| `SWITCHYARD_MONITOR_INTERVAL_SECONDS` | How often ports are read for history and events, default 30, 0 = off |
| `SWITCHYARD_AUTH_VIEWER_PASSWORD` | Second, read-only login |
| `SWITCHYARD_SERVER_FORWARDED_ALLOW_IPS` | Reverse proxies whose forwarded headers are trusted |
| `SWITCHYARD_BACKUP_INTERVAL_HOURS` | Automatic backups (only when the configuration changed), default 24, 0 = off |
| `SWITCHYARD_METRICS_TOKEN` | Bearer token for `/metrics` |
| `SWITCHYARD_MQTT_URL` | MQTT broker for Home Assistant, see [docs/HOME_ASSISTANT.md](docs/HOME_ASSISTANT.md) |
| `SWITCHYARD_NOTIFY_WEBHOOK_URL` | POST link and online/offline events here |
| `SWITCHYARD_DEMO_ENABLED` | Use simulated switches |

All keys: [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Documentation

- [docs/HARDWARE.md](docs/HARDWARE.md) first run on a real switch, recovery, compatibility list
- [docs/HOME_ASSISTANT.md](docs/HOME_ASSISTANT.md) entities, setup, optional control
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) Docker, systemd, reverse proxy, routed networks
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) every setting; [docs/API.md](docs/API.md) every HTTP route
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) nothing found, password refused, locked out
- [docs/security.md](docs/security.md) what is protected and what is not
- [docs/PROTOCOL.md](docs/PROTOCOL.md) the NSDP protocol as Switchyard speaks it
- [docs/DESIGN.md](docs/DESIGN.md) goals, architecture, decisions; [docs/adr](docs/adr/README.md)
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) build, test, contribute; [CONTRIBUTING.md](CONTRIBUTING.md)

MIT licensed. Thanks to the projects that documented the protocol first, see
[docs/DESIGN.md](docs/DESIGN.md#acknowledgements).
