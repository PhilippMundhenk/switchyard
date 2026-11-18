# Contributing

Thanks for helping. The most valuable contribution is a **hardware report**: use the issue
template and say which model and firmware worked.

## Setup

```sh
make install     # virtualenv with dev tools
make test        # backend tests, 95 % coverage gate
make e2e         # browser tests (needs Chromium: python -m playwright install chromium)
make lint
make demo        # run the web interface with simulated switches
```

## Rules

- Every change comes with tests: working path, refusing path, security edge.
- Keep the UI dependency-free: plain ES modules, no build step, no inline scripts or styles
  (the CSP forbids them), no HTML built from untrusted strings.
- No private data in code, tests, issues or screenshots: use the simulator's fake addresses.
- Protocol changes: update [docs/PROTOCOL.md](docs/PROTOCOL.md) and the simulator together.
- Commit subjects mention the issue number, for example `Add LAG support (#12)`.

CI runs lint, tests on Python 3.11-3.13, the browser tests, the screenshot job and a container
smoke test. Pull requests from forks may need a maintainer to start the workflows.
