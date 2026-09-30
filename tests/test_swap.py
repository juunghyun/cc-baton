import json

from conftest import TEAM_PROFILE

SID = "11111111-2222-3333-4444-555555555555"
BODY = '{"type":"user","message":"hello"}\n{"type":"assistant","message":"hi"}\n'


def marker(home):
    return home.state / "request.json"


def test_handoff_copies_transcript_verbatim(home):
    home.transcript(".claude", SID, body=BODY)
    r = home.run("swap", "handoff", "2", "--sid", SID, "--src-profile", str(home.root / ".claude"))
    assert r.returncode == 0, r.stderr
    dst = home.root / TEAM_PROFILE / "projects/-tmp-proj" / f"{SID}.jsonl"
    assert dst.read_text() == BODY


def test_request_then_consume_moves_conversation(home):
    home.transcript(".claude", SID, body=BODY)
    home.set_config(**{"2": {"kind": "personal"}})  # 경계 교차 없는 스왑
    r = home.run("swap", "request", "2", "--sid", SID, CC_SWAP_LOOP=1)
    assert r.returncode == 0, r.stdout + r.stderr
    assert marker(home).exists()

    r = home.run("swap", "consume")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"2\t{SID}"
    assert not marker(home).exists()
    assert (home.root / TEAM_PROFILE / "projects/-tmp-proj" / f"{SID}.jsonl").read_text() == BODY


def test_crossing_requires_approval(home):
    r = home.run("swap", "request", "2", "--sid", SID, CC_SWAP_LOOP=1)
    assert r.returncode == 2
    assert not marker(home).exists()
    assert home.run("swap", "request", "2", "--sid", SID, "--yes", CC_SWAP_LOOP=1).returncode == 0


def test_request_outside_wrapper_leaves_no_marker(home):
    r = home.run("swap", "request", "2", "--sid", SID, "--yes")
    assert r.returncode == 3
    assert not marker(home).exists()


def test_prompt_hook_passes_normal_prompts_and_blocks_swap(home):
    r = home.run("swap", "hook", "prompt", input=json.dumps({"prompt": "hello", "session_id": SID}))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")

    r = home.run("swap", "hook", "prompt", CC_SWAP_LOOP=1,
                 input=json.dumps({"prompt": "/swap 2 --yes", "session_id": SID}))
    assert r.returncode == 2  # 프롬프트 차단, 모델 호출 없음
    assert json.loads(marker(home).read_text())["sid"] == SID


def limit_hook(home, **env):
    data = {"error": "rate_limit", "session_id": SID, "last_assistant_message": "You've hit your limit"}
    r = home.run("swap", "hook", "limit", input=json.dumps(data), CC_SWAP_LOOP=1, **env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["systemMessage"] if r.stdout.strip() else ""


def test_limit_hook_schedules_swap_to_account_with_headroom(home):
    home.set_config(onLimit={"enabled": True, "approveCrossing": True, "minHeadroomPct": 15})
    msg = limit_hook(home)
    assert "예약됨" in msg
    assert json.loads(marker(home).read_text())["targetNum"] == "2"


def test_limit_hook_without_target_or_disabled(home):
    home.set_config(onLimit={"enabled": True, "approveCrossing": True, "minHeadroomPct": 15})
    home.set_usage("2", 100)
    assert "여유가 있는 다른 계정이 없다" in limit_hook(home)
    assert not marker(home).exists()

    home.set_usage("2", 40)
    home.set_config(onLimit={"enabled": False})
    assert limit_hook(home) == ""
    assert not marker(home).exists()


def test_limit_hook_crossing_needs_approval(home):
    msg = limit_hook(home)  # 기본 fixture: approveCrossing=False, 대상은 team
    assert "경계 교차 승인이 필요하다" in msg
    assert not marker(home).exists()


def test_crossing_is_any_group_change(home):
    home.set_config(**{"1": {"group": "side"}, "2": {"group": "side"}})
    assert home.run("swap", "request", "2", "--sid", SID, CC_SWAP_LOOP=1).returncode == 0
    home.set_config(**{"2": {"group": "client-a"}})
    assert home.run("swap", "request", "2", "--sid", SID, CC_SWAP_LOOP=1).returncode == 2


def test_handoff_copies_session_side_folder(home):
    src = home.transcript(".claude", SID, body=BODY)
    side = src.with_suffix("")
    (side / "tool-results").mkdir(parents=True)
    (side / "tool-results/big.txt").write_text("large tool output")
    (side / "subagents").mkdir()
    (side / "subagents/agent-1.jsonl").write_text("{}\n")
    r = home.run("swap", "handoff", "2", "--sid", SID, "--src-profile", str(home.root / ".claude"))
    assert r.returncode == 0, r.stderr
    dst = home.root / TEAM_PROFILE / "projects/-tmp-proj" / SID
    assert (dst / "tool-results/big.txt").read_text() == "large tool output"
    assert (dst / "subagents/agent-1.jsonl").exists()


def test_english_messages(home):
    r = home.run("swap", "request", "2", "--sid", SID, CC_SWAP_LOOP=1, CC_BATON_LANG="en")
    assert r.returncode == 2 and "crosses groups" in r.stdout
    home.set_config(onLimit={"enabled": True, "approveCrossing": True, "minHeadroomPct": 15})
    home.set_usage("2", 100)
    assert "No other account has room left" in limit_hook(home, CC_BATON_LANG="en")
