import json

import pytest
from playwright.sync_api import expect

from .conftest import PASSWORD, add_switch, api

pytestmark = pytest.mark.e2e

MAC = "00:26:f2:aa:10:01"
GOTO = "{url}/#/switch/{mac}/{tab}"


def open_tab(page, tab, mac=MAC):
    page.goto(GOTO.format(url=page.server.url, mac=mac, tab=tab))
    page.reload()


def test_login_errors_and_logout(page, server):
    page.set_default_timeout(15000)
    page.goto(server.url)
    page.get_by_test_id("login-password").fill("wrong")
    page.get_by_test_id("login-submit").click()
    expect(page.get_by_test_id("login-error")).to_contain_text("Wrong password")
    page.get_by_test_id("login-password").fill(PASSWORD)
    page.get_by_test_id("login-submit").click()
    expect(page.get_by_test_id("demo-banner")).to_be_visible()
    expect(page.get_by_test_id("empty")).to_be_visible()
    page.get_by_test_id("logout").click()
    expect(page.get_by_test_id("login-submit")).to_be_visible()


def test_scan_add_and_remove(app):
    app.get_by_test_id("scan").click()
    rows = app.get_by_test_id("found-row")
    expect(rows).to_have_count(4)
    app.locator(f'[data-testid="found-row"][data-mac="{MAC}"] [data-testid="add-found"]').click()
    expect(app.get_by_test_id("switch-card")).to_have_count(1)
    expect(app.get_by_test_id("found-row")).to_have_count(3)
    app.get_by_test_id("switch-card").click()
    expect(app.get_by_test_id("switch-title")).to_have_text("office")
    app.get_by_test_id("tab-maintenance").click()
    app.get_by_test_id("meta-label").fill("Desk")
    app.get_by_test_id("meta-save").click()
    expect(app.get_by_test_id("switch-title")).to_have_text("Desk")
    app.get_by_test_id("remove-switch").click()
    app.get_by_test_id("confirm-ok").click()
    expect(app.get_by_test_id("empty")).to_be_visible()


def test_add_by_ip(app):
    app.get_by_test_id("add-by-ip").click()
    app.get_by_test_id("add-ip").fill("not an ip")
    app.get_by_test_id("add-ip-ok").click()
    expect(app.get_by_test_id("modal")).to_contain_text("valid IPv4")
    app.get_by_test_id("add-ip").fill("127.0.0.1")
    app.get_by_test_id("add-ip-ok").click()
    expect(app.get_by_test_id("switch-card")).to_have_count(1)


def test_overview_shows_ports_and_identity(app):
    add_switch(app, MAC)
    open_tab(app, "overview")
    expect(app.get_by_test_id("port-panel")).to_be_visible()
    expect(app.get_by_test_id("port-1")).to_have_class(__import__("re").compile("s1000"))
    expect(app.get_by_test_id("port-3")).to_have_class(__import__("re").compile("down"))
    expect(app.get_by_test_id("identity")).to_contain_text("GS108E")


def test_wrong_then_right_switch_password_retries_the_action(app):
    add_switch(app, MAC)
    open_tab(app, "features")
    app.get_by_test_id("loop-enabled").check()
    app.get_by_test_id("loop-save").click()
    expect(app.get_by_test_id("modal")).to_be_visible()
    app.get_by_test_id("switch-password").fill("wrong")
    app.get_by_test_id("switch-login-ok").click()
    expect(app.get_by_test_id("switch-login-error")).to_contain_text("Incorrect password")
    app.get_by_test_id("switch-password").fill("password")
    app.get_by_test_id("switch-login-ok").click()
    expect(app.locator(".toast").last).to_contain_text("Loop detection saved")
    state = api(app, "GET", f"/devices/{MAC}/state").json()
    assert state["loop_detection"] is True
    assert api(app, "GET", f"/devices/{MAC}").json()["remembered"] is True


def test_cancelling_the_password_dialog_changes_nothing(app):
    add_switch(app, MAC)
    open_tab(app, "features")
    app.get_by_test_id("loop-enabled").check()
    app.get_by_test_id("loop-save").click()
    app.get_by_role("button", name="Cancel").click()
    assert api(app, "GET", f"/devices/{MAC}/state").json()["loop_detection"] is False


def test_vlan_edit_validate_apply(app):
    add_switch(app, MAC, "password")
    open_tab(app, "vlan")
    app.get_by_test_id("vlan-mode").select_option("dot1q_advanced")
    app.get_by_test_id("vlan-mode-apply").click()
    app.get_by_test_id("confirm-ok").click()
    expect(app.get_by_test_id("vlan-matrix")).to_be_visible()
    app.get_by_test_id("new-vlan-id").fill("20")
    app.get_by_test_id("add-vlan").click()
    for port in (5, 6, 7, 8):
        cell = app.get_by_test_id(f"cell-20-{port}")
        cell.click()
        expect(cell).to_have_attribute("data-state", "U")
    app.get_by_test_id("cell-20-1").click()
    app.get_by_test_id("cell-20-1").click()  # U then T
    expect(app.get_by_test_id("cell-20-1")).to_have_attribute("data-state", "T")
    expect(app.get_by_test_id("dirty")).to_be_visible()
    # ports 5-8 are still in VLAN 1 and use it as PVID: fine; remove port 5 from VLAN 1
    app.get_by_test_id("cell-1-5").click()
    app.get_by_test_id("cell-1-5").click()
    expect(app.get_by_test_id("cell-1-5")).to_have_attribute("data-state", "-")
    expect(app.get_by_test_id("vlan-errors")).to_contain_text("Port 5 must be a member of VLAN 1")
    expect(app.get_by_test_id("vlan-apply")).to_be_disabled()
    app.get_by_test_id("pvid-5").select_option("20")
    expect(app.get_by_test_id("vlan-errors")).to_have_text("")
    for port in (6, 7, 8):
        app.get_by_test_id(f"pvid-{port}").select_option("20")
    expect(app.get_by_test_id("vlan-apply")).to_be_enabled()
    app.get_by_test_id("vlan-apply").click()
    expect(app.locator(".toast").last).to_contain_text("VLANs saved")
    vlan = api(app, "GET", f"/devices/{MAC}/state").json()["vlan"]
    by_id = {v["id"]: v for v in vlan["dot1q"]}
    assert by_id[20]["members"] == [1, 5, 6, 7, 8] and by_id[20]["tagged"] == [1]
    assert 5 not in by_id[1]["members"] and vlan["pvid"]["5"] == 20


def test_vlan_discard_and_remove(app):
    add_switch(app, MAC, "password")
    api(app, "PUT", f"/devices/{MAC}/vlan/mode", {"mode": "dot1q_advanced"})
    open_tab(app, "vlan")
    app.get_by_test_id("new-vlan-id").fill("30")
    app.get_by_test_id("add-vlan").click()
    expect(app.get_by_test_id("vlan-row-30")).to_be_visible()
    app.get_by_test_id("vlan-discard").click()
    expect(app.get_by_test_id("vlan-row-30")).to_have_count(0)
    expect(app.get_by_test_id("dirty")).to_be_hidden()


def test_vlan_mode_change_asks_for_confirmation(app):
    add_switch(app, MAC, "password")
    open_tab(app, "vlan")
    app.get_by_test_id("vlan-mode").select_option("port_basic")
    app.get_by_test_id("vlan-mode-apply").click()
    expect(app.get_by_test_id("modal")).to_contain_text("erases all current VLAN settings")
    app.get_by_role("button", name="Cancel").click()
    assert api(app, "GET", f"/devices/{MAC}/state").json()["vlan"]["mode"] == "disabled"


def test_qos_settings(app):
    add_switch(app, MAC, "password")
    open_tab(app, "qos")
    app.get_by_test_id("qos-mode").select_option("port")
    app.get_by_test_id("prio-1").select_option("high")
    app.get_by_test_id("ingress-2").select_option("5")
    app.get_by_test_id("storm-enabled").check()
    app.get_by_test_id("storm-3").select_option("4")
    app.get_by_test_id("qos-save").click()
    app.get_by_test_id("confirm-ok").click()
    expect(app.locator(".toast").last).to_contain_text("QoS settings saved")
    q = api(app, "GET", f"/devices/{MAC}/state").json()["qos"]
    assert q["mode"] == "port" and q["priorities"]["1"] == "high" and q["ingress"]["2"] == 5
    assert q["storm_control"] is True and q["storm_rates"]["3"] == 4


def test_network_validation(app):
    add_switch(app, MAC, "password")
    open_tab(app, "network")
    app.get_by_test_id("net-dhcp").uncheck()
    app.get_by_test_id("net-gateway").fill("10.9.9.9")
    app.get_by_test_id("net-save").click()
    expect(app.get_by_test_id("net-errors")).to_contain_text("same subnet")
    app.get_by_test_id("net-gateway").fill("192.168.0.1")
    app.get_by_test_id("net-name").fill("lab-switch")
    app.get_by_test_id("net-save").click()
    expect(app.locator(".toast").last).to_contain_text("Settings saved")
    assert api(app, "GET", f"/devices/{MAC}/state").json()["info"]["name"] == "lab-switch"


def test_mirror_igmp_cable(app):
    add_switch(app, MAC, "password")
    open_tab(app, "features")
    app.get_by_test_id("mirror-dest").select_option("1")
    app.get_by_test_id("mirror-src-1").check()
    app.get_by_test_id("mirror-save").click()
    expect(app.locator(".toast").last).to_contain_text("cannot also be a source")
    app.get_by_test_id("mirror-src-1").uncheck()
    app.get_by_test_id("mirror-src-2").check()
    app.get_by_test_id("mirror-save").click()
    expect(app.locator(".toast").last).to_contain_text("Mirroring saved")
    app.get_by_test_id("igmp-enabled").check()
    app.get_by_test_id("igmp-save").click()
    expect(app.locator(".toast").last).to_contain_text("IGMP snooping saved")
    open_tab(app, "cable")
    app.get_by_test_id("cable-port-3").check()
    app.get_by_test_id("cable-run").click()
    expect(app.get_by_test_id("cable-3")).to_contain_text("Open cable")


def test_unsupported_sections_are_explained(app):
    add_switch(app, "e0:91:f5:cc:30:03", "password")
    open_tab(app, "features", "e0:91:f5:cc:30:03")
    expect(app.get_by_test_id("unsupported")).to_have_count(1)
    expect(app.get_by_test_id("unsupported")).to_contain_text("Loop detection")


def test_backup_and_restore(app, tmp_path):
    add_switch(app, MAC, "password")
    api(app, "PUT", f"/devices/{MAC}/vlan/mode", {"mode": "dot1q_advanced"})
    api(
        app,
        "PUT",
        f"/devices/{MAC}/vlan/dot1q",
        {
            "vlans": [{"id": 1, "members": [1, 2, 3, 4]}, {"id": 20, "members": [5, 6, 7, 8]}],
            "pvid": {"5": 20, "6": 20, "7": 20, "8": 20},
        },
    )
    open_tab(app, "maintenance")
    with app.expect_download() as dl:
        app.get_by_test_id("backup").click()
    saved = tmp_path / "backup.json"
    dl.value.save_as(saved)
    assert json.loads(saved.read_text())["format"] == "switchyard-backup"
    api(app, "POST", f"/devices/{MAC}/factory-reset", {"confirm": MAC})
    import time

    time.sleep(3.5)
    app.reload()
    app.get_by_test_id("restore-file").set_input_files(str(saved))
    app.get_by_test_id("restore").click()
    app.get_by_test_id("confirm-ok").click()
    # the stored password was dropped by the reset: the dialog asks again
    app.get_by_test_id("switch-password").fill("password")
    app.get_by_test_id("switch-login-ok").click()
    expect(app.get_by_test_id("restore-steps")).to_contain_text("802.1Q VLANs")
    vlan = api(app, "GET", f"/devices/{MAC}/state").json()["vlan"]
    assert {v["id"] for v in vlan["dot1q"]} == {1, 20}


def test_reboot_requires_typing_the_mac_and_recovers(app):
    add_switch(app, MAC, "password")
    open_tab(app, "maintenance")
    app.get_by_test_id("do-reboot").click()
    app.get_by_test_id("confirm-input").fill("nope")
    app.get_by_test_id("confirm-ok").click()
    expect(app.get_by_test_id("confirm-input")).to_have_attribute("aria-invalid", "true")
    app.get_by_test_id("confirm-input").fill(MAC)
    app.get_by_test_id("confirm-ok").click()
    expect(app.locator(".toast").first).to_contain_text("restarting")
    open_tab(app, "overview")
    for _ in range(6):
        if app.get_by_test_id("port-panel").count():
            break
        retry = app.get_by_test_id("retry")
        if retry.count():
            retry.click()
        app.wait_for_timeout(1500)
    expect(app.get_by_test_id("port-panel")).to_be_visible()


def test_names_from_the_network_are_rendered_as_text(app):
    add_switch(app, MAC, "password", label="")
    payload = '<img src=x onerror="window.__pwned=1">'
    api(app, "PUT", f"/devices/{MAC}/network", {"name": "x"})
    api(app, "PATCH", f"/devices/{MAC}", {"label": payload, "notes": payload})
    app.goto(app.server.url + "/#/")
    app.reload()
    expect(app.get_by_test_id("switch-card")).to_contain_text("<img src=x")
    assert app.evaluate("window.__pwned") is None
    assert app.locator("img[src='x']").count() == 0


def test_activity_log_shows_traffic_without_passwords(app):
    add_switch(app, MAC, "password")
    api(app, "PUT", f"/devices/{MAC}/loop-detection", {"enabled": True})
    app.get_by_test_id("nav-log").click()
    expect(app.get_by_test_id("log-row").first).to_be_visible()
    app.get_by_test_id("log-filter").select_option(MAC)
    app.get_by_test_id("log-row").first.click()
    text = app.get_by_test_id("log-body").inner_text()
    assert "loop_detection" in text or "read_" in text or "write" in text
    from switchyard.nsdp.auth import xor_password

    assert xor_password(b"password").hex() not in text.replace(" ", "")


def test_settings_and_theme(app):
    app.get_by_test_id("nav-settings").click()
    expect(app.get_by_test_id("system")).to_contain_text("Version")
    app.get_by_test_id("theme").click()
    expect(app.locator("html")).to_have_attribute("data-theme", "light")
    app.get_by_test_id("theme").click()
    expect(app.locator("html")).to_have_attribute("data-theme", "dark")


def test_session_expiry_returns_to_login(app):
    api(app, "POST", "/logout", {})
    app.get_by_test_id("scan").click()
    expect(app.get_by_test_id("login-submit")).to_be_visible()
    expect(app.get_by_test_id("login-error")).to_contain_text("session ended")


def test_phone_layout_has_no_horizontal_scroll(app):
    add_switch(app, MAC, "password")
    app.set_viewport_size({"width": 390, "height": 844})
    app.goto(app.server.url + "/#/")
    app.reload()
    expect(app.get_by_test_id("switch-card")).to_be_visible()
    assert app.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
