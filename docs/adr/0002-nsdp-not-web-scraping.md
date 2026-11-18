# 0002 NSDP over UDP instead of scraping the switch web UI

## Context

Newer models have a web interface that other tools scrape. The older and most common ones (GS105E v1, GS108E v1/v2) have none. The vendor utility uses NSDP.

## Decision

Speak NSDP.

## Consequences

Works on the models nobody else serves. Depends on a reverse-engineered protocol and on firmware that still allows it.
