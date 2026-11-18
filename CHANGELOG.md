# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- First version: discovery, multi-switch list, port status and statistics, name and IP settings,
  port-based and 802.1Q VLANs, QoS and rate limits, port mirroring, IGMP snooping, loop detection,
  cable test, switch password change, reboot, factory reset, JSON backup and restore.
- Web interface without a build step, light and dark theme, phone layout.
- Live protocol log with credentials blanked.
- Link aggregation (static LAGs), per-port speed and flow control, firmware upgrade over TFTP with image checks, confirmation, progress and restart verification.
- Background monitor: traffic history chart, link and online/offline events (Events page), live port dots on the switch cards, Prometheus `/metrics`, optional webhook (#13, #14, #15, #16).
- Automatic stored backups (only on change), compare a backup with the live switch before restoring, restore from a stored backup, port and VLAN names (#17, #18, #19).
- Accessibility check over every page in the browser tests; no API docs page from a CDN; dashboard summary on the switch list; CSV export of traffic history (#22, #23, #24, #25).
- Read-only viewer login, audit log of changes, trusted proxy setting with proxy examples (#26, #27, #28).
- Fuzz tests against garbled firmware replies; generated HTTP API doc; docs-vs-code consistency check in CI (#29, #30).
- Icon, favicon, web manifest and social preview image.
- Switch simulator (including TFTP) and demo mode.
