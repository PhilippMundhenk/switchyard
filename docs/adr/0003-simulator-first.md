# 0003 Simulator-first testing

## Context

No hardware was available while building.

## Decision

Write a software switch that follows the documented behaviour, run all tests over real UDP sockets against it, use it for demo mode and screenshots.

## Consequences

Fast, deterministic tests. The simulator encodes our assumptions; real hardware may differ. Hardware reports feed back into it.
