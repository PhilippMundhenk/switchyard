"""Login for the web interface: one password, sessions in memory."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE = "switchyard_session"
MAX_FAILURES = 5
LOCK_SECONDS = 60


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


class Auth:
    def __init__(self, data_dir: Path, password: str, disabled: bool, session_hours: float):
        self.disabled = disabled
        self.session_seconds = session_hours * 3600
        self.sessions: dict[str, float] = {}
        self.failures = 0
        self.locked_until = 0.0
        self.path = data_dir / "secrets.json"
        self.generated: str | None = None
        if disabled:
            log.warning("auth.disabled is set: anyone who reaches this port can configure switches")
            self.salt, self.hash = b"", ""
        elif password:
            self.salt = os.urandom(16)
            self.hash = _hash(password, self.salt)
        elif self.path.exists():
            stored = json.loads(self.path.read_text())
            self.salt, self.hash = bytes.fromhex(stored["salt"]), stored["hash"]
        else:
            self.generated = secrets.token_urlsafe(12)
            self.set_password(self.generated)
            log.warning(
                "No password configured. Generated one for the web interface: %s "
                "(shown only this once; change it under Settings)",
                self.generated,
            )

    def set_password(self, password: str) -> None:
        self.salt = os.urandom(16)
        self.hash = _hash(password, self.salt)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({"salt": self.salt.hex(), "hash": self.hash}, handle)
        self.sessions.clear()

    def check(self, password: str) -> bool:
        return hmac.compare_digest(_hash(password, self.salt), self.hash)

    def login(self, password: str) -> str | None:
        """Returns a session token, or None. Raises PermissionError while locked."""
        now = time.time()
        if now < self.locked_until:
            raise PermissionError(int(self.locked_until - now) + 1)
        if not self.check(password):
            self.failures += 1
            if self.failures >= MAX_FAILURES:
                self.failures = 0
                self.locked_until = now + LOCK_SECONDS
            return None
        self.failures = 0
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + self.session_seconds
        return token

    def valid(self, token: str | None) -> bool:
        if self.disabled:
            return True
        expiry = self.sessions.get(token or "")
        if expiry is None:
            return False
        if expiry < time.time():
            del self.sessions[token]
            return False
        return True

    def logout(self, token: str | None) -> None:
        self.sessions.pop(token or "", None)
