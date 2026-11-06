"""QA 검토에서 재현된 고장이 다시 생기지 않는지."""
import json
import subprocess
import time

from conftest import ROOT, TEAM_PROFILE, plain

EXE = "/opt/cc-baton/bin/cc-baton"
SID_A = "aaaaaaaa-0000-0000-0000-000000000000"


def test_swap_request_belongs_to_the_tab_that_made_it(home):
    r = home.run("swap", "hook", "prompt", CC_SWAP_LOOP=1, CC_BATON_TAB="111",
                 input=json.dumps({"prompt": "/swap team --yes", "session_id": SID_A}))
    assert r.returncode == 2 and (home.state / "request-111.json").exists()
    assert home.run("swap", "consume", CC_BATON_TAB="222").returncode == 1  # 다른 탭은 가져가지 못한다
    assert (home.state / "request-111.json").exists()


def test_broken_config_is_not_overwritten(home):
    broken = '{"1": {"group": "side"},}'
    home.config.write_text(broken)
    for args in (("install",), ("lang", "ko")):
        assert home.run(*args, CC_BATON_BIN=EXE).returncode == 1
        assert home.config.read_text() == broken


def test_lang_does_not_install_hooks(home):
    assert home.run("lang", "en", CC_BATON_BIN=EXE).returncode == 0
    assert not (home.root / ".claude/settings.json").exists()


def test_zshrc_without_our_end_line_is_left_alone(home):
    zshrc = home.root / ".zshrc"
    text = "# >>> cc-baton >>>\nsource x\nexport KEEP=1\nalias ll='ls -l'\n"
    zshrc.write_text(text)
    home.run("install", CC_BATON_BIN=EXE)
    home.run("uninstall", "--yes", "--keep-package", CC_BATON_BIN=EXE)
    assert zshrc.read_text() == text


def test_uninstall_without_a_terminal_keeps_the_package(home):
    assert home.run("uninstall", CC_BATON_BIN=EXE).returncode == 0
    calls = home.root / "uv-calls"
    assert not calls.exists() or "uninstall cc-baton" not in calls.read_text()


def test_hud_keeps_the_account_line_on_bad_input(home):
    home.set_config(hibernate={"enabled": True, "idleMin": "90"}, onLimit={"enabled": True, "minHeadroomPct": "15"})
    home.write_json(".claude-swap-backup/cache/usage.json", {"accounts": {"1": {"lastGood": {"five_hour": {"pct": "21"}},
                                                                                "fetchedAt": "2026"}}})
    for stdin in ('{"effort": "high", "model": "x"}', "null", "[]", '{"context_window": {"used_percentage": "12"}}'):
        out = plain(home.run("statusline", input=stdin).stdout)
        assert out.startswith("● personal") and "[statusline]" not in out, out


def test_claude_update_returns_at_once_without_npm(home):
    home.set_config(autoUpdate={"enabled": True})
    t = time.time()
    assert home.run("claude-update", NPM_PREFIX="").returncode == 0
    assert time.time() - t < 5 and not (home.root / "curl-called").exists()


def test_sleep_banner_names_the_session_not_the_registry_slug(home):
    parked = home.state / "hib/parked"
    parked.mkdir(parents=True)
    (parked / f"{SID_A}.json").write_text(json.dumps({"sid": SID_A, "cwd": str(home.root), "name": "workspace-93",
                                                       "profile": str(home.root / TEAM_PROFILE), "rss": 300,
                                                       "hibernatedAt": time.time()}))
    # 스왑을 거쳐 트랜스크립트가 기록된 프로필이 아닌 쪽에 있다. /rename 이름이 자동 제목보다 앞선다.
    home.transcript(".claude", SID_A, body="\n".join(json.dumps(d) for d in (
        {"type": "ai-title", "aiTitle": "auto title"},
        {"type": "agent-name", "agentName": "renamed"},
        {"type": "last-prompt", "lastPrompt": "fix the\nbanner"})) + "\n")
    out = home.run("hib", "banner", SID_A).stdout
    assert "\x1b]0;💤 renamed\x07" in out
    text = plain(out)
    assert "workspace-93" not in text and "renamed" in text and "fix the banner" in text


def test_parked_sessions_without_a_transcript_are_hidden_then_cleaned(home):
    gone = "bbbbbbbb-0000-0000-0000-000000000000"
    parked = home.state / "hib/parked"
    parked.mkdir(parents=True)
    for sid, name in ((SID_A, "workspace-1"), (gone, "workspace-2")):
        (parked / f"{sid}.json").write_text(json.dumps({"sid": sid, "cwd": str(home.root), "name": name,
                                                        "hibernatedAt": time.time()}))
    home.transcript(TEAM_PROFILE, SID_A, body=json.dumps({"type": "ai-title", "aiTitle": "결정 기준"}) + "\n")
    out = plain(home.run("hib", "list").stdout)
    assert "결정 기준" in out and "workspace-2" not in out
    home.run("hib", "clean")
    assert (parked / f"{SID_A}.json").exists() and not (parked / f"{gone}.json").exists()


def test_typing_without_sending_keeps_the_session_awake(home):
    home.fake("ps", 'echo "4242 ttys009 300000 /usr/local/bin/claude"')
    home.fake("memory_pressure", "exit 0")
    home.write_json(f"{TEAM_PROFILE}/sessions/4242.json", {
        "pid": 4242, "sessionId": SID_A, "status": "idle", "kind": "interactive", "name": "w",
        "cwd": str(home.root), "statusUpdatedAt": (time.time() - 3 * 3600) * 1000})
    home.transcript(TEAM_PROFILE, SID_A)
    assert "유휴 0분 <" not in plain(home.run("hib", "scan").stdout)
    draft = home.state / "drafts" / f"{SID_A}.txt"
    draft.parent.mkdir(parents=True)
    draft.write_text("쓰다 만 질문")  # mods/draft 가 입력창 편집마다 남기는 파일
    assert "유휴 0분 <" in plain(home.run("hib", "scan").stdout)


def test_woken_session_gets_its_name_back(home):
    pid = int(subprocess.check_output(["sh", "-c", "sleep 30 >/dev/null 2>&1 & echo $!"]))  # 재우기가 진짜로 죽일 대상
    # tty 는 없는 이름이어야 한다. 재우기가 /dev/<tty> 에 안내문을 쓰는데 가짜 HOME 으로는 /dev 가 격리되지 않는다.
    home.fake("ps", f'case "$*" in *ppid*) echo 1 ;; *) echo "{pid} ttyNONE 300000 /usr/local/bin/claude" ;; esac')
    home.write_json(f"{TEAM_PROFILE}/sessions/{pid}.json", {
        "pid": pid, "sessionId": SID_A, "status": "idle", "kind": "interactive", "name": "workspace-c4",
        "cwd": str(home.root), "statusUpdatedAt": time.time() * 1000})
    home.transcript(TEAM_PROFILE, SID_A)
    assert home.run("hib", "sleep", str(pid), "--force").returncode == 0
    assert home.run("hib", "wake", SID_A).returncode == 0  # 마커는 여기서 지워진다
    assert home.run("hib", "name", SID_A).stdout.strip() == "workspace-c4"


def test_name_comes_from_the_snapshot_unless_a_live_session_holds_it(home):
    home.write_json(".local/state/cc-baton/hib/live.json", {"sessions": {SID_A: {"name": "workspace-c4"}}})
    assert home.run("hib", "name", SID_A).stdout.strip() == "workspace-c4"
    home.fake("ps", 'echo "4242 ttyNONE 300000 /usr/local/bin/claude"')
    home.write_json(f"{TEAM_PROFILE}/sessions/4242.json", {"pid": 4242, "sessionId": "other", "name": "workspace-c4"})
    assert home.run("hib", "name", SID_A).stdout.strip() == ""


def test_baton_passes_the_old_name_only_when_resuming(home):
    log = home.root / "launched"
    fake = home.root / "fake-baton"
    fake.write_text(f"""#!/bin/sh
case "$1 $2" in
  "hib claim"|"toggle is-on") exit 1 ;;
  "hib name") [ "$3" = "{SID_A}" ] && echo workspace-c4 ;;
  "cswap run") echo "${{CLAUDE_CODE_SESSION_NAME-none}}" >> "{log}" ;;
esac
exit 0
""")
    fake.chmod(0o755)
    runs = f"baton 1 -- --resume {SID_A}; baton 1 -- --resume unknown; baton 1"
    subprocess.run(["/bin/zsh", "-fc", f'source "$0"; {runs}', str(ROOT / "cc_baton/data/baton.zsh")],
                   env=home.env(CC_BATON_BIN=fake), cwd=home.root, timeout=20, check=True)
    assert log.read_text().split() == ["workspace-c4", "none", "none"]


def test_failed_cswap_validation_keeps_conversation_history(home, monkeypatch):
    from claude_swap import session

    from cc_baton.cswap import keep_projects
    monkeypatch.setattr(session, "delete_macos_keychain_entry", lambda d: None)  # 진짜 키체인은 건드리지 않는다
    prof = home.root / TEAM_PROFILE
    t = home.transcript(TEAM_PROFILE, SID_A)
    (prof / ".credentials.json").write_text("{}")
    (prof / "todos").mkdir()
    keep_projects(session.SessionManager._cleanup_failed_session)(None, prof)
    assert [p.name for p in prof.iterdir()] == ["projects"] and t.read_text() == '{"type":"user"}\n'
