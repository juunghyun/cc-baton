"""cc-baton <하위명령> [인자…]

  install        Claude Code 에 연결 (질문 없음, 여러 번 돌려도 같은 결과)
  setup          설정 위저드: 계정 그룹, HUD, 절전, 한도 자동 스왑 (baton 첫 실행 때 자동)
  uninstall      연결 제거 (--purge: 설정·기록까지, --yes: 묻지 않음)
  upgrade        cc-baton 을 최신으로 올리고 다시 연결

  swap           계정 스왑: request <계정> | consume | handoff <계정> | hook prompt|limit
  pick           계정 피커 (선택한 계정 번호를 stdout 으로)
  toggle         기능 스위치: update|hib|swap on|off, is-on <이름>
  hib            유휴 세션 재우기: scan | tick | sleep | wake | list | resume …
  statusline     Claude Code statusLine 명령
  claude-update  Claude Code 최신화 (npm 설치본, baton 이 실행 전에 부른다)
  cswap          함께 설치된 claude-swap 실행
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

from . import __version__

MODULES = ("swap", "pick", "toggle", "hib", "statusline")
INSTALL_CMDS = {"install": "cmd_install", "setup": "cmd_setup", "uninstall": "cmd_uninstall", "upgrade": "cmd_upgrade"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sub, rest = (argv[0], argv[1:]) if argv else ("", [])
    if sub in ("-V", "--version"):
        print(f"cc-baton {__version__}")
        return 0
    if sub == "claude-update":
        # 쉘 스크립트가 다시 cc-baton(toggle) 을 부를 수 있게 지금 인터프리터를 넘긴다.
        env = {**os.environ, "CC_BATON_PY": sys.executable}
        return subprocess.call(["/bin/bash", str(Path(__file__).with_name("claude_update.sh")), *rest], env=env)
    if sub == "cswap":
        from .state import CSWAP_BIN
        os.execv(str(CSWAP_BIN), ["cswap", *rest])
    if sub in INSTALL_CMDS:
        from . import install
        return getattr(install, INSTALL_CMDS[sub])(rest) or 0
    if sub not in MODULES:
        print(__doc__.strip(), file=sys.stderr)
        return 0 if sub in ("-h", "--help") else 2
    sys.argv = [f"cc-baton {sub}", *rest]  # 각 모듈은 sys.argv 를 그대로 읽는다
    return importlib.import_module(f".{sub}", __package__).cli() or 0
