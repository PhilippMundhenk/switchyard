import os
import time

import pytest

from switchyard.backups import BackupStore
from switchyard.diff import diff_config

MAC = "00:26:f2:aa:10:01"
D = f"/api/devices/{MAC}"


def test_store_saves_lists_prunes_and_protects(tmp_path):
    store = BackupStore(tmp_path / "b", keep=3)
    names = [store.save(MAC, {"n": i}) for i in range(5)]
    assert len(set(names)) == 5 and names[1].endswith("-002.json")
    rows = store.list(MAC)
    assert len(rows) == 3 and rows[0]["name"] == names[-1]  # newest first, oldest pruned
    assert store.read(MAC, names[-1]) == {"n": 4}
    assert oct(os.stat(store.path(MAC, names[-1])).st_mode & 0o777) == "0o600"
    assert store.latest(MAC) == {"n": 4}
    store.delete(MAC, names[-1])
    assert store.latest(MAC) == {"n": 3}
    for bad in ("../x.json", "a.json", "20200101-000000.json", "20200101-000000-001.json/../../x"):
        with pytest.raises(ValueError):
            store.path(MAC, bad)
    with pytest.raises(ValueError):
        store.list("../../etc")
    with pytest.raises(FileNotFoundError):
        store.read(MAC, "20200101-000000-001.json")
    with pytest.raises(FileNotFoundError):
        store.delete(MAC, "20200101-000000-001.json")
    assert (
        BackupStore(tmp_path / "none", 1).list(MAC) == []
        and BackupStore(tmp_path / "n", 1).latest(MAC) is None
    )


def test_diff_lines():
    live = {
        "network": {"name": "a", "dhcp": False, "ip": "1.1.1.1"},
        "vlan": {
            "mode": "dot1q_advanced",
            "dot1q": [
                {"id": 1, "members": [1, 2], "tagged": []},
                {"id": 5, "members": [1], "tagged": []},
            ],
            "port_vlans": [],
            "pvid": {"1": 1, "2": 1},
        },
        "qos": {
            "mode": "dot1p",
            "storm_control": False,
            "priorities": {"1": "normal"},
            "ingress": {"1": 0},
            "egress": {},
            "storm_rates": {},
        },
        "lag": {"lags": [{"id": 1, "enabled": False, "members": []}]},
        "port_admin": {"1": {"speed_code": 0, "speed": "auto", "flow_control": False}},
        "mirror": {"destination": 0, "sources": []},
        "igmp": {"enabled": False},
        "loop_detection": False,
        "labels": {"ports": {}, "vlans": {}},
    }
    other = {
        "network": {"name": "b", "dhcp": False, "ip": "1.1.1.1"},
        "vlan": {
            "mode": "dot1q_advanced",
            "dot1q": [
                {"id": 1, "members": [1, 2, 3], "tagged": [1]},
                {"id": 9, "members": [2], "tagged": []},
            ],
            "port_vlans": [],
            "pvid": {"1": 9, "2": 1},
        },
        "qos": {
            "mode": "port",
            "storm_control": True,
            "priorities": {"1": "high"},
            "ingress": {"1": 5},
            "egress": {},
            "storm_rates": {},
        },
        "lag": {"lags": [{"id": 1, "enabled": True, "members": [1, 2]}]},
        "port_admin": {"1": {"speed_code": 5, "speed": "100M full", "flow_control": True}},
        "mirror": {"destination": 8, "sources": [1]},
        "igmp": {"enabled": True},
        "loop_detection": True,
        "labels": {"ports": {"1": "NAS"}, "vlans": {}},
    }
    text = "\n".join(f"{c['area']}: {c['text']}" for c in diff_config(live, other))
    for expected in (
        "name: a → b",
        "VLAN 5 would be removed",
        "VLAN 9 would be added (ports 2)",
        "VLAN 1 members: 1, 2 → 1, 2, 3",
        "VLAN 1 tagged: none → 1",
        "Port 1 PVID: 1 → 9",
        "mode: dot1p → port",
        "Port 1 priority: normal → high",
        "Port 1 ingress limit: 0 → 5",
        "LAG 1: off (none) → on (1, 2)",
        "Port 1: auto → 100M full",
        "destination 0",
        "IGMP snooping",
        "enabled: False → True",
        "Port or VLAN names would change",
    ):
        assert expected in text, expected
    assert diff_config(live, live) == []
    other_mode = {**other, "vlan": {**other["vlan"], "mode": "port_basic"}}
    assert "mode: dot1q_advanced → port_basic" in "\n".join(
        c["text"] for c in diff_config(live, other_mode)
    )


def test_api_backups_cycle_and_restore_from_stored(client, office):
    assert client.get(f"{D}/backups").json() == {"backups": [], "interval_hours": 24}
    name = client.post(f"{D}/backups").json()["name"]
    assert client.get(f"{D}/backups").json()["backups"][0]["name"] == name
    dl = client.get(f"{D}/backups/{name}")
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    assert dl.json()["format"] == "switchyard-backup"
    # diff: same, then different after a live change
    assert client.post(f"{D}/diff", json={"stored": name}).json() == {"same": True, "changes": []}
    client.put(f"{D}/loop-detection", json={"enabled": True})
    changes = client.post(f"{D}/diff", json={"stored": name}).json()
    assert changes["same"] is False and any(
        c["area"] == "Loop detection" for c in changes["changes"]
    )
    r = client.post(f"{D}/restore", json={"stored": name})
    assert r.json()["ok"] and client.sim.switches[0].loop_detection is False
    assert client.post(f"{D}/diff", json={"stored": name}).json()["same"] is True
    # refusals
    assert client.post(f"{D}/restore", json={}).status_code == 422
    assert client.post(f"{D}/restore", json={"stored": name, "backup": {}}).status_code == 422
    assert client.post(f"{D}/diff", json={"stored": "20200101-000000-001.json"}).status_code == 404
    assert client.post(f"{D}/diff", json={"stored": "../../x"}).status_code == 422
    assert client.post(f"{D}/diff", json={"backup": {"format": "no"}}).status_code == 422
    broken = {"format": "switchyard-backup", "version": 1, "switch": {}, "config": {"vlan": 5}}
    assert client.post(f"{D}/diff", json={"backup": broken}).status_code == 422
    assert client.get(f"{D}/backups/20200101-000000-001.json").status_code == 404
    assert client.get(f"{D}/backups/..%2f..%2fx").status_code in (404, 422)
    assert client.delete(f"{D}/backups/{name}").status_code == 200
    assert client.delete(f"{D}/backups/{name}").status_code == 404
    assert client.get("/api/devices/aa:bb:cc:dd:ee:ff/backups").status_code == 404


def test_scheduled_backup_loop(tmp_path, sim):
    from fastapi.testclient import TestClient

    from switchyard import config as cfg
    from switchyard.web.app import create_app

    from .conftest import PASSWORD, make_env

    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_BACKUP_INTERVAL_HOURS="1")
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        c.post("/api/login", json={"password": PASSWORD})
        c.post("/api/discovery/scan")
        c.post("/api/devices", json={"mac": MAC, "password": "password"})
        m = c.app.state.manager
        m.backup_tick = 0.3
        deadline = time.time() + 6
        while time.time() < deadline and not c.get(f"{D}/backups").json()["backups"]:
            time.sleep(0.2)
        first = c.get(f"{D}/backups").json()["backups"]
        assert len(first) == 1
        # not due again within the interval, and an unchanged config is not stored twice
        time.sleep(0.8)
        assert len(c.get(f"{D}/backups").json()["backups"]) == 1
        assert c.portal.call(m.backup_due, MAC, 1) is False
        assert c.portal.call(m.backup_due, MAC, 0) is True
        old = tmp_path / "backups" / MAC.replace(":", "")
        for f in old.iterdir():
            os.utime(f, (time.time() - 7200, time.time() - 7200))
        time.sleep(1.0)  # due again, config unchanged: touched, not duplicated
        assert len(c.get(f"{D}/backups").json()["backups"]) == 1
        assert c.portal.call(m.backup_due, MAC, 1) is False
        c.put(f"{D}/loop-detection", json={"enabled": True})
        for f in old.iterdir():
            os.utime(f, (time.time() - 7200, time.time() - 7200))
        deadline = time.time() + 6
        while time.time() < deadline and len(c.get(f"{D}/backups").json()["backups"]) < 2:
            time.sleep(0.2)
        assert len(c.get(f"{D}/backups").json()["backups"]) == 2  # changed: stored again
