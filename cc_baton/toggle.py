#!/usr/bin/env python3
"""기능 스위치. 값의 원본은 ~/.config/cc-baton/config.json, HUD 3행이 이걸 보여준다."""
import json
import os
import sys
from pathlib import Path

from . import state as st
from .i18n import L

USAGE_EN = """cc-baton toggle                          show switches
cc-baton toggle update|hib|swap on|off   turn one on or off
cc-baton toggle is-on update|hib|swap    for scripts (exit 0 = on)

  update  update Claude Code before each launch and swap (npm install only)
  hib     hibernate idle sessions automatically (manual /sleep always works)
  swap    schedule a swap to another account when you hit a rate limit"""
USAGE_KO = """cc-baton toggle                          현황
cc-baton toggle update|hib|swap on|off   켜기/끄기
cc-baton toggle is-on update|hib|swap    스크립트용 (exit 0 = 켜짐)

  update  실행·스왑 직전 Claude Code 최신화 (npm 설치본만)
  hib     유휴 세션 자동 재우기 (/sleep 수동 재우기는 끄지 않는다)
  swap    한도 도달 시 다른 계정으로 자동 스왑 예약"""

KEYS = {"update": "autoUpdate", "hib": "hibernate", "swap": "onLimit"}


def set_enabled(key, on):
    # 계정 성격 등 다른 설정과 같은 파일이다. 못 읽으면 덮어쓰지 않고 멈춘다.
    path = st.config_path()
    cfg = json.loads(path.read_text()) if path.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg.setdefault(key, {})["enabled"] = on
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "is-on" and argv[1] in KEYS:
        return 0 if st.feature(KEYS[argv[1]])["enabled"] else 1
    if len(argv) == 2 and argv[0] in KEYS and argv[1] in ("on", "off"):
        set_enabled(KEYS[argv[0]], argv[1] == "on")
    elif argv:
        print(L(USAGE_EN, USAGE_KO), file=sys.stderr)
        return 1
    for name, key in KEYS.items():
        print(f"{name:<7} {'on' if st.feature(key)['enabled'] else 'off'}")
    return 0


def cli():
    return main()
