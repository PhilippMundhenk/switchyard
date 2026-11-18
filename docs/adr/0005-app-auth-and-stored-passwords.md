# 0005 Web password and stored switch passwords

## Context

Anyone who reaches the web port could reconfigure switches. Entering the switch password for every change is tedious.

## Decision

One web password (configured or generated once), scrypt hash, in-memory sessions. Switch passwords are stored only on request, in a mode 0600 file.

## Consequences

Simple and honest. Stored passwords are plain text in the data folder; documented in security.md.
