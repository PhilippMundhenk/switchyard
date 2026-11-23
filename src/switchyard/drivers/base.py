"""The interface every switch protocol implements.

The rest of Switchyard (manager, monitor, backups, MQTT, web API) only talks to a
:class:`SwitchDriver` for one switch and to a :class:`Backend` for discovery. A driver
returns the same dictionaries whatever the vendor; a setting the switch does not have is
``None`` in the state, and a write it cannot do raises :class:`Unsupported`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class SwitchError(Exception):
    """The switch answered, but with an error."""


class AuthError(SwitchError):
    """The switch refused the password (or user name)."""


class Unsupported(SwitchError):
    """This switch or protocol cannot do that."""

    def __init__(self, what: str):
        self.what = what
        super().__init__(f"This switch does not support {what}.")


class PasswordRequired(Exception):
    """A write was attempted without a stored password."""


class Unreachable(Exception):
    """The switch did not answer."""


# state section -> driver method; "features" returns mirror, igmp, loop_detection (and led)
PARTS = {
    "info": "read_info",
    "ports": "read_ports",
    "vlan": "read_vlan",
    "qos": "read_qos",
    "lag": "read_lag",
    "port_admin": "read_port_admin",
    "features": "read_features",
}


class SwitchDriver(ABC):
    """One switch. Every method may raise Unreachable, AuthError or SwitchError."""

    protocol: ClassVar[str] = ""
    vendor: ClassVar[str] = ""
    #: whether the switch can be reached by unicast to its IP address (add by IP, routed nets)
    unicast: ClassVar[bool] = True
    #: whether a login needs a user name besides the password
    needs_username: ClassVar[bool] = False

    mac: bytes
    address: str | None
    password: str | None
    username: str | None = None

    def _unsupported(self, what: str):
        raise Unsupported(what)

    # -- reading -------------------------------------------------------------

    @abstractmethod
    async def login(self, password: str, username: str | None = None) -> None: ...

    @abstractmethod
    async def read_info(self) -> dict: ...

    @abstractmethod
    async def read_ports(self) -> list[dict]: ...

    @abstractmethod
    async def read_state(self) -> dict: ...

    async def read_port_admin(self) -> dict | None:
        return None

    async def read_part(self, part: str):
        """One section of read_state, to refresh only what a change touched."""
        method = PARTS.get(part)
        if method is None:
            raise ValueError(f"unknown part {part}")
        return await getattr(self, method)()

    async def scan_tags(self, tags: list[int], timeout: float = 0.8) -> list[dict]:
        self._unsupported("the settings scan")

    def diagnostic_tags(self) -> list[int]:
        """Setting numbers the diagnostics scan asks for."""
        return []

    def tag_allowed(self, tag: int) -> bool:
        """Whether the scan may ask for this extra setting number (never credentials)."""
        return False

    # -- writing (default: not supported) ------------------------------------

    async def set_network(self, **fields) -> None:
        self._unsupported("changing name and IP settings")

    async def set_vlan_mode(self, mode: str, uplink: int | None = None) -> None:
        self._unsupported("this VLAN mode")

    async def set_port_vlans(self, vlans: list[dict]) -> None:
        self._unsupported("port based VLANs")

    async def apply_dot1q(self, vlans: list[dict], pvid: dict[int, int]) -> None:
        self._unsupported("802.1Q VLANs")

    async def set_qos(self, **fields) -> None:
        self._unsupported("QoS")

    async def set_mirror(self, destination: int, sources: list[int], **directions) -> None:
        self._unsupported("port mirroring")

    async def set_igmp(self, **fields) -> None:
        self._unsupported("IGMP snooping")

    async def set_loop_detection(self, enabled: bool) -> None:
        self._unsupported("loop detection")

    async def set_led(self, enabled: bool) -> None:
        self._unsupported("switching the port LEDs")

    async def apply_lags(self, lags: list[dict]) -> None:
        self._unsupported("link aggregation")

    async def set_port_admin(self, settings: dict[int, dict]) -> None:
        self._unsupported("port speed settings")

    async def set_poe(self, settings: dict[int, dict]) -> None:
        self._unsupported("PoE settings")

    async def reset_stats(self) -> None:
        self._unsupported("resetting the statistics")

    async def cable_test(self, port: int) -> dict:
        self._unsupported("the cable test")

    async def reboot(self) -> None:
        self._unsupported("restarting")

    async def factory_reset(self) -> None:
        self._unsupported("a factory reset")

    async def change_password(self, new_password: str) -> None:
        self._unsupported("changing the password")

    async def upgrade_firmware(self, image: bytes, *args, **kwargs) -> None:
        self._unsupported("firmware upgrades")


class Backend(ABC):
    """One protocol: finds switches and makes drivers for them."""

    protocol: ClassVar[str] = ""
    vendor: ClassVar[str] = ""
    unicast: ClassVar[bool] = True
    needs_username: ClassVar[bool] = False

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def discover(self, seconds: float) -> list[dict]:
        """Identities of the switches that answer: mac, model, name, ip, ... ."""

    async def probe(self, address: str, timeout: float = 2.0) -> dict | None:
        """Identity of the switch at this unicast address, or None."""
        return None

    @abstractmethod
    def driver(
        self, mac: str, address: str | None, password: str | None, username: str | None
    ) -> SwitchDriver: ...

    def packet_log(self) -> list:
        return []
