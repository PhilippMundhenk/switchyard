# 0004 Host networking and per-interface sockets

## Context

Discovery is a UDP broadcast on layer 2. A bridged container cannot send or receive it. A single socket sends limited broadcasts out of one interface only.

## Decision

Run with host networking; open one socket per interface bound to its address; allow explicit targets for routed networks.

## Consequences

Works on multi-homed hosts. Docker Desktop on macOS and Windows cannot do host networking: run natively there.

Bridged containers work only for switches added by IP that answer to the sender; macvlan gives
the container its own LAN address instead. Both, and the combination with a reverse proxy, are in
DEPLOYMENT.md (#69).
