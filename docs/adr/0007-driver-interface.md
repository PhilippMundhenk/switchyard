# 0007 One driver interface for several switch protocols

## Context

TP-Link Easy Smart switches are configured by a Windows tool with its own UDP protocol, much
like NETGEAR's Plus switches. Manager, monitor, backups, MQTT and the API called the NSDP
client directly, so a second protocol would have meant branches everywhere.

## Decision

`switchyard/drivers/` defines a `SwitchDriver` (one switch) and a `Backend` (one protocol:
discovery, probing, making drivers). Drivers return the same dictionaries for every vendor; a
setting the switch does not have is `None` in its state, and a write it cannot do raises
`Unsupported` (HTTP 409 `unsupported`). Every stored switch remembers its `protocol`; entries
from before this change are NSDP. The search asks all backends at once; one failing backend
does not stop the others.

## Consequences

A new protocol is a backend, a driver and a simulator; the UI already hides what a switch's
state reports as `None`. Protocol-specific details (diagnostic tags, user names, whether a
switch can be reached by unicast) are properties of the driver or backend.
