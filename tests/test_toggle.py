import json


def test_toggle_roundtrip_keeps_other_settings(home):
    r = home.run("cc-toggle", "hib", "off")
    assert r.returncode == 0 and "hib     off" in r.stdout
    cfg = json.loads(home.config.read_text())
    assert cfg["hibernate"] == {"enabled": False, "idleMin": 90}
    assert cfg["1"] == {"kind": "personal"}  # 계정 설정은 그대로

    assert home.run("cc-toggle", "is-on", "hib").returncode == 1
    home.run("cc-toggle", "hib", "on")
    assert home.run("cc-toggle", "is-on", "hib").returncode == 0


def test_toggle_refuses_to_overwrite_unreadable_config(home):
    home.config.write_text("{ broken")
    r = home.run("cc-toggle", "swap", "off")
    assert r.returncode != 0
    assert home.config.read_text() == "{ broken"


def test_toggle_rejects_unknown_args(home):
    assert home.run("cc-toggle", "bogus").returncode == 1


def test_update_off_skips_network(home):
    prefix = home.root / "npm"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/claude").write_text("#!/bin/sh\n")
    (prefix / "bin/claude").chmod(0o755)  # 없으면 cc-update 가 설치 중인 줄 알고 40초 기다린다

    home.run("cc-toggle", "update", "off")
    r = home.run("cc-update", NPM_PREFIX=prefix)
    assert r.returncode == 0
    assert not (home.root / "curl-called").exists()

    home.run("cc-toggle", "update", "on")
    home.run("cc-update", NPM_PREFIX=prefix)
    assert (home.root / "curl-called").exists()  # 켜져 있으면 레지스트리 조회까지 간다


def test_hibernate_off_makes_tick_noop(home):
    home.run("cc-toggle", "hib", "off")
    r = home.run("cc-hib", "tick")
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
