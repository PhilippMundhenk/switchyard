import json
import stat

from fastapi.testclient import TestClient

from switchyard import config as cfg
from switchyard.web.app import create_app

from .conftest import PASSWORD, make_env

MAC = "00:26:f2:aa:10:01"
BIN = {"Content-Type": "application/octet-stream"}
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


def test_login_lockout_is_per_client(config):
    with TestClient(create_app(config)) as c:
        for _ in range(5):
            assert c.post("/api/login", json={"password": "bad"}).status_code == 401
        r = c.post("/api/login", json={"password": PASSWORD})
        assert r.status_code == 429 and r.json()["code"] == "locked"
        auth = c.app.state.auth
        # another client is not affected by this one's failures (no lock-out of the admin)
        assert auth.login(PASSWORD, "10.0.0.9")
        auth.locked["testclient"] = 0
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_lockout_tracking_is_bounded(config, monkeypatch):
    from switchyard.web import auth as auth_mod

    monkeypatch.setattr(auth_mod, "MAX_TRACKED_CLIENTS", 3)
    with TestClient(create_app(config)) as c:
        auth = c.app.state.auth
        for i in range(10):
            for _ in range(5):
                auth.login("bad", f"10.0.0.{i}")
        assert len(auth.failures) <= 3 and len(auth.locked) <= 3
        for i in range(10):
            auth.login("bad", f"10.0.1.{i}")
        assert len(auth.failures) <= 3


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


def test_generated_first_start_password(tmp_path, sim, capsys, caplog):
    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_LOG_LEVEL="error")
    del env["SWITCHYARD_AUTH_PASSWORD"]
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        generated = c.app.state.auth.generated
        assert c.post("/api/login", json={"password": generated}).status_code == 200
    err = capsys.readouterr().err
    assert generated and generated in err and "shown only this once" in err
    assert generated not in caplog.text  # only on stderr, not in the log
    secrets = tmp_path / "secrets.json"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert generated not in secrets.read_text()
    # second start: the stored hash is used, nothing is generated or shown again
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        assert c.app.state.auth.generated is None
        assert c.post("/api/login", json={"password": generated}).status_code == 200
    assert generated not in capsys.readouterr().err


def test_auth_disabled(tmp_path, sim):
    env = make_env(
        sim.port, SWITCHYARD_AUTH_DISABLED="true", SWITCHYARD_SERVER_ALLOWED_HOSTS="testserver"
    )
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        assert c.get("/api/devices").status_code == 200
        assert c.get("/api/session").json()["auth_disabled"] is True
        assert (
            c.post("/api/password", json={"current": "", "new": "newpassword1"}).status_code == 422
        )


def test_app_password_change(tmp_path, sim):
    env = make_env(sim.port, sim.tftp_port)
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
    css = client.get("/style.css")
    assert css.status_code == 200 and css.headers["cache-control"] == "no-cache"
    assert client.get("/app.js").headers["cache-control"] == "no-cache"
    assert client.get("/api/docs").status_code == 404  # no Swagger UI: it would load from a CDN
    assert client.get("/api/openapi.json").json()["info"]["title"] == "Switchyard"
    assert (
        "default-src 'self'" in client.get("/api/openapi.json").headers["content-security-policy"]
    )


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
        assert len(c.post("/api/discovery/scan").json()["found"]) == 6  # 4 NETGEAR, 2 TP-Link


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


def test_lag_and_port_settings(client, office):
    state = client.get(f"{D}/state").json()
    assert state["lag"]["groups"] == 4 and state["port_admin"]["1"]["speed"] == "auto"
    assert state["admin_speeds"]["6"] == "1G full"
    r = client.put(f"{D}/lag", json={"lags": [{"id": 1, "enabled": True, "members": [1, 2]}]})
    assert r.status_code == 200
    assert client.get(f"{D}/state").json()["lag"]["lags"][0]["members"] == [1, 2]
    bad = client.put(f"{D}/lag", json={"lags": [{"id": 2, "enabled": True, "members": [3]}]})
    assert bad.status_code == 422
    r = client.put(
        f"{D}/port-settings", json={"ports": {"5": {"speed_code": 3, "flow_control": True}}}
    )
    assert r.status_code == 200
    port5 = client.get(f"{D}/state").json()["port_admin"]["5"]
    assert port5["speed"] == "10M full" and port5["flow_control"] is True
    assert (
        client.put(f"{D}/port-settings", json={"ports": {"99": {"speed_code": 0}}}).status_code
        == 422
    )
    assert client.put(f"{D}/port-settings", json={"ports": {}}).status_code == 422
    assert (
        client.put(f"{D}/port-settings", json={"ports": {"1": {"speed_code": 99}}}).status_code
        == 422
    )


def test_backup_restore_includes_lag_and_speed(client, office):
    client.put(
        f"{D}/port-settings", json={"ports": {"1": {"speed_code": 5}, "2": {"speed_code": 5}}}
    )
    client.put(f"{D}/lag", json={"lags": [{"id": 1, "enabled": True, "members": [1, 2]}]})
    data = client.get(f"{D}/backup").json()
    assert data["config"]["lag"]["lags"][0]["members"] == [1, 2]
    client.put(f"{D}/lag", json={"lags": []})
    client.put(
        f"{D}/port-settings", json={"ports": {"1": {"speed_code": 0}, "2": {"speed_code": 0}}}
    )
    r = client.post(f"{D}/restore", json={"backup": data})
    assert r.json()["ok"], r.json()
    after = client.get(f"{D}/state").json()
    assert after["lag"]["lags"][0]["enabled"] and after["port_admin"]["1"]["speed"] == "100M full"


def _image(board="GS108E", version="100D"):
    from switchyard.nsdp.firmware import build_image

    return build_image(board, version, bytes(i % 251 for i in range(6000)))


def _wait_upgrade(client, state="done", seconds=20):
    import time

    end = time.time() + seconds
    while time.time() < end:
        s = client.get(f"{D}/firmware").json()
        if s["state"] == state or (state == "done" and s["state"] == "failed"):
            return s
        time.sleep(0.2)
    raise AssertionError(s)


def test_firmware_check_and_upgrade_flow(client, office):
    manager = client.app_.state.manager
    manager.upgrade_settle, manager.upgrade_poll = 0.05, 0.2
    manager.transport.tftp_timeout = 0.3
    assert client.get(f"{D}/firmware").json() == {"state": "idle"}
    image = _image()
    chk = client.post(f"{D}/firmware/check", content=image, headers=BIN).json()
    assert chk["matches"] is True and chk["image"]["version"] == "1.00.13"
    assert chk["switch"]["model"] == "GS108E" and chk["same_version"] is False
    other = client.post(f"{D}/firmware/check", content=_image(board="GS105E"), headers=BIN).json()
    assert other["matches"] is False
    # refusals
    assert (
        client.post(f"{D}/firmware", content=image, headers=BIN).status_code == 422
    )  # no confirmation
    assert client.post(f"{D}/firmware?confirm=wrong", content=image, headers=BIN).status_code == 422
    assert client.post(f"{D}/firmware?confirm={MAC}", content=b"", headers=BIN).status_code == 422
    assert (
        client.post(f"{D}/firmware?confirm={MAC}", content=b"junk" * 500, headers=BIN).status_code
        == 422
    )
    r = client.post(f"{D}/firmware?confirm={MAC}", content=_image(board="GS105E"), headers=BIN)
    assert r.status_code == 422 and "GS105E" in r.json()["message"]
    assert client.sim.switches[0].upgrades == 0 and not client.sim.switches[0].upgrade_mode
    # the real thing
    r = client.post(
        f"{D}/firmware?confirm={MAC.upper()}&filename=../../etc/x y.flash",
        content=image,
        headers=BIN,
    )
    assert r.status_code == 202 and r.json()["state"] == "running"
    status = _wait_upgrade(client)
    assert status["state"] == "done", status
    assert status["version"] == "1.00.13" and status["from_version"] == "1.00.12"
    assert client.sim.switches[0].firmware == "1.00.13"
    assert client.get(f"{D}").json()["info"]["firmware"] == "1.00.13"


def test_firmware_upgrade_needs_the_switch_password(client):
    client.post("/api/discovery/scan")
    client.post("/api/devices", json={"mac": MAC})
    r = client.post(f"{D}/firmware?confirm={MAC}", content=_image(), headers=BIN)
    assert r.status_code == 409 and r.json()["code"] == "password_required"
    client.post(f"{D}/login", json={"password": "wrong"})
    r = client.post(f"{D}/firmware?confirm={MAC}", content=_image(), headers=BIN)
    assert r.status_code == 409
    assert client.sim.switches[0].upgrades == 0


def test_firmware_failure_is_reported(client, office):
    manager = client.app_.state.manager
    manager.upgrade_settle = 0.05
    manager.transport.tftp_port = 9
    manager.transport.tftp_timeout = 0.05
    assert (
        client.post(f"{D}/firmware?confirm={MAC}", content=_image(), headers=BIN).status_code == 202
    )
    status = _wait_upgrade(client)
    assert status["state"] == "failed" and "stopped answering" in status["error"]


def test_firmware_second_upgrade_is_refused_while_running(client, office):
    manager = client.app_.state.manager
    manager.upgrade_settle = 1.0
    assert (
        client.post(f"{D}/firmware?confirm={MAC}", content=_image(), headers=BIN).status_code == 202
    )
    again = client.post(f"{D}/firmware?confirm={MAC}", content=_image(), headers=BIN)
    assert again.status_code == 422 and "already running" in again.json()["message"]


def test_firmware_never_comes_back(client, office):
    manager = client.app_.state.manager
    manager.upgrade_settle, manager.upgrade_poll, manager.upgrade_wait = 0.05, 0.1, 0.5
    manager.transport.tftp_timeout, manager.transport.timeout, manager.transport.retries = (
        0.3,
        0.1,
        0,
    )
    client.sim.switches[0].reboot_seconds = 30
    client.post(f"{D}/firmware?confirm={MAC}", content=_image(), headers=BIN)
    status = _wait_upgrade(client)
    assert status["state"] == "failed" and "did not come back" in status["error"]


def test_firmware_too_large(client, office):
    from switchyard import manager as mgr

    assert client.post(
        f"{D}/firmware/check",
        content=b"x" * 10,
        headers={**BIN, "content-length": str(mgr.MAX_FIRMWARE_BYTES + 1)},
    ).status_code in (413, 422, 400)


def test_labels_are_stored_shown_and_backed_up(client, office):
    r = client.put(
        f"{D}/labels",
        json={"ports": {"1": " NAS ", "2": "", "3": "x" * 50}, "vlans": {"10": "IoT"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["port_labels"] == {"1": "NAS", "3": "x" * 30} and body["vlan_names"] == {
        "10": "IoT"
    }
    assert client.get(f"{D}/state").json()["device"]["port_labels"]["1"] == "NAS"
    assert client.get("/api/devices").json()["devices"][0]["vlan_names"] == {"10": "IoT"}
    for bad in ({"ports": {"0": "x"}}, {"ports": {"99": "x"}}, {"vlans": {"5000": "x"}}):
        assert client.put(f"{D}/labels", json=bad).status_code == 422
    assert client.put(f"{D}/labels", json={"ports": {"x": "y"}}).status_code == 422
    data = client.get(f"{D}/backup").json()
    assert data["config"]["labels"]["ports"]["1"] == "NAS"
    client.put(f"{D}/labels", json={})
    assert client.get(f"{D}").json()["port_labels"] == {}
    r = client.post(f"{D}/restore", json={"backup": data})
    assert r.json()["ok"] and client.get(f"{D}").json()["port_labels"]["1"] == "NAS"
    assert client.put("/api/devices/aa:bb:cc:dd:ee:ff/labels", json={}).status_code == 404


def test_viewer_login_is_read_only(tmp_path, sim):
    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_AUTH_VIEWER_PASSWORD="viewer-pass-1")
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as admin:
        admin.post("/api/login", json={"password": PASSWORD})
        admin.post("/api/discovery/scan")
        admin.post("/api/devices", json={"mac": MAC, "password": "password"})
        assert admin.get("/api/session").json()["role"] == "admin"
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as v:
        assert v.post("/api/login", json={"password": "viewer-pass-1"}).json()["role"] == "viewer"
        assert v.get("/api/session").json()["role"] == "viewer"
        assert v.get("/api/devices").status_code == 200
        assert v.get(f"{D}/state").status_code == 200
        assert v.get(f"{D}/backup").status_code == 200
        assert v.post("/api/discovery/scan").status_code == 200
        diff = v.post(f"{D}/diff", json={"backup": v.get(f"{D}/backup").json()})
        assert diff.status_code == 200
        for method, path, body in [
            ("PUT", f"{D}/loop-detection", {"enabled": True}),
            ("POST", f"{D}/reboot", {"confirm": MAC}),
            ("POST", f"{D}/backups", {}),
            ("PATCH", D, {"label": "x"}),
            ("DELETE", D, None),
            ("POST", "/api/devices", {"mac": MAC}),
            ("POST", f"{D}/login", {"password": "password"}),
            ("POST", "/api/password", {"current": "a", "new": "b" * 9}),
            ("POST", f"{D}/firmware?confirm={MAC}", None),
        ]:
            r = v.request(method, path, json=body)
            assert r.status_code == 403 and r.json()["code"] == "read_only", (method, path)
        assert v.post("/api/logout").status_code == 200
    # the switch was not touched and a wrong password still fails
    assert sim.switches[0].loop_detection is False
    with TestClient(create_app(cfg.load(tmp_path, env=env))) as c:
        assert c.post("/api/login", json={"password": "nope"}).status_code == 401


def test_audit_log_records_changes_without_bodies(client, office):
    client.put(f"{D}/loop-detection", json={"enabled": True})
    client.put(f"{D}/network", json={"name": "secret-name-xyz"})
    client.put(f"{D}/qos", json={"ingress": {"2": 99}})  # refused: not logged
    client.get(f"{D}/state")  # reads are not logged
    entries = client.get("/api/audit").json()["entries"]
    routes = [(e["method"], e["route"]) for e in entries]
    assert ("PUT", "/api/devices/{mac}/network") in routes and (
        "PUT",
        "/api/devices/{mac}/loop-detection",
    ) in routes
    assert not any(r[1].endswith("/qos") for r in routes)
    assert all(e["role"] == "admin" and e["status"] < 400 for e in entries)
    assert entries[0]["route"].endswith("/loop-detection") or entries[0]["route"].endswith(
        "/network"
    )
    assert any(e["switch"] == MAC for e in entries)
    raw = (client.app_.state.config.data_dir / "audit.log").read_text()
    assert "secret-name-xyz" not in raw and "password" not in raw.lower()
    assert stat.S_IMODE((client.app_.state.config.data_dir / "audit.log").stat().st_mode) == 0o600
    assert client.get("/api/audit?limit=1").json()["entries"].__len__() == 1


def test_audit_tail_survives_a_torn_line(tmp_path):
    from switchyard.web.audit import Audit

    a = Audit(tmp_path / "a.log")
    assert a.tail() == []
    a.record("admin", "PUT", "/x", None, 200)
    with a.path.open("a") as f:
        f.write('{"time": 1, "ro')
    assert len(a.tail()) == 1


def test_every_write_route_is_closed_to_viewers(tmp_path, sim):
    """New routes must be refused for the read-only login unless they are listed as harmless."""
    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_AUTH_VIEWER_PASSWORD="viewer-pass-1")
    app = create_app(cfg.load(tmp_path, env=env))
    allowed = {
        "/api/login",
        "/api/logout",
        "/api/discovery/scan",
        "/api/devices/{mac}/diff",
        "/api/devices/{mac}/firmware/check",
    }
    with TestClient(app) as v:
        v.post("/api/login", json={"password": "viewer-pass-1"})
        for route, methods in app.openapi()["paths"].items():
            for method in methods:
                if method.upper() == "GET" or route in allowed:
                    continue
                r = v.request(
                    method.upper(), route.replace("{mac}", MAC).replace("{name}", "x"), json={}
                )
                assert r.status_code == 403, (method, route, r.status_code)


def test_diagnostics_scan_is_read_only_and_works_for_viewers(client, office):
    r = client.get(f"{D}/diagnostics")
    assert r.status_code == 200
    rows = {t["tag"]: t for t in r.json()["tags"]}
    assert rows[0x0001]["answered"] and rows[0x0001]["name"] == "model"
    assert rows[0x2000]["answered"] and rows[0x0C00]["values"]
    assert 0x000A not in rows and 0x0009 not in rows and 0x001A not in rows  # no credentials
    assert rows[0xA000]["answered"] is False  # unverified setting the simulator does not have
    assert r.json()["info"]["model"] == "GS108E"
    extra = client.get(f"{D}/diagnostics?extra=0x1234,5000").json()["tags"]
    assert {0x1234, 5000} <= {t["tag"] for t in extra}
    assert client.get(f"{D}/diagnostics?extra=0x10000").status_code == 422
    assert client.get(f"{D}/diagnostics?extra=zz").status_code == 422
    assert client.get("/api/devices/aa:bb:cc:dd:ee:ff/diagnostics").status_code == 404


async def test_scan_tags_marks_silent_firmware(transport):
    from switchyard.nsdp import SwitchClient, parse_mac
    from switchyard.sim import SimSwitch, Simulator

    sw = SimSwitch("02:00:00:00:00:44", unknown_tag="silent")
    sim = Simulator([sw])
    await sim.start()
    from switchyard.nsdp import Transport

    t = Transport(client_port=0, targets=[f"127.0.0.1:{sim.port}"])
    await t.start()
    try:
        c = SwitchClient(t, parse_mac(sw.mac), None, None)
        rows = {r["tag"]: r for r in await c.scan_tags([0x0001, 0xA000], timeout=0.15)}
        assert rows[0x0001]["answered"] is True
        assert rows[0xA000]["answered"] is False and rows[0xA000]["status"] is None
    finally:
        await t.stop()
        sim.stop()


def test_dns_rebinding_is_refused_when_login_is_disabled(tmp_path, sim):
    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_AUTH_DISABLED="true")
    app = create_app(cfg.load(tmp_path, env=env))
    with TestClient(app, base_url="http://evil.example") as c:
        r = c.post("/api/discovery/scan", headers={"Origin": "http://evil.example"})
        assert r.status_code == 421 and r.json()["code"] == "bad_host"
        assert c.get("/").status_code == 421
    for base in ("http://192.168.0.5:8080", "http://localhost:8080", "http://[::1]:8080"):
        with TestClient(app, base_url=base) as c:
            assert c.get("/api/devices").status_code == 200, base


def test_host_names_with_login_enabled(tmp_path, sim):
    env = make_env(sim.port, sim.tftp_port, SWITCHYARD_SERVER_ALLOWED_HOSTS="switchyard.lan")
    app = create_app(cfg.load(tmp_path, env=env))
    with TestClient(app, base_url="http://switchyard.lan") as c:
        assert c.get("/api/session").status_code == 200
    with TestClient(app, base_url="http://other.lan") as c:
        assert c.get("/api/session").status_code == 421
    from switchyard.web.app import host_allowed

    assert host_allowed("any.name", [], strict=False) is True  # login protects, names are free
    assert host_allowed("any.name", [], strict=True) is False
    assert host_allowed("10.0.0.1:8080", [], strict=True) is True
    assert host_allowed("[fe80::1]", [], strict=True) is True
    assert host_allowed("", [], strict=False) is False
    assert host_allowed(None, [], strict=False) is False


def test_cross_site_and_content_type_checks(client):
    r = client.post("/api/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert (
        client.post("/api/discovery/scan", headers={"Sec-Fetch-Site": "same-origin"}).status_code
        == 200
    )
    r = client.post(
        "/api/discovery/scan", content=b'{"a": 1}', headers={"Content-Type": "text/plain"}
    )
    assert r.status_code == 415 and r.json()["code"] == "bad_content_type"
    assert client.post("/api/discovery/scan").status_code == 200  # no body, no type needed


def test_request_bodies_are_limited(client, office):
    big = {"notes": "x" * (1024 * 1024 + 10)}
    assert client.patch(D, json=big).status_code == 413

    def chunks():
        for _ in range(20):
            yield b"x" * (1024 * 1024)

    r = client.post(f"{D}/firmware/check", content=chunks(), headers=BIN)  # chunked, no length
    assert r.status_code == 413
    r = client.post("/api/login", json={"password": "x" * 300})
    assert r.status_code == 422  # passwords are capped before hashing


def test_empty_identity_does_not_wipe_known_info(client, office):
    m = client.app.state.manager
    mac = office
    before = dict(m.store.get(mac)["info"])
    assert before["model"]
    m._note_seen({"mac": mac, "model": None, "name": "", "ip": None, "firmware": None})
    after = m.store.get(mac)["info"]
    assert after["model"] == before["model"] and after["ip"] == before["ip"]
    m._note_seen({"mac": mac, "name": "renamed"})
    assert m.store.get(mac)["info"]["name"] == "renamed"


def test_netgear_has_no_dscp_mode(client, office):
    r = client.put(f"/api/devices/{office}/qos", json={"mode": "dscp"})
    assert r.status_code == 422 and "dscp" in r.json()["message"]
