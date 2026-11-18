import json
import logging
import stat

from fastapi.testclient import TestClient

from switchyard import config as cfg
from switchyard.web.app import create_app

from .conftest import PASSWORD, make_env

MAC = "00:26:f2:aa:10:01"
D = f"/api/devices/{MAC}"


def test_every_api_route_needs_a_session(config):
    app = create_app(config)
    open_paths = {"/api/login", "/api/session", "/api/health", "/health"}
    with TestClient(app) as anon:
        paths = app.openapi()["paths"]
        assert len(paths) > 25
        for route, methods in paths.items():
            if route in open_paths:
                continue
            for method in methods:
                r = anon.request(method.upper(), route.replace("{mac}", MAC), json={})
                assert r.status_code == 401, (method, route)
                assert r.json()["code"] == "login_required"


def test_session_login_logout_and_cookie(config):
    with TestClient(create_app(config)) as c:
        assert c.get("/api/session").json()["authenticated"] is False
        assert c.post("/api/login", json={"password": "wrong"}).status_code == 401
        r = c.post("/api/login", json={"password": PASSWORD})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert c.get("/api/session").json()["authenticated"] is True
        assert c.post("/api/logout").status_code == 200
        assert c.get("/api/devices").status_code == 401


def test_login_lockout(config, monkeypatch):
    with TestClient(create_app(config)) as c:
        for _ in range(5):
            assert c.post("/api/login", json={"password": "bad"}).status_code == 401
        r = c.post("/api/login", json={"password": PASSWORD})
        assert r.status_code == 429 and r.json()["code"] == "locked"
        c.app.state.auth.locked_until = 0
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_session_expiry(config):
    with TestClient(create_app(config)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        auth = c.app.state.auth
        for token in auth.sessions:
            auth.sessions[token] = 0
        assert c.get("/api/devices").status_code == 401


def test_foreign_origin_is_refused(client):
    r = client.post("/api/logout", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "bad_origin"
    ok = client.post("/api/discovery/scan", headers={"Origin": "http://testserver"})
    assert ok.status_code == 200


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/devices").headers["cache-control"] == "no-store"
    assert "Switchyard" in r.text


def test_generated_first_start_password(tmp_path, sim, caplog):
    env = make_env(sim.port)
    del env["SWITCHYARD_AUTH_PASSWORD"]
    with (
        caplog.at_level(logging.WARNING),
        TestClient(create_app(cfg.load(tmp_path, env=env))) as c,
    ):
        generated = c.app.state.auth.generated
        assert generated and generated in caplog.text
        assert c.post("/api/login", json={"password": generated}).status_code == 200
    secrets = tmp_path / "secrets.json"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert generated not in secrets.read_text()
    # second start: the stored hash is used, nothing is generated or logged again
    caplog.clear()
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        assert c.app.state.auth.generated is None
        assert c.post("/api/login", json={"password": generated}).status_code == 200
    assert generated not in caplog.text


def test_auth_disabled(tmp_path, sim):
    env = make_env(sim.port, SWITCHYARD_AUTH_DISABLED="true")
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        assert c.get("/api/devices").status_code == 200
        assert c.get("/api/session").json()["auth_disabled"] is True
        assert (
            c.post("/api/password", json={"current": "", "new": "newpassword1"}).status_code == 422
        )


def test_app_password_change(tmp_path, sim):
    env = make_env(sim.port)
    del env["SWITCHYARD_AUTH_PASSWORD"]
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        first = c.app.state.auth.generated
        c.post("/api/login", json={"password": first})
        assert (
            c.post("/api/password", json={"current": "nope", "new": "newpassword1"}).status_code
            == 422
        )
        assert c.post("/api/password", json={"current": first, "new": "short"}).status_code == 422
        assert (
            c.post("/api/password", json={"current": first, "new": "newpassword1"}).status_code
            == 200
        )
        assert c.get("/api/devices").status_code == 401  # sessions end
        assert c.post("/api/login", json={"password": first}).status_code == 401
        assert c.post("/api/login", json={"password": "newpassword1"}).status_code == 200


def test_password_from_config_cannot_be_changed_in_ui(client):
    r = client.post("/api/password", json={"current": PASSWORD, "new": "newpassword1"})
    assert r.status_code == 422 and "configuration" in r.json()["message"]


def test_discovery_add_update_remove(client, tmp_path):
    scan = client.post("/api/discovery/scan").json()
    assert len(scan["found"]) == 4 and not any(f["managed"] for f in scan["found"])
    r = client.post("/api/devices", json={"mac": MAC.upper(), "label": "Office"})
    assert r.status_code == 201 and r.json()["label"] == "Office" and r.json()["online"]
    assert client.post("/api/devices", json={"mac": MAC}).status_code == 422  # duplicate
    assert client.post("/api/devices", json={}).status_code == 422
    assert client.post("/api/devices", json={"mac": "nonsense"}).status_code == 422
    assert client.get("/api/discovery").json()["found"][0]["managed"] is True
    r = client.patch(D, json={"label": "Desk", "notes": "n"})
    assert (r.json()["label"], r.json()["notes"]) == ("Desk", "n")
    assert (
        client.patch(D, json={"set_address": True, "address": "127.0.0.1"}).json()["address"]
        == "127.0.0.1"
    )
    assert client.patch(D, json={"set_address": True, "address": None}).json()["address"] is None
    assert client.patch(D, json={"set_address": True, "address": "999.1.1.1"}).status_code == 422
    assert client.get("/api/devices").json()["devices"][0]["mac"] == MAC
    assert client.get(D).status_code == 200
    assert stat.S_IMODE((tmp_path / "devices.json").stat().st_mode) == 0o600
    assert client.delete(D).status_code == 200
    assert client.get(D).status_code == 404
    assert client.delete(D).status_code == 404


def test_add_by_ip_and_unknown_address(client):
    r = client.post("/api/devices", json={"address": "127.0.0.1"})
    assert r.status_code == 201 and r.json()["address"] == "127.0.0.1"
    assert client.post("/api/devices", json={"address": "not-an-ip"}).status_code == 422


def test_mac_in_path_accepts_dashes_and_upper_case(client, office):
    for form in (MAC.upper(), MAC.replace(":", "-"), MAC.upper().replace(":", "-")):
        assert client.get(f"/api/devices/{form}").status_code == 200
    assert client.get("/api/devices/aa:bb:cc:dd:ee:ff").status_code == 404


def test_password_storage_follows_remember(client):
    client.post("/api/discovery/scan")
    client.post("/api/devices", json={"mac": MAC})
    assert client.get(D).json()["has_password"] is False
    r = client.put(f"{D}/loop-detection", json={"enabled": True})
    assert r.status_code == 409 and r.json()["code"] == "password_required"
    bad = client.post(f"{D}/login", json={"password": "wrong"})
    assert bad.status_code == 409 and bad.json()["code"] == "switch_auth"
    ok = client.post(f"{D}/login", json={"password": "password", "remember": False})
    assert ok.status_code == 200 and ok.json()["remembered"] is False
    assert client.put(f"{D}/loop-detection", json={"enabled": True}).status_code == 200
    client.post(f"{D}/login", json={"password": "password", "remember": True})
    assert client.get(D).json()["remembered"] is True
    assert client.patch(D, json={"forget_password": True}).json()["remembered"] is False


def test_state_and_ports(client, office):
    s = client.get(f"{D}/state").json()
    assert (
        s["info"]["model"] == "GS108E" and len(s["ports"]) == 8 and s["vlan"]["mode"] == "disabled"
    )
    assert s["device"]["online"] and s["rate_codes"]["1"] == 512
    assert len(client.get(f"{D}/ports").json()["ports"]) == 8


def test_unreachable_switch_is_504(client):
    client.sim.switches[0].offline_until = 1e18
    client.post("/api/devices", json={"mac": MAC})
    client.app.state.manager.transport.timeout = 0.1
    client.app.state.manager.transport.retries = 0
    r = client.get(f"{D}/state")
    assert r.status_code == 504 and r.json()["code"] == "unreachable"


def test_network_validation_and_write(client, office):
    assert client.put(f"{D}/network", json={"dhcp": False, "ip": "192.168.0.5"}).status_code == 422
    r = client.put(
        f"{D}/network",
        json={
            "dhcp": False,
            "ip": "192.168.0.5",
            "netmask": "255.255.255.0",
            "gateway": "10.1.1.1",
        },
    )
    assert r.status_code == 422
    assert client.put(f"{D}/network", json={"ip": "bogus"}).status_code == 422
    r = client.put(
        f"{D}/network",
        json={"name": "renamed", "dhcp": False, "ip": "192.168.0.5", "netmask": "255.255.255.0"},
    )
    assert r.status_code == 200
    assert client.sim.switches[0].name == "renamed" and client.sim.switches[0].ip == "192.168.0.5"


def test_vlan_flow(client, office):
    assert client.put(f"{D}/vlan/mode", json={"mode": "bogus"}).status_code == 422
    assert client.put(f"{D}/vlan/mode", json={"mode": "dot1q_advanced"}).status_code == 200
    body = {
        "vlans": [
            {"id": 1, "members": [1, 2, 3, 4]},
            {"id": 20, "members": [1, 5, 6, 7, 8], "tagged": [1]},
        ],
        "pvid": {"5": 20, "6": 20, "7": 20, "8": 20},
    }
    assert client.put(f"{D}/vlan/dot1q", json=body).status_code == 200
    v = client.get(f"{D}/state").json()["vlan"]
    assert v["pvid"]["5"] == 20 and {x["id"] for x in v["dot1q"]} == {1, 20}
    bad = {**body, "pvid": {"5": 99}}
    assert client.put(f"{D}/vlan/dot1q", json=bad).status_code == 422
    assert (
        client.put(f"{D}/vlan/dot1q", json={"vlans": [{"id": 0, "members": []}]}).status_code == 422
    )
    assert client.put(f"{D}/vlan/mode", json={"mode": "port_advanced"}).status_code == 200
    r = client.put(
        f"{D}/vlan/port-based",
        json={"vlans": [{"id": 1, "members": [1, 2]}, {"id": 2, "members": [3]}]},
    )
    assert r.status_code == 200
    assert {x["id"] for x in client.get(f"{D}/state").json()["vlan"]["port_vlans"]} == {1, 2}


def test_qos_mirror_igmp_loop_stats_cable(client, office):
    r = client.put(
        f"{D}/qos",
        json={
            "mode": "port",
            "priorities": {"1": "high"},
            "ingress": {"2": 5},
            "storm_control": True,
            "storm_rates": {"1": 3},
        },
    )
    assert r.status_code == 200
    q = client.get(f"{D}/state").json()["qos"]
    assert q["mode"] == "port" and q["priorities"]["1"] == "high" and q["ingress"]["2"] == 5
    assert client.put(f"{D}/qos", json={"ingress": {"2": 99}}).status_code == 422
    assert client.put(f"{D}/qos", json={"priorities": {"1": "urgent"}}).status_code == 422
    assert client.put(f"{D}/mirror", json={"destination": 8, "sources": [1]}).status_code == 200
    assert client.put(f"{D}/mirror", json={"destination": 1, "sources": [1]}).status_code == 422
    assert (
        client.put(
            f"{D}/igmp", json={"enabled": True, "vlan": 1, "block_unknown": True}
        ).status_code
        == 200
    )
    assert client.put(f"{D}/igmp", json={"enabled": True, "vlan": 9999}).status_code == 422
    assert client.put(f"{D}/loop-detection", json={"enabled": True}).status_code == 200
    client.sim.switches[0].counters[1][0] = 77
    assert client.post(f"{D}/stats/reset").status_code == 200
    assert client.sim.switches[0].counters[1][0] < 77
    r = client.post(f"{D}/cable-test", json={"ports": [1, 3]})
    assert [x["status"] for x in r.json()["results"]] == ["ok", "open"]
    assert client.post(f"{D}/cable-test", json={"ports": []}).status_code == 422


def test_unsupported_setting_is_502(client):
    client.post("/api/discovery/scan")
    mac = "e0:91:f5:cc:30:03"
    client.post("/api/devices", json={"mac": mac, "password": "password"})
    r = client.put(f"/api/devices/{mac}/loop-detection", json={"enabled": True})
    assert r.status_code == 502 and r.json()["code"] == "switch_error"
    state = client.get(f"/api/devices/{mac}/state").json()
    assert state["loop_detection"] is None


def test_switch_password_change_updates_stored_one(client, office):
    assert client.post(f"{D}/password", json={"new": ""}).status_code == 422
    assert client.post(f"{D}/password", json={"new": "newsecret"}).status_code == 200
    assert client.app_.state.manager.device(MAC)["password"] == "newsecret"
    assert client.put(f"{D}/loop-detection", json={"enabled": True}).status_code == 200


def test_reboot_and_factory_reset_need_the_mac(client, office):
    for path in ("reboot", "factory-reset"):
        assert client.post(f"{D}/{path}", json={"confirm": "wrong"}).status_code == 422
    assert client.post(f"{D}/reboot", json={"confirm": MAC}).status_code == 200
    assert client.sim.switches[0].online is False
    import time

    time.sleep(0.6)
    client.app_.state.manager.transport.timeout = 0.3
    assert client.post(f"{D}/factory-reset", json={"confirm": MAC.upper()}).status_code == 200
    assert client.app_.state.manager.device(MAC)["password"] is None


def test_backup_restore_round_trip(client, office):
    client.put(f"{D}/vlan/mode", json={"mode": "dot1q_advanced"})
    client.put(
        f"{D}/vlan/dot1q",
        json={
            "vlans": [
                {"id": 1, "members": [1, 2, 3, 4]},
                {"id": 20, "members": [1, 5, 6, 7, 8], "tagged": [1]},
            ],
            "pvid": {"5": 20, "6": 20, "7": 20, "8": 20},
        },
    )
    client.put(f"{D}/qos", json={"mode": "port", "priorities": {"1": "high"}, "egress": {"3": 8}})
    client.put(f"{D}/mirror", json={"destination": 8, "sources": [1]})
    client.put(f"{D}/igmp", json={"enabled": True, "vlan": 20, "block_unknown": True})
    before = client.get(f"{D}/backup")
    assert "attachment" in before.headers["content-disposition"]
    data = before.json()
    assert data["format"] == "switchyard-backup" and "password" not in json.dumps(data).lower()
    # factory reset, then restore
    client.post(f"{D}/factory-reset", json={"confirm": MAC})
    import time

    time.sleep(0.6)
    client.post(f"{D}/login", json={"password": "password"})
    r = client.post(f"{D}/restore", json={"backup": data})
    assert r.status_code == 200 and r.json()["ok"], r.json()
    after = client.get(f"{D}/backup").json()
    for key in ("vlan", "qos", "mirror", "igmp", "loop_detection"):
        assert after["config"][key] == data["config"][key], key


def test_restore_refuses_bad_files(client, office):
    for bad in ({}, {"format": "other"}, {"format": "switchyard-backup", "version": 2}):
        assert client.post(f"{D}/restore", json={"backup": bad}).status_code == 422
    wrong_ports = {
        "format": "switchyard-backup",
        "version": 1,
        "switch": {"ports": 5},
        "config": {},
    }
    assert client.post(f"{D}/restore", json={"backup": wrong_ports}).status_code == 422
    broken = {
        "format": "switchyard-backup",
        "version": 1,
        "switch": {"ports": 8},
        "config": {"vlan": {"mode": "dot1q_advanced"}},
    }
    r = client.post(f"{D}/restore", json={"backup": broken})
    assert r.status_code == 422 and "damaged" in r.json()["message"]
    odd = {
        "format": "switchyard-backup",
        "version": 1,
        "switch": {"ports": 8},
        "config": {"qos": 5},
    }
    assert client.post(f"{D}/restore", json={"backup": odd}).status_code == 422


def test_restore_with_network_part(client, office):
    data = client.get(f"{D}/backup").json()
    data["config"]["network"]["name"] = "restored"
    r = client.post(f"{D}/restore", json={"backup": data, "include_network": True})
    assert r.json()["ok"] and client.sim.switches[0].name == "restored"
    bad = client.post(
        f"{D}/restore", json={"backup": {**data, "config": {"vlan": {"mode": "disabled"}}}}
    )
    assert bad.status_code == 200


def test_packet_log_and_filters(client, office):
    client.get(f"{D}/state")
    entries = client.get("/api/log").json()["entries"]
    assert entries and {e["direction"] for e in entries} == {"tx", "rx"}
    last = entries[-1]["id"]
    assert client.get(f"/api/log?after={last}").json()["entries"] == []
    mine = client.get(f"/api/log?device={MAC}").json()["entries"]
    assert mine and all(e["device"] == MAC for e in mine)
    blob = json.dumps(entries)
    assert "hex" in blob


def test_system_meta_health_static(client):
    s = client.get("/api/system").json()
    assert s["client_port"] and s["targets"] and s["password_from_config"] is True
    assert "rate_codes" in client.get("/api/meta").json()
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/health").json()["demo"] is False
    assert client.get("/style.css").status_code == 200
    assert client.get("/api/docs").status_code in (200, 401)


def test_demo_mode_starts_its_own_switches(tmp_path):
    env = {
        "SWITCHYARD_DEMO_ENABLED": "true",
        "SWITCHYARD_AUTH_PASSWORD": PASSWORD,
        "SWITCHYARD_NSDP_POLL_SECONDS": "0",
        "SWITCHYARD_NSDP_DISCOVERY_SECONDS": "0.3",
    }
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        assert c.get("/api/session").json()["demo"] is True
        c.post("/api/login", json={"password": PASSWORD})
        assert len(c.post("/api/discovery/scan").json()["found"]) == 4


def test_background_poller_marks_switches_online(tmp_path, sim):
    env = make_env(sim.port, SWITCHYARD_NSDP_POLL_SECONDS="1")
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        c.post("/api/login", json={"password": PASSWORD})
        import time

        time.sleep(1.0)
        assert c.get("/api/discovery").json()["found"]


def test_stored_device_found_by_address_when_broadcast_misses(client):
    client.post("/api/devices", json={"address": "127.0.0.1"})
    mac = client.get("/api/devices").json()["devices"][0]["mac"]
    assert client.post("/api/discovery/scan").status_code == 200
    assert client.get(f"/api/devices/{mac}").json()["online"]


def test_pydantic_errors_are_422_not_500(client, office):
    assert (
        client.put(
            f"{D}/qos", content=b"not json", headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert client.put(f"{D}/vlan/dot1q", json={"vlans": "x"}).status_code == 422
    assert client.put(f"{D}/qos", json={"ingress": {"abc": 1}}).status_code == 422
    assert client.put(f"{D}/mirror", json={"destination": 99, "sources": [1]}).status_code == 422


def test_restore_without_password_asks_for_it_instead_of_failing_a_step(client):
    client.post("/api/discovery/scan")
    client.post("/api/devices", json={"mac": MAC, "password": "password"})
    data = client.get(f"{D}/backup").json()
    client.patch(D, json={"forget_password": True})
    r = client.post(f"{D}/restore", json={"backup": data})
    assert r.status_code == 409 and r.json()["code"] == "password_required"
