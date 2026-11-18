# 0001 Python and FastAPI, no-build vanilla JS UI

## Context

The server needs raw UDP sockets and a small JSON API; the UI is a handful of forms and tables. Contributors should not need a JS toolchain.

## Decision

Python 3.11+, FastAPI on uvicorn, asyncio UDP. The UI is plain ES modules served as static files, tested in a real browser.

## Consequences

No build step, tiny image, strict CSP is easy. No framework helpers: DOM code is hand-written, kept small by helpers in util.js and ui.js.
