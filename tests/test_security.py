"""보안 검토에서 나온 공격 경로가 다시 열리지 않는지."""
import json
import os
import stat
import subprocess
import time

from conftest import TEAM_PROFILE

EXE = "/opt/cc-baton/bin/cc-baton"
SID = "11111111-2222-3333-4444-555555555555"


def test_statusline_does_not_run_repo_fsmonitor(home):
    repo = home.root / "evil"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run([*git, "add", "f.txt"], check=True)
    (repo / "f.txt").write_text("y")
    pwned = home.root / "PWNED"  # 설정은 마지막에 (테스트 자신의 git 호출이 먼저 실행하지 않게)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", f"touch {pwned}; false"], check=True)
    data = {"model": {"display_name": "Opus 5.5"}, "workspace": {"current_dir": str(repo)}}
    home.run("statusline", input=json.dumps(data))
    assert not pwned.exists()


def test_install_keeps_settings_file_private(home):
    settings = home.root / ".claude/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-test"}}))
    settings.chmod(0o600)
    assert home.run("install", CC_BATON_BIN=EXE).returncode == 0
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600


def test_foreign_entries_named_cc_baton_are_not_ours(home):
    foreign_status = {"type": "command", "command": "~/dotfiles/cc-baton-fork/my-status.sh"}
    foreign_hook = {"type": "command", "command": "afplay ~/sounds/cc-baton-done.aiff"}
    home.write_json(".claude/settings.json", {"statusLine": foreign_status, "hooks": {"Stop": [{"hooks": [foreign_hook]}]}})
    assert home.run("install", CC_BATON_BIN=EXE).returncode == 0
    data = json.loads((home.root / ".claude/settings.json").read_text())
    assert data["statusLine"] == foreign_status  # 남의 statusline 을 말없이 바꾸지 않는다
    assert home.run("uninstall", "--yes", "--keep-package", CC_BATON_BIN=EXE).returncode == 0
    data = json.loads((home.root / ".claude/settings.json").read_text())
    assert data["statusLine"] == foreign_status and data["hooks"]["Stop"] == [{"hooks": [foreign_hook]}]


def test_legacy_migration_does_not_eat_a_symlinked_new_dir(home):
    old = home.root / ".local/state/cc-swap"
    old.mkdir(parents=True)
    (old / "important.json").write_text("keep")
    home.state.parent.mkdir(parents=True, exist_ok=True)
    home.state.symlink_to(old)  # 새 위치가 옛 폴더를 가리키는 링크
    assert home.run("install", CC_BATON_BIN=EXE).returncode == 0
    assert (old / "important.json").read_text() == "keep"


def test_swap_takes_arguments_from_the_first_line_only(home):
    prompt = "/swap 2\nsome pasted text with --yes in it"
    r = home.run("swap", "hook", "prompt", CC_SWAP_LOOP=1, input=json.dumps({"prompt": prompt, "session_id": SID}))
    assert r.returncode == 0  # 여러 줄이면 /swap 명령으로 보지 않는다
    assert not (home.state / "request.json").exists()


def test_bad_session_ids_never_reach_paths(home):
    (home.root / ".claude/projects/-p").mkdir(parents=True)
    (home.root / ".claude/projects/-p/secret-convo.jsonl").write_text("secret")
    r = home.run("swap", "handoff", "2", "--sid", "*", "--src-profile", str(home.root / ".claude"))
    assert r.returncode != 0
    assert not list((home.root / TEAM_PROFILE).rglob("*.jsonl"))


def test_consume_ignores_profiles_written_into_the_marker(home):
    home.set_config(**{"2": {"group": "personal"}})
    evil = home.root / "evil-profile"
    home.state.mkdir(parents=True, exist_ok=True)
    (home.state / "request.json").write_text(json.dumps({
        "targetNum": "2", "targetLabel": "team", "sid": SID, "srcProfile": str(home.root / "outside"),
        "dstProfile": str(evil), "requestedAt": time.time()}))
    r = home.run("swap", "consume")
    assert r.returncode == 0 and r.stdout.strip() == "2"  # 출발 프로필을 못 믿으니 resume 없이
    assert not evil.exists()


def test_unknown_groups_count_as_crossing(home):
    home.set_config(**{"1": {}, "2": {}})
    r = home.run("swap", "request", "2", "--sid", SID, CC_SWAP_LOOP=1)
    assert r.returncode == 2


def test_hib_resume_passes_session_as_arguments_not_shell_code(home):
    parked = home.state / "hib/parked"
    parked.mkdir(parents=True)
    (parked / f"{SID}.json").write_text(json.dumps({"sid": SID, "account": "2", "cwd": str(home.root), "name": "x",
                                                     "hibernatedAt": time.time()}))
    log = home.root / "zsh-args"
    home.fake("zsh", f'printf "%s\\n" "$@" > "{log}"')
    home.run("hib", "resume", "1")
    assert log.read_text().splitlines() == ["-ic", 'baton "$1" -- --resume "$2"', "zsh", "2", SID]


def test_restore_script_cannot_smuggle_commands(home):
    parked = home.state / "hib/parked"
    parked.mkdir(parents=True)
    (parked / f"{SID}.json").write_text(json.dumps({"sid": SID, "account": "2", "cwd": "/tmp/p\ntouch PWNED\n#",
                                                     "name": "n", "hibernatedAt": time.time()}))
    out = home.root / "restore.sh"
    home.run("hib", "restore", "--script", str(out))
    live = [ln for ln in out.read_text().splitlines() if ln and not ln.startswith("#")]
    assert live == []


def test_permission_bypass_is_off_by_default(home):
    assert home.run("toggle", "is-on", "bypass").returncode == 1
