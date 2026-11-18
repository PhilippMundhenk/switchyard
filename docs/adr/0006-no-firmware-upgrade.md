# 0006 No firmware upgrade in the first release

## Context

A firmware upgrade is a TFTP push after an NSDP write. A failed or wrong upgrade can brick a switch, and it cannot be tested without hardware.

## Decision

Leave it out until it has been verified on real devices.

## Consequences

Users keep the vendor's tools or the switch web interface for firmware.
