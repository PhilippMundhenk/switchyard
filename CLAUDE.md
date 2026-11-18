# Switchyard: notes for Claude Code

- Commands: `make lint`, `make test` (95 % coverage gate), `make e2e`, `make screenshots`, `make demo`.
  Run `make lint` last before every commit (repo-wide ruff check + format check).
- Everything talks to switches through `src/switchyard/nsdp/`. Tests run the real UDP code against
  `src/switchyard/sim.py`. When you add a setting: tag + codec in `nsdp/tags.py`, client method,
  simulator behaviour, API route, UI, docs/PROTOCOL.md, tests.
- The UI under `src/switchyard/web/static/` has no build step. CSP is strict: no inline script or
  style attributes in HTML strings, set styles from JS. Never put untrusted text into `innerHTML`.
- Localhost HTTP from a shell in the sandbox needs `NO_PROXY=127.0.0.1,localhost`.
- No private data anywhere: simulator MACs and IPs only. Not verified on real hardware yet.
