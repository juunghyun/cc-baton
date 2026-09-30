import json


def test_toggle_roundtrip_keeps_other_settings(home):
    r = home.run("toggle", "hib", "off")
    assert r.returncode == 0 and "hib     off" in r.stdout
    cfg = json.loads(home.config.read_text())
    assert cfg["hibernate"] == {"enabled": False, "idleMin": 90}
    assert cfg["1"] == {"kind": "personal"}  # 계정 설정은 그대로

    assert home.run("toggle", "is-on", "hib").returncode == 1
    home.run("toggle", "hib", "on")
    assert home.run("toggle", "is-on", "hib").returncode == 0


def test_toggle_refuses_to_overwrite_unreadable_config(home):
    home.config.write_text("{ broken")
    r = home.run("toggle", "swap", "off")
    assert r.returncode != 0
    assert home.config.read_text() == "{ broken"


def test_toggle_rejects_unknown_args(home):
    assert home.run("toggle", "bogus").returncode == 1


def test_update_off_skips_network(home):
    prefix = home.root / "npm"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/claude").write_text("#!/bin/sh\n")
    (prefix / "bin/claude").chmod(0o755)  # 없으면 cc-update 가 설치 중인 줄 알고 40초 기다린다
    home.fake("npm", "exit 1")
    home.fake("node", "exit 1")

    home.run("toggle", "update", "off")
    r = home.run("claude-update", NPM_PREFIX=prefix)
    assert r.returncode == 0
    assert not (home.root / "curl-called").exists()

    home.set_config(autoUpdate={"enabled": True})
    home.run("claude-update", NPM_PREFIX=prefix)
    assert (home.root / "curl-called").exists()  # 켜져 있으면 레지스트리 조회까지 간다


def test_hibernate_off_makes_tick_noop(home):
    home.run("toggle", "hib", "off")
    r = home.run("hib", "tick")
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_legacy_config_is_copied_to_new_location(home):
    legacy = home.root / ".claude/cc-accounts.json"
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_text(home.config.read_text())
    home.config.unlink()

    assert home.run("toggle", "swap", "off").returncode == 0
    assert json.loads(home.config.read_text())["onLimit"]["enabled"] is False
    assert json.loads(legacy.read_text())["onLimit"]["enabled"] is True  # 예전 파일은 건드리지 않는다


def test_update_cannot_be_turned_on_for_a_self_updating_claude(home):
    home.set_config(autoUpdate={"enabled": False})
    r = home.run("toggle", "update", "on")  # 가짜 HOME 의 PATH 에는 npm 설치본 claude 가 없다
    assert r.returncode == 1 and "npm" in r.stderr
    assert home.run("toggle", "is-on", "update").returncode == 1
    assert home.run("toggle", "autoswap", "off").returncode == 0  # HUD 용어로도 부른다
    assert home.run("toggle", "is-on", "swap").returncode == 1


def test_update_check_uses_the_cached_latest_version(home):
    prefix = home.root / "npm"
    pkg = prefix / "lib/node_modules/@anthropic-ai/claude-code"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{\n  "name": "@anthropic-ai/claude-code",\n  "version": "1.0.0"\n}\n')
    (prefix / "bin").mkdir()
    (prefix / "bin/claude").write_text("#!/bin/sh\n")
    (prefix / "bin/claude").chmod(0o755)
    home.fake("npm", "exit 1")
    home.fake("node", "exit 1")  # 버전은 node 없이 읽는다
    home.set_config(autoUpdate={"enabled": True})
    home.state.mkdir(parents=True, exist_ok=True)
    (home.state / "claude-latest").write_text("1.0.0\n")  # 30분 안에 확인한 최신 버전
    assert home.run("claude-update", NPM_PREFIX=prefix).returncode == 0
    assert not (home.root / "curl-called").exists()  # 레지스트리에 다시 묻지 않는다
