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
- Switch protocols sit behind one driver interface; NETGEAR NSDP is the first driver. Stored switches remember their protocol, the search asks every protocol, and a setting a switch does not have is answered with a clear 'not supported' (#62).

### Documentation
- A bridged Docker network with a published port works for switches added by IP address; host networking is only needed for the search (#61).

### Fixed
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
