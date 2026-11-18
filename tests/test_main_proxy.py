from switchyard import __main__ as cli


def test_serve_passes_proxy_trust_to_uvicorn(tmp_path, monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("SWITCHYARD_SERVER_FORWARDED_ALLOW_IPS", "10.0.0.5,10.0.0.6")
    assert cli.main(["--data", str(tmp_path), "serve"]) == 0
    assert seen["forwarded_allow_ips"] == "10.0.0.5,10.0.0.6"
    monkeypatch.delenv("SWITCHYARD_SERVER_FORWARDED_ALLOW_IPS")
    assert cli.main(["--data", str(tmp_path / "x"), "serve"]) == 0
    assert seen["forwarded_allow_ips"] == "127.0.0.1"
