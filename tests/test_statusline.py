import json

import pytest

from conftest import TEAM_PROFILE, plain, width

SWITCH_ON, SWITCH_OFF, SWITCH_WARN = "\x1b[48;5;22m", "\x1b[48;5;244m", "\x1b[48;2;90;51;0m"


def render(home, effort=None, columns=140, **env):
    data = {"model": {"display_name": "Opus 5.5 (1M context)"},
            "context_window": {"used_percentage": 12},
            "workspace": {"current_dir": str(home.root)}}
    if effort:
        data["effort"] = {"level": effort}
    r = home.run("statusline.py", input=json.dumps(data), COLUMNS=columns, **env)
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
    assert "⇄ 한도스왑" in text and "승인필요" in text


def test_settings_row_off_and_warnings(home):
    home.set_config(autoUpdate={"enabled": False})
    row = render(home, "high")[-1]
    assert row.count(SWITCH_OFF) == 1 and row.count(SWITCH_ON) == 2

    home.set_config(autoUpdate={"enabled": True})
    home.state.mkdir(parents=True, exist_ok=True)
    (home.state / "update-failed").touch()
    home.set_usage("2", 100)  # 옮겨 갈 여유 계정 없음
    row = render(home, "high", FAKE_LAUNCHCTL_RC=1)[-1]  # launchd 미등록
    assert row.count(SWITCH_WARN) == 3
    text = plain(row)
    assert "실패" in text and "미등록" in text and "대상없음" in text
