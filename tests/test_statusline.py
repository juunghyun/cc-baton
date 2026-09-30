import json
import subprocess
import time

import pytest

from conftest import TEAM_PROFILE, plain, width

SWITCH_ON, SWITCH_OFF, SWITCH_WARN = "\x1b[48;5;22m", "\x1b[48;5;244m", "\x1b[48;2;90;51;0m"


def render(home, effort=None, columns=140, **env):
    data = {"model": {"display_name": "Opus 5.5 (1M context)"},
            "context_window": {"used_percentage": 12},
            "workspace": {"current_dir": str(home.root)}}
    if effort:
        data["effort"] = {"level": effort}
    r = home.run("statusline", input=json.dumps(data), COLUMNS=columns, **env)
    assert r.returncode == 0, r.stderr
    return r.stdout.rstrip("\n").split("\n")


@pytest.mark.parametrize("level,cells,lv", [
    ("low", 1, "Lv.1"), ("medium", 2, "Lv.30"), ("high", 3, "Lv.60"),
    ("xhigh", 4, "Lv.100"), ("max", 5, "Lv.999"),
])
def test_effort_meter_and_level_chip(home, level, cells, lv):
    line1 = plain(render(home, level)[0])
    assert "Opus 5.5[1M] " + "▰" * cells + "▱" * (5 - cells) in line1
    assert f"▌ {lv} " in line1


def test_unknown_or_missing_effort(home):
    assert "(weird)" in plain(render(home, "weird")[0])
    assert "Lv." not in plain(render(home)[0])


def test_identity_from_session_profile(home):
    line1 = plain(render(home, "high", CLAUDE_CONFIG_DIR=home.root / TEAM_PROFILE)[0])
    assert line1.startswith("● team [TEAM]")
    assert plain(render(home, "high")[0]).startswith("● personal [PERSONAL]")


@pytest.mark.parametrize("columns", [140, 80, 60, 45])
def test_lines_wrap_within_terminal_width(home, columns):
    lines = render(home, "max", columns=columns)
    assert all(width(line) <= columns - 4 for line in lines), [plain(x) for x in lines]


def test_settings_row_all_on(home):
    row = render(home, "high")[-1]
    assert row.count(SWITCH_ON) == 3
    text = plain(row)
    assert "⟳ 업데이트" in text and "⏾ 절전" in text and "90m" in text
    assert "⇄ 자동 스왑" in text and "같은 그룹만" in text


def test_settings_row_off_and_warnings(home):
    home.set_config(autoUpdate={"enabled": False})  # npm 설치본이 아니면 꺼진 업데이트 항목은 숨긴다
    row = render(home, "high")[-1]
    assert "업데이트" not in plain(row) and row.count(SWITCH_ON) == 2

    home.set_config(hibernate={"enabled": False, "idleMin": 90})
    row = render(home, "high")[-1]
    assert row.count(SWITCH_OFF) == 1 and "꺼짐" in plain(row)
    home.set_config(hibernate={"enabled": True, "idleMin": 90})

    home.set_config(autoUpdate={"enabled": True})
    home.state.mkdir(parents=True, exist_ok=True)
    (home.state / "update-failed").touch()
    home.set_usage("2", 100)  # 옮겨 갈 여유 계정 없음
    row = render(home, "high", FAKE_LAUNCHCTL_RC=1)[-1]  # launchd 미등록
    assert row.count(SWITCH_WARN) == 3
    text = plain(row)
    assert "실패" in text and "미등록" in text and "대상없음" in text


def test_branch_drops_login_name_and_shortens_type(home):
    repo = home.root / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "feature/tester_proj-1_login"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    data = {"model": {"display_name": "Opus 5.5"}, "workspace": {"current_dir": str(repo)}}
    # HUD 는 git 을 0.4초까지만 기다린다(느리면 브랜치를 건너뛴다). 느린 CI 에선 첫 git 호출이 그보다 길어서 몇 번 준다.
    subprocess.run([*git, "status", "-s"], capture_output=True)
    for _ in range(3):
        line1 = plain(home.run("statusline", input=json.dumps(data)).stdout.split("\n")[0])
        if "(" in line1:
            break
    assert "(ft/proj-1_login)" in line1


def test_free_form_groups_and_colors(home):
    home.set_config(**{"1": {"group": "side"}, "2": {"group": "work", "color": 170}})
    line1 = render(home, "high")[0]
    assert plain(line1).startswith("● personal [SIDE]")
    assert "\x1b[38;5;39m" in line1  # 색을 안 정한 첫 그룹 = 팔레트 첫 색
    line1 = render(home, "high", CLAUDE_CONFIG_DIR=home.root / TEAM_PROFILE)[0]
    assert plain(line1).startswith("● team [WORK]") and "\x1b[38;5;170m" in line1


def others_segment(home):
    row2 = plain(render(home, "high")[1])
    return row2[row2.index("↔"):]


def test_other_accounts_registration_order_then_more(home):
    for num, alias in (("3", "gamma"), ("4", "delta")):
        home.add_account(num, alias, 10)
    # 사용 기록이 없으면 등록 순서: team(2), gamma(3) 만 이름으로, 나머지는 외 k개
    assert others_segment(home) == "↔ team 40%/9%, gamma 10%/9% 외 1개"


def test_other_accounts_most_recently_used_first(home):
    now = time.time()
    home.touch_session(home.root / TEAM_PROFILE, now - 3600)
    home.touch_session(home.add_account("3", "gamma", 10), now - 7200)
    home.touch_session(home.add_account("4", "delta", 10), now - 60)  # 방금 씀
    assert others_segment(home) == "↔ delta 10%/9%, team 40%/9% 외 1개"


def test_missing_usage_values_do_not_break_hud(home):
    path = home.backup / "cache/usage.json"
    data = json.loads(path.read_text())
    data["accounts"]["1"]["lastGood"]["five_hour"]["pct"] = None  # 지금 계정 5h 값 없음
    data["accounts"]["2"]["lastGood"]["five_hour"].pop("pct")      # 다른 계정 5h 값 없음
    path.write_text(json.dumps(data))
    lines = render(home, "high")
    assert "[statusline]" not in lines[0]  # 오류 한 줄로 떨어지지 않는다
    row2 = plain(lines[1])
    assert "5h —" in row2 and "team —/9%" in row2


def test_hibernate_shows_threshold_actually_used(home):
    (home.state / "hib").mkdir(parents=True)
    (home.state / "hib/threshold.json").write_text(json.dumps({"idleMin": 30, "why": "memory", "at": time.time()}))
    assert "30m 메모리부족" in plain(render(home, "high")[-1])
    (home.state / "hib/threshold.json").write_text(json.dumps({"idleMin": 30, "at": time.time() - 3600}))
    assert "90m" in plain(render(home, "high")[-1])  # 오래된 기록이면 설정값


def test_english_hud(home):
    for num, alias in (("3", "gamma"), ("4", "delta")):
        home.add_account(num, alias, 10)
    lines = [plain(x) for x in render(home, "high", CC_BATON_LANG="en")]
    assert "⟳ Update" in lines[-1] and "⏾ Sleep" in lines[-1] and "⇄ Auto-swap" in lines[-1]
    assert "same group" in lines[-1]
    assert lines[1].endswith("+1 more")


def test_language_from_config_when_env_unset(home):
    home.set_config(language="en")
    assert "⟳ Update" in plain(render(home, "high", CC_BATON_LANG="")[-1])
    home.set_config(language="ko")
    assert "⟳ 업데이트" in plain(render(home, "high", CC_BATON_LANG="")[-1])
