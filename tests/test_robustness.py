"""QA 검토에서 재현된 고장이 다시 생기지 않는지."""
import json
import time

from conftest import TEAM_PROFILE, plain

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
