"""Webhook delivery: one JSON POST to the URL the operator configured, nothing else."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

log = logging.getLogger(__name__)


def _send(url: str, payload: dict) -> None:
    if not url.startswith(("http://", "https://")):
        raise ValueError("The webhook URL must start with http:// or https://")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "switchyard"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5):
        pass


async def post_json(url: str, payload: dict) -> bool:
    try:
        await asyncio.to_thread(_send, url, payload)
        return True
    except Exception as exc:  # delivery is best effort; never break monitoring
        log.warning("webhook delivery failed: %s", exc)
        return False
