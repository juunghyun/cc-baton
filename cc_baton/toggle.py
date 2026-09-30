#!/usr/bin/env python3
"""기능 스위치. 값의 원본은 ~/.claude/cc-accounts.json, HUD 3행이 이걸 보여준다.

  cc-baton toggle                        현황
  cc-baton toggle update|hib|swap on|off 켜기/끄기
  cc-baton toggle is-on update|hib|swap  스크립트용 (exit 0 = 켜짐)

  update  cc 실행·스왑 직전 Claude Code 최신화 (cc-baton update)
  hib     유휴 세션 자동 재우기 (cc-baton hib tick). /sleep 수동 재우기는 끄지 않는다
  swap    한도 도달 시 다른 계정으로 자동 스왑 예약 (cc-baton swap hook limit)
"""
import json
import os
import sys
from pathlib import Path

from . import state as st

KEYS = {"update": "autoUpdate", "hib": "hibernate", "swap": "onLimit"}


def set_enabled(key, on):
    # 계정 성격 등 다른 설정과 같은 파일이다. 못 읽으면 덮어쓰지 않고 멈춘다.
    path = st.KIND_PATH
    cfg = json.loads(path.read_text()) if path.exists() else {}
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
        print(__doc__.strip(), file=sys.stderr)
        return 1
    for name, key in KEYS.items():
        print(f"{name:<7} {'on' if st.feature(key)['enabled'] else 'off'}")
    return 0


def cli():
    return main()
