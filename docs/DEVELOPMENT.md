# Development

```sh
make install      # .venv with dev dependencies
make test         # 95 % coverage gate
make e2e          # browser tests
make demo         # UI with simulated switches
```

## Test layers

| Layer | Where | Notes |
| --- | --- | --- |
| Codec and tags | `tests/test_protocol.py` | byte-exact packets, known-answer password vectors |
| Client | `tests/test_client.py` | real UDP sockets on loopback against `sim.py`, all password modes, all unknown-tag behaviours |
| API | `tests/test_api.py` | real ASGI app, every route, security checks |
| Browser | `tests/e2e/` | real server in demo mode, real Chromium, marker `e2e` |
| Screenshots | `tools/screenshots.py` | documentation images, run by CI |
| Container | `tools/smoke.sh` | builds the image, drives the API |

The simulator (`src/switchyard/sim.py`) models authentication, unsupported settings, the VLAN
rules and reboots. Extend it together with the code.

## Add a setting

1. Tag and codec in `nsdp/tags.py`; document in [PROTOCOL.md](PROTOCOL.md).
2. Method in `nsdp/client.py`; reading in `read_state`.
3. Behaviour in `sim.py`.
4. API route in `web/app.py`; UI in `web/static/views/tabs.js`.
5. Tests at every layer.

## Browser tests in a restricted sandbox

Set `SWITCHYARD_CHROMIUM` to a Chromium binary; both the e2e tests and the screenshot tool use it.

## Release

Update `CHANGELOG.md`, bump `__version__`, tag `vX.Y.Z`. The release workflow runs the gates,
pushes the image and attaches the wheel.
