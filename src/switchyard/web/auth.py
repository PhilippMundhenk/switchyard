"""Login for the web interface: one password, sessions in memory."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE = "switchyard_session"
MAX_FAILURES = 5
LOCK_SECONDS = 60
MAX_TRACKED_CLIENTS = 1000


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


class Auth:
    def __init__(
        self,
        data_dir: Path,
        password: str,
        disabled: bool,
        session_hours: float,
        viewer_password: str = "",
    ):
        self.disabled = disabled
        self.session_seconds = session_hours * 3600
        self.sessions: dict[str, float] = {}
        self.roles: dict[str, str] = {}
        self.viewer_salt = os.urandom(16)
        self.viewer_hash = _hash(viewer_password, self.viewer_salt) if viewer_password else ""
        self.failures: dict[str, int] = {}
        self.locked: dict[str, float] = {}
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
            announce(self.generated)
            log.warning("No password configured: generated one and printed it once on stderr")

    def set_password(self, password: str) -> None:
        self.salt = os.urandom(16)
        self.hash = _hash(password, self.salt)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({"salt": self.salt.hex(), "hash": self.hash}, handle)
        self.sessions.clear()
        self.roles.clear()

    def check(self, password: str) -> bool:
        return hmac.compare_digest(_hash(password, self.salt), self.hash)

    def check_viewer(self, password: str) -> bool:
        return bool(self.viewer_hash) and hmac.compare_digest(
            _hash(password, self.viewer_salt), self.viewer_hash
        )

    def login(self, password: str, client: str = "?") -> str | None:
        """Returns a session token, or None. Raises PermissionError while this client is locked.

        Failures are counted per client address, so one client cannot lock out the others.
        """
        now = time.time()
        until = self.locked.get(client, 0.0)
        if now < until:
            raise PermissionError(int(until - now) + 1)
        role = (
            "admin" if self.check(password) else "viewer" if self.check_viewer(password) else None
        )
        if role is None:
            if client not in self.failures and len(self.failures) >= MAX_TRACKED_CLIENTS:
                self.failures.pop(next(iter(self.failures)))  # forget the oldest
            self.failures[client] = self.failures.get(client, 0) + 1
            if self.failures[client] >= MAX_FAILURES:
                del self.failures[client]
                if len(self.locked) >= MAX_TRACKED_CLIENTS:
                    self.locked = {k: v for k, v in self.locked.items() if v > now}
                while len(self.locked) >= MAX_TRACKED_CLIENTS:  # still full: drop the oldest
                    self.locked.pop(min(self.locked, key=self.locked.get))
                self.locked[client] = now + LOCK_SECONDS
            return None
        self.failures.pop(client, None)
        self.locked.pop(client, None)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + self.session_seconds
        self.roles[token] = role
        return token

    def role(self, token: str | None) -> str | None:
        """``admin``, ``viewer`` or None for no valid session."""
        if self.disabled:
            return "admin"
        return self.roles.get(token or "") if self.valid(token) else None

    def valid(self, token: str | None) -> bool:
        if self.disabled:
            return True
        expiry = self.sessions.get(token or "")
        if expiry is None:
            return False
        if expiry < time.time():
            del self.sessions[token]
            self.roles.pop(token, None)
            return False
        return True

    def logout(self, token: str | None) -> None:
        self.sessions.pop(token or "", None)
        self.roles.pop(token or "", None)


def announce(password: str, stream=None) -> None:
    """Show a generated password once, on stderr, whatever the log level."""
    stream = stream or sys.stderr
    line = "=" * 64
    stream.write(
        f"\n{line}\n Switchyard: no password was configured, one was generated.\n"
        f" Web interface password: {password}\n"
        f" It is shown only this once and stored hashed in the data folder.\n"
        f" Change it under Settings, set SWITCHYARD_AUTH_PASSWORD, or run\n"
        f" 'switchyard reset-password' to generate a new one.\n{line}\n\n"
    )
    stream.flush()


def reset_password(data_dir: Path) -> str:
    """Generate a new password into secrets.json (takes effect at the next start)."""
    auth = Auth.__new__(Auth)
    auth.path = data_dir / "secrets.json"
    auth.sessions, auth.roles = {}, {}
    password = secrets.token_urlsafe(12)
    auth.set_password(password)
    return password
