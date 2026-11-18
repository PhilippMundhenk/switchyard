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
- Icon, favicon, web manifest and social preview image.
- Switch simulator (including TFTP) and demo mode.
