# 0006 Firmware upgrade with guardrails

## Context

A firmware upgrade is a TFTP push after an NSDP write. A wrong or interrupted upgrade can brick a
switch, and the author had no hardware while writing it. Users still want it: the vendor's
utility is Windows only and many models have no web interface.

## Decision

Implement it, but make the dangerous parts hard to do by accident: parse and checksum the image,
compare its board name with the switch model, require the switch password, require the switch
MAC typed as confirmation, show a plain warning, refuse a second upgrade, wait for the switch to
come back and report the result, and keep a simulator (including a TFTP server) to test every step.
"Force" for a mismatching board is a separate, labelled checkbox.

## Consequences

The feature exists but is **unverified on hardware**; documented as such. The simulator encodes
our guesses about the container format and flow. Hardware reports decide whether it stays enabled
by default.
