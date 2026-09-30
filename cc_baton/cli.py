"""cc-baton <하위명령> [인자…]

  swap        계정 스왑: request <계정> | consume | handoff <계정> | hook prompt|limit
  pick        시작 시 계정 피커 (선택한 계정 번호를 stdout 으로)
  toggle      기능 스위치: update|hib|swap on|off, is-on <이름>
  hib         유휴 세션 재우기: scan | tick | sleep | wake | list | resume …
  update      Claude Code 최신화 (cc 실행·스왑 직전)
  statusline  Claude Code statusLine 명령
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

from . import __version__

MODULES = ("swap", "pick", "toggle", "hib", "statusline")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sub, rest = (argv[0], argv[1:]) if argv else ("", [])
    if sub in ("-V", "--version"):
        print(f"cc-baton {__version__}")
        return 0
    if sub == "update":
        # 쉘 스크립트가 다시 cc-baton(toggle) 을 부를 수 있게 지금 인터프리터를 넘긴다.
        env = {**os.environ, "CC_BATON_PY": sys.executable}
        return subprocess.call(["/bin/bash", str(Path(__file__).with_name("update.sh")), *rest], env=env)
    if sub not in MODULES:
        print(__doc__.strip(), file=sys.stderr)
        return 0 if sub in ("-h", "--help") else 2
    sys.argv = [f"cc-baton {sub}", *rest]  # 각 모듈은 sys.argv 를 그대로 읽는다
    return importlib.import_module(f".{sub}", __package__).cli() or 0
