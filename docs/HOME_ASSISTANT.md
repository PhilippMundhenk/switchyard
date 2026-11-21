# Home Assistant

Switchyard publishes the state of your switches to an MQTT broker with Home Assistant discovery.
Each switch appears in Home Assistant as a device with its entities; nothing has to be installed
in Home Assistant besides its standard MQTT integration.

## Set up

1. Have an MQTT broker (for example the Mosquitto add-on) and the MQTT integration in Home
   Assistant.
2. Point Switchyard at the broker:

   ```yaml
   environment:
     SWITCHYARD_MQTT_URL: "mqtt://192.168.0.10:1883"
     SWITCHYARD_MQTT_USERNAME: "switchyard"
     SWITCHYARD_MQTT_PASSWORD: "secret"
     # optional: link from the Home Assistant device page back to Switchyard
     SWITCHYARD_MQTT_BASE_URL: "http://192.168.0.5:8080"
   ```

   Use `mqtts://broker:8883` for TLS (the broker certificate is verified).
3. Restart Switchyard. *Settings* shows "Home Assistant (MQTT): connected". The switches appear
   under *Settings*, *Devices & services*, *MQTT*.

The values come from the background monitor (`monitor.interval_seconds`, default 30 s). With the
monitor off, Home Assistant gets the switches but no live values.

## Entities

Per switch:

| Entity | Type | Notes |
| --- | --- | --- |
| Online | binary sensor (connectivity) | Stays available while the switch is down, so you can alert on it |
| Firmware | sensor (diagnostic) | |
| Ports up | sensor | Number of ports with a link |

Per port (names include your port names, for example "Port 1 (NAS) link"):

| Entity | Type | Unit |
| --- | --- | --- |
| Link | binary sensor (connectivity) | |
| Speed | sensor (data rate) | Mbit/s, 0 when down |
| Received, Sent | sensor (data rate) | bit/s, averaged over one monitor interval |
| CRC errors | sensor (diagnostic, total increasing) | |

Port entities become unavailable while the switch does not answer; all entities become
unavailable when Switchyard stops (MQTT last will).

What these switches cannot tell over their protocol, and so Home Assistant cannot show either:
temperature, fans, CPU or memory load, uptime, PoE power, the MAC address table, LLDP neighbours.

## Control (opt-in)

With `SWITCHYARD_MQTT_ALLOW_CONTROL: "true"` every switch also gets:

| Entity | What it does |
| --- | --- |
| Restart (button) | Reboots the switch |
| Port N enabled (switch) | Turns a port off (speed setting "disabled") or back on ("auto") |

Notes:

- Commands use the switch password stored in Switchyard ("remember" when you entered it).
- Turning a port on sets its speed to "auto"; a fixed speed setting is not kept.
- Anyone who can publish to the broker can use these commands. Give Switchyard its own broker
  user and limit publishing to `switchyard/#` with broker ACLs.
- Every command is written to the audit log (*Events* page) with the role `mqtt`.
- Switching off the port that connects Switchyard or Home Assistant to the network cuts you off.

## Topics

| Topic | Content |
| --- | --- |
| `switchyard/status` | `online` / `offline` (retained, last will) |
| `switchyard/<mac>/availability` | `online` / `offline` (retained) |
| `switchyard/<mac>/state` | JSON: `online`, `model`, `firmware`, `ip`, `ports_up`, `ports` with `link`, `speed`, `rx_bps`, `tx_bps`, `crc` (and `enabled` with control on) |
| `switchyard/<mac>/reboot/press` | command `PRESS` (control only) |
| `switchyard/<mac>/port/<n>/set` | command `ON` / `OFF` (control only) |
| `homeassistant/<component>/switchyard_<mac>/<object>/config` | discovery (retained) |

`<mac>` is the MAC address without colons. The prefixes are `mqtt.topic_prefix` and
`mqtt.discovery_prefix`. Removing a switch from Switchyard removes its entities.

## Other ways

- `GET /metrics` for Prometheus and Grafana.
- `notify.webhook_url` for a POST on link and online/offline events (Home Assistant webhook trigger).
