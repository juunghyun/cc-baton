"""가짜 HOME 에 claude-swap 상태를 만들어 두고 `python -m cc_baton <하위명령>` 을 실제 프로세스로 돌린다.

스크립트는 전부 Path.home() 기준으로 상태를 읽으므로 HOME 만 바꾸면 실사용 환경과 격리된다.
launchctl·curl 은 PATH 앞의 가짜 명령으로 대체한다.
"""
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")

PERSONAL = {"num": "1", "email": "personal@example.com", "alias": "personal"}
TEAM = {"num": "2", "email": "team@example.com", "alias": "team"}
TEAM_PROFILE = ".claude-swap-backup/sessions/2-team_example.com"


def plain(s):
    return ANSI.sub("", s)


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in plain(s))


def _iso(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class Home:
    def __init__(self, root: Path):
        self.root = root
        self.backup = root / ".claude-swap-backup"
        self.fakebin = root / "fakebin"
        self.state = root / ".local/state/cc-swap"
        self.config = root / ".claude/cc-accounts.json"

    # --- 상태 조작 ---
    def write_json(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return p

    def set_usage(self, num, five_hour):
        path = self.backup / "cache/usage.json"
        data = json.loads(path.read_text())
        data["accounts"][num]["lastGood"]["five_hour"]["pct"] = five_hour
        path.write_text(json.dumps(data))

    def set_config(self, **top):
        cfg = json.loads(self.config.read_text())
        cfg.update(top)
        self.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

    def transcript(self, profile_rel, sid, slug="-tmp-proj", body='{"type":"user"}\n'):
        p = self.root / profile_rel / "projects" / slug / f"{sid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        return p

    def fake(self, name, script):
        p = self.fakebin / name
        p.write_text("#!/bin/sh\n" + script + "\n")
        p.chmod(0o755)

    # --- 실행 ---
    def env(self, **extra):
        e = {
            "HOME": str(self.root),
            # 스크립트 shebang 의 python3 가 테스트와 같은 인터프리터를 잡게 한다.
            "PATH": f"{self.fakebin}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "COLUMNS": "140",
            "PYTHONPATH": str(ROOT),
        }
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def run(self, sub, *args, input=None, **env):
        return subprocess.run([sys.executable, "-m", "cc_baton", sub, *args], input=input,
                              capture_output=True, text=True, env=self.env(**env), cwd=self.root, timeout=20)


@pytest.fixture
def home(tmp_path):
    h = Home(tmp_path)
    now = time.time()
    h.write_json(".claude-swap-backup/sequence.json", {
        "activeAccountNumber": 1,
        "sequence": [1, 2],
        "accounts": {a["num"]: {"email": a["email"], "alias": a["alias"]} for a in (PERSONAL, TEAM)},
    })
    usage = {
        "lastGood": {
            "five_hour": {"pct": 21, "resets_at": _iso(3)},
            "seven_day": {"pct": 9, "resets_at": _iso(80)},
            "scoped": [{"name": "Fable", "pct": 0}],
        },
        "fetchedAt": now,
        "nextPollAt": now + 3600,  # statusline 이 백그라운드 갱신을 던지지 않게
    }
    h.write_json(".claude-swap-backup/cache/usage.json", {"accounts": {
        "1": usage,
        "2": {**usage, "lastGood": {**usage["lastGood"], "five_hour": {"pct": 40, "resets_at": _iso(2)}}},
    }})
    h.write_json(".claude/cc-accounts.json", {
        "1": {"kind": "personal"},
        "2": {"kind": "team"},
        "autoUpdate": {"enabled": True},
        "hibernate": {"enabled": True, "idleMin": 90},
        "onLimit": {"enabled": True, "approveCrossing": False, "minHeadroomPct": 15},
    })
    h.write_json(".claude.json", {"oauthAccount": {"emailAddress": PERSONAL["email"]}})
    (tmp_path / TEAM_PROFILE).mkdir(parents=True)
    h.fakebin.mkdir()
    h.fake("launchctl", 'exit "${FAKE_LAUNCHCTL_RC:-0}"')
    h.fake("curl", f'touch "{tmp_path}/curl-called"; exit 1')
    return h
