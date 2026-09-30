#!/usr/bin/env python3
"""기능 스위치. 값의 원본은 ~/.config/cc-baton/config.json, HUD 3행이 이걸 보여준다."""
import json
import os
import sys
from pathlib import Path

from . import state as st
from .i18n import L

USAGE_EN = """cc-baton toggle                          show switches
cc-baton toggle update|hib|swap|usage|bypass on|off   turn one on or off
cc-baton toggle is-on update|hib|swap    for scripts (exit 0 = on)

  update  update Claude Code before each launch and swap (npm install only)
  hib     hibernate idle sessions automatically (manual /sleep always works)
  swap    prepare a swap to another of your accounts when one hits its usage limit
  usage   keep usage numbers fresh (claude-swap asks Anthropic's usage endpoint per account)
  bypass  start sessions with --dangerously-skip-permissions (Claude runs commands without asking)"""
USAGE_KO = """cc-baton toggle                          현황
cc-baton toggle update|hib|swap|usage|bypass on|off   켜기/끄기
cc-baton toggle is-on update|hib|swap    스크립트용 (exit 0 = 켜짐)

  update  실행·스왑 직전 Claude Code 최신화 (npm 설치본만)
  hib     유휴 세션 자동 재우기 (/sleep 수동 재우기는 끄지 않는다)
  swap    한도에 걸린 계정이 있으면 다른 내 계정으로 스왑을 준비
  usage   사용량 숫자를 최신으로 (claude-swap 이 계정마다 Anthropic 사용량 API 를 조회)
  bypass  --dangerously-skip-permissions 로 세션 시작 (Claude 가 묻지 않고 명령 실행)"""

KEYS = {"update": "autoUpdate", "hib": "hibernate", "swap": "onLimit", "usage": "usageRefresh", "bypass": "skipPermissions"}


def set_enabled(key, on):
    # 계정 성격 등 다른 설정과 같은 파일이다. 못 읽으면 덮어쓰지 않고 멈춘다.
    path = st.config_path()
    cfg = json.loads(path.read_text()) if path.exists() else {}
    st.private_dir(path.parent)
    cfg.setdefault(key, {})["enabled"] = on
    st.write_json(path, cfg)


ALIASES = {"sleep": "hib", "autoswap": "swap"}  # HUD·문서 용어로도 부를 수 있게


def main(argv=None):
    argv = [ALIASES.get(a, a) for a in (sys.argv[1:] if argv is None else argv)]
    if len(argv) == 2 and argv[0] == "is-on" and argv[1] in KEYS:
        return 0 if st.feature(KEYS[argv[1]])["enabled"] else 1
    if len(argv) == 2 and argv[0] in KEYS and argv[1] in ("on", "off"):
        if argv[0] == "update" and argv[1] == "on" and not st.claude_is_npm():
            print(L("Your Claude Code updates itself (it isn't an npm install), so this switch has nothing to do.",
                    "Claude Code 가 스스로 업데이트하는 설치본(npm 이 아님)이라 이 스위치는 할 일이 없습니다."), file=sys.stderr)
            return 1
        set_enabled(KEYS[argv[0]], argv[1] == "on")
    elif argv:
        print(L(USAGE_EN, USAGE_KO), file=sys.stderr)
        return 1
    for name, key in KEYS.items():
        print(f"{name:<7} {'on' if st.feature(key)['enabled'] else 'off'}")
    return 0


def cli():
    return main()
