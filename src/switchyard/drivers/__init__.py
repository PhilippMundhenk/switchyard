"""Switch protocols behind one interface (see base.py)."""

from .base import (
    AuthError,
    Backend,
    PasswordRequired,
    SwitchDriver,
    SwitchError,
    Unreachable,
    Unsupported,
)

__all__ = [
    "AuthError",
    "Backend",
    "PasswordRequired",
    "SwitchDriver",
    "SwitchError",
    "Unreachable",
    "Unsupported",
]
