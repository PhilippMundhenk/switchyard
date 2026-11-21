"""The driver interface: several protocols behind one manager."""

from __future__ import annotations

import pytest

from switchyard.drivers import Backend, SwitchDriver, Unsupported

FAKE_MAC = "02:00:00:00:00:01"


class FakeDriver(SwitchDriver):
    protocol = "fake"
    vendor = "Example"
    unicast = False
    needs_username = True

    def __init__(self, mac, address, password, username):
        self.mac, self.address, self.password, self.username = mac, address, password, username
        self.logins: list[tuple[str, str | None]] = []

    async def login(self, password, username=None):
        if password != "secret":
            from switchyard.drivers import AuthError

            raise AuthError("Incorrect user name or password.")
        self.logins.append((password, username))
        self.password, self.username = password, username or "admin"

    async def read_info(self):
        return {"mac": FAKE_MAC, "model": "EX-8", "ports": 2}

    async def read_ports(self):
        return [{"port": 1, "up": True, "speed_mbps": 1000}, {"port": 2, "up": False}]

    async def read_state(self):
        return {
            "info": {
                "mac": FAKE_MAC,
                "model": "EX-8",
                "name": "",
                "ip": "10.0.0.2",
                "netmask": "255.255.255.0",
                "gateway": "10.0.0.1",
                "dhcp": False,
                "firmware": "1.0",
                "ports": 2,
            },
            "ports": await self.read_ports(),
            "vlan": None,
        }


class FakeBackend(Backend):
    protocol = "fake"
    vendor = "Example"
    unicast = False
    needs_username = True

    def __init__(self, fail=False):
        self.fail = fail
        self.started = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    async def discover(self, seconds):
        if self.fail:
            raise OSError("no network")
        return [{"mac": FAKE_MAC, "model": "EX-8", "name": "", "ip": "10.0.0.2"}]

    def driver(self, mac, address, password, username):
        return FakeDriver(mac, address, password, username)


def test_defaults_raise_unsupported():
    import asyncio

    driver = FakeDriver(FAKE_MAC, None, None, None)
    with pytest.raises(Unsupported, match="port mirroring"):
        asyncio.run(driver.set_mirror(1, [2]))
    assert driver.diagnostic_tags() == [] and not driver.tag_allowed(1)
    assert asyncio.run(driver.read_port_admin()) is None


def test_second_protocol_in_search_list_and_api(client):
    manager = client.app.state.manager
    manager.backends["fake"] = FakeBackend()
    found = client.post("/api/discovery/scan").json()["found"]
    fake = next(f for f in found if f["mac"] == FAKE_MAC)
    assert fake["protocol"] == "fake"
    assert any(f.get("protocol") == "nsdp" for f in found)  # NETGEAR still found

    # wrong password: added, but not logged in; then with user name and password
    added = client.post("/api/devices", json={"mac": FAKE_MAC, "password": "nope"}).json()
    assert added["protocol"] == "fake" and added["vendor"] == "Example"
    assert added["needs_username"] is True and added["unicast"] is False
    assert added["has_password"] is False
    r = client.post(
        f"/api/devices/{FAKE_MAC}/login", json={"password": "secret", "username": "ops"}
    )
    assert r.status_code == 200 and r.json()["username"] == "ops"
    assert manager.store.get(FAKE_MAC)["username"] == "ops"

    state = client.get(f"/api/devices/{FAKE_MAC}/state").json()
    assert state["info"]["model"] == "EX-8" and state["vlan"] is None
    # a write the driver does not have is a clear 409, not a crash
    r = client.put(f"/api/devices/{FAKE_MAC}/mirror", json={"destination": 1, "sources": [2]})
    assert r.status_code == 409 and r.json()["code"] == "unsupported"
    assert "port mirroring" in r.json()["message"]


def test_failing_backend_does_not_stop_the_search(client, caplog):
    client.app.state.manager.backends["fake"] = FakeBackend(fail=True)
    found = client.post("/api/discovery/scan").json()["found"]
    assert found and all(f["protocol"] == "nsdp" for f in found)
    assert "search failed" in caplog.text


def test_unknown_protocol_is_reported(client):
    manager = client.app.state.manager
    manager.store.add(FAKE_MAC, protocol="gone")
    view = client.get("/api/devices").json()["devices"]
    assert any(d["mac"] == FAKE_MAC and d["vendor"] is None for d in view)
    r = client.get(f"/api/devices/{FAKE_MAC}/state")
    assert r.status_code == 422 and "gone" in r.json()["message"]
