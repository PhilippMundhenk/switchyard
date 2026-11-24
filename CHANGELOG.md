# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Security
- Login lockout per client address instead of global, so nobody can lock out the admin (#43).
- Host name check against DNS rebinding when login is disabled; new `server.allowed_hosts` (#44).
- Request size limits: 1 MB, firmware 16 MB, also for chunked uploads (#45).
- Add by IP accepts only the probed address's answer (#46).
- Write requests also check `Sec-Fetch-Site` and need a JSON or binary content type (#47).
- The generated password is printed once on stderr regardless of the log level;
  new `switchyard reset-password` (#48).

### Changed
- The protocol log shows how long each answer took, which makes slow switches and resends visible (#123).
- Dependencies: uvicorn 0.54, paho-mqtt 2.2, pytest-asyncio 1.5, ruff 0.17, amqtt 0.12; GitHub Actions updated (#131).
- A change shows its result as soon as the switch confirms it; the page then re-reads only the affected settings in the background (`GET /api/devices/{mac}/state?parts=...`) instead of the whole switch. Much faster on TP-Link switches (#81).
- The container image runs Python 3.15 (CI also tests 3.15); test tools and GitHub Actions updated (#77).
- Switch protocols sit behind one driver interface; NETGEAR NSDP is the first driver. Stored switches remember their protocol, the search asks every protocol, and a setting a switch does not have is answered with a clear 'not supported' (#62).

### Documentation
- Networking in DEPLOYMENT.md: host, bridged and macvlan compared, how to check a bridged setup, and each behind Traefik (host.docker.internal, health check, trusted proxy subnets, keeping the port away from the LAN); `server.forwarded_allow_ips` takes subnets (#69).
- A bridged Docker network with a published port works for switches added by IP address; host networking is only needed for the search (#61).

### Fixed
- TP-Link PoE: recovery changes are checked against the switch's answer; a change it ignores is retried without the statistics bytes and otherwise reported, never shown as saved (#128). The PoE page shows units and the ranges the switch accepts (#129).
- TP-Link: the port LED switch is on the Network tab; the cable test page says to run the test on the switch's own web interface (#121).
- TP-Link PoE: recovery and extend mode are written as the groups the switch expects (all PoE ports in one change); error 3 outside the system settings gets a neutral message (#120).
- The switch page shows only the sections the switch has (no PoE on NETGEAR, no LAG or port speed on a GS108E v1), and the tab row wraps instead of scrolling sideways (#122).
- TP-Link: storm control per port, with the rate and the frame types of each port, like the switch's own page (#111).
- TP-Link: port LEDs on and off (#113); the overview shows the hardware version (#112); the MTU VLAN uplink field is aligned (#110).
- TP-Link: a switch that drops its session key is reached again with a new key, instead of staying 'not reachable' until a restart (#95).
- TP-Link: the VLAN page no longer offers 'Disabled' (these switches cannot switch VLANs off; the default is port-based with every port in group 1) and says so (#96).
- TP-Link VLAN page: port-based groups can be added (a port is in one group; group 1 holds the rest), MTU VLAN mode with its uplink port, hints on PVIDs (#87).
- TP-Link mirroring takes one source port with its direction; a refused change explains itself (#86).
- TP-Link storm control: choose unknown unicast, multicast and broadcast frames; the API accepts TP-Link's own rate choices (#88).
- Firmware and cable test tabs say 'not supported' where the switch's protocol cannot do them (#89).
- TP-Link: a change the switch answers slowly is no longer reported as 'does not answer'; the VLAN page offers only the switch's modes (port-based, 802.1Q); mirroring can copy received and sent traffic per port separately (#83).
- TP-Link: changes on newer firmware that wants a token besides the session key (TL-SG108PE 5.0, 1.0.0 Build 20230218) no longer go unanswered; QoS offers the DSCP/802.1p mode; the settings scan shows the token (#79).
- MQTT: when the broker cannot be reached, *Settings* and the log say why (unknown host name, connection refused, no answer, TLS, login refused) instead of only 'connecting' (#60).
- After an update, browsers no longer keep running the old interface scripts from their cache. The settings scan labels answers without a value as 'empty (not supported)' (#10).
- 'Copy for bug report' works on plain HTTP (no clipboard API there): copy fallback, else a dialog with the text and a download; the protocol log page is titled 'Protocol log' (#56).
- Requests are no longer broadcast on Docker, Podman, libvirt and Kubernetes bridges, and go only out of the interface a switch answered on (#57).
- Phone layout: statistics as one row per port; QoS and speed/flow control with port chips and one editor for the selected ports (#58).
- Background refreshes of a page are all stopped when leaving it (only the last one was).
- MQTT: the 'offline' status could be lost on shutdown, so Home Assistant kept showing Switchyard as online.
- Phone layout: no sideways scrolling; section drop-down instead of the tab strip, one block per port in tables, VLAN and LAG matrices with ports as rows (#55).
- GS108E v1 firmware that answers an empty reply when a read contains an unknown setting: identity, mirroring and IGMP were missing (#53).
- Stray 'null' text above the header outside demo mode (#54).
- An unwritable data folder (bind mount owned by another user) stops with a clear message
  instead of a traceback; the requirements are documented (#52).
- Add by IP through a router on hosts with several interfaces: unicast now leaves with the
  source address the routing table picks (#49).

### Added
- TP-Link PoE switches: a PoE tab with auto recovery per port (ping address, delays, threshold, break time) and extend mode; layouts from a tester's TL-SG108PE scans (#114).
- TP-Link Easy Smart switches (TL-SG105E, TL-SG108E, TL-SG108PE, TL-SG1016DE, ...): search, status, packet statistics, 802.1Q and port-based VLANs, QoS with rate limits and storm control, mirroring, IGMP snooping, loop prevention, LED, link aggregation, port speed and flow control, name and IP, password, reboot, factory reset, backup and restore. Both protocol generations (token, and the session key of firmware from 2023). A simulator with two demo switches. Tested against the simulator only so far; test plan in HARDWARE.md (#63 #64 #65 #67 #68).
- Home Assistant integration over MQTT discovery: online, firmware, ports up; per port link, speed, traffic, CRC errors; opt-in restart button and port on/off (#51).
- First version: discovery, multi-switch list, port status and statistics, name and IP settings,
  port-based and 802.1Q VLANs, QoS and rate limits, port mirroring, IGMP snooping, loop detection,
  cable test, switch password change, reboot, factory reset, JSON backup and restore.
- Web interface without a build step, light and dark theme, phone layout.
- Live protocol log with credentials blanked.
- Link aggregation (static LAGs), per-port speed and flow control, firmware upgrade over TFTP with image checks, confirmation, progress and restart verification.
- Background monitor: traffic history chart, link and online/offline events (Events page), live port dots on the switch cards, Prometheus `/metrics`, optional webhook (#14, #15, #16, #17).
- Automatic stored backups (only on change), compare a backup with the live switch before restoring, restore from a stored backup, port and VLAN names (#19, #21, #24).
- Accessibility check over every page in the browser tests; no API docs page from a CDN; dashboard summary on the switch list; CSV export of traffic history (#31, #32, #34, #36).
- Read-only viewer login, audit log of changes, trusted proxy setting with proxy examples (#37, #38, #39).
- Fuzz tests against garbled firmware replies; generated HTTP API doc; docs-vs-code consistency check in CI (#40, #41).
- Read-only diagnostics scan of the settings a switch answers, with a paste-friendly report (#42).
- Icon, favicon, web manifest and social preview image.
- Switch simulator (including TFTP) and demo mode.
