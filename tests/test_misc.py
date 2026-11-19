import asyncio
import stat
import sys

import pytest

from switchyard import __main__ as cli
from switchyard import config as cfg
from switchyard.store import Store


def test_default_file_is_commented_and_parses(tmp_path):
    c = cfg.load(tmp_path, env={})
    text = c.path.read_text()
    assert text.count("\n#") > 15 and "SWITCHYARD_<SECTION>_<KEY>" in text
    assert (
        c["server.port"] == 8080 and c["nsdp.client_port"] == 63321 and c["nsdp.interfaces"] == []
    )
    assert cfg.load(tmp_path, env={})["server.port"] == 8080  # existing file is kept


def test_env_overrides_every_type(tmp_path):
    c = cfg.load(
        tmp_path,
        env={
            "SWITCHYARD_SERVER_PORT": "9000",
            "SWITCHYARD_AUTH_DISABLED": "yes",
            "SWITCHYARD_NSDP_DISCOVERY_SECONDS": "0.5",
            "SWITCHYARD_NSDP_INTERFACES": "eth0, eth1",
            "SWITCHYARD_LOG_LEVEL": "debug",
        },
    )
    assert c["server.port"] == 9000 and c["auth.disabled"] is True
    assert c["nsdp.discovery_seconds"] == 0.5 and c["nsdp.interfaces"] == ["eth0", "eth1"]
    assert c["log.level"] == "debug"
    assert cfg.load(tmp_path, env={"SWITCHYARD_AUTH_DISABLED": "no"})["auth.disabled"] is False


def test_file_values_and_type_errors(tmp_path):
    (tmp_path / "switchyard.toml").write_text(
        "[server]\nport = 1234\n[nsdp]\ndiscovery_seconds = 3\n"
    )
    c = cfg.load(tmp_path, env={})
    assert c["server.port"] == 1234 and c["nsdp.discovery_seconds"] == 3.0
    (tmp_path / "switchyard.toml").write_text('[server]\nport = "x"\n')
    with pytest.raises(ValueError, match="must be of type int"):
        cfg.load(tmp_path, env={})


def test_data_dir_from_env(tmp_path):
    c = cfg.load(None, env={"SWITCHYARD_DATA_DIR": str(tmp_path / "sub")})
    assert c.data_dir.is_dir()


def test_store_persists_atomically_with_private_mode(tmp_path):
    s = Store(tmp_path / "d.json")
    s.add("aa:bb:cc:dd:ee:ff", label="x", password="secret")
    assert stat.S_IMODE((tmp_path / "d.json").stat().st_mode) == 0o600
    assert not (tmp_path / "d.tmp").exists()
    s2 = Store(tmp_path / "d.json")
    assert s2.get("aa:bb:cc:dd:ee:ff")["label"] == "x"
    s2.update("aa:bb:cc:dd:ee:ff", label="y")
    s2.remove("aa:bb:cc:dd:ee:ff")
    s2.remove("aa:bb:cc:dd:ee:ff")
    assert Store(tmp_path / "d.json").devices == {}


def test_cli_discover(sim, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SWITCHYARD_NSDP_TARGETS", f"127.0.0.1:{sim.port}")
    monkeypatch.setenv("SWITCHYARD_NSDP_CLIENT_PORT", "0")
    monkeypatch.setenv("SWITCHYARD_NSDP_DISCOVERY_SECONDS", "0.3")
    assert cli.main(["--data", str(tmp_path), "discover"]) == 0
    out = capsys.readouterr().out
    assert "GS108E" in out and "00:26:f2:aa:10:01" in out
    monkeypatch.setenv("SWITCHYARD_NSDP_TARGETS", "127.0.0.1:9")
    assert cli.main(["--data", str(tmp_path), "discover"]) == 1
    assert "No switch answered" in capsys.readouterr().err


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0 and "0." in capsys.readouterr().out


def test_cli_serve_calls_uvicorn(tmp_path, monkeypatch):
    seen = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    assert cli.main(["--data", str(tmp_path), "serve", "--demo"]) == 0
    assert seen["port"] == 8080 and seen["app"].state.config["demo.enabled"] is True
    seen.clear()
    assert cli.main(["--data", str(tmp_path)]) == 0  # default command
    assert "app" in seen


def test_cli_sim_runs_until_cancelled(monkeypatch):
    async def go():
        task = asyncio.create_task(cli._sim(type("A", (), {"host": "127.0.0.1", "port": 0})()))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    monkeypatch.setattr(cli, "_sim", lambda a: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(
        asyncio,
        "run",
        lambda coro: (
            coro.close() if hasattr(coro, "close") else None,
            (_ for _ in ()).throw(KeyboardInterrupt()),
        )[1],
    )
    assert cli.main(["sim"]) == 0


def test_main_module_importable():
    assert sys.modules["switchyard.__main__"].main


def test_cli_reset_password(tmp_path, capsys):
    import json

    assert cli.main(["--data", str(tmp_path), "reset-password"]) == 0
    err = capsys.readouterr().err
    assert "Web interface password:" in err and "Restart Switchyard" in err
    password = err.split("Web interface password:")[1].split()[0]
    stored = json.loads((tmp_path / "secrets.json").read_text())
    from switchyard.web.auth import _hash

    assert _hash(password, bytes.fromhex(stored["salt"])) == stored["hash"]


def test_cli_reset_password_refuses_a_configured_password(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SWITCHYARD_AUTH_PASSWORD", "configured-1")
    assert cli.main(["--data", str(tmp_path), "reset-password"]) == 1
    assert "change it there" in capsys.readouterr().err
