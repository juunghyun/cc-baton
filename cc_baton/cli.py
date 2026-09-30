"""cc-baton 진입점. 하위명령을 각 모듈로 넘긴다."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .i18n import L, LANGS, lang

MODULES = ("swap", "pick", "toggle", "hib", "statusline")
INSTALL_CMDS = {"install": "cmd_install", "setup": "cmd_setup", "uninstall": "cmd_uninstall", "upgrade": "cmd_upgrade"}


def usage():
    return L("""cc-baton <command> [args…]

  install        Wire cc-baton into Claude Code (no questions, safe to re-run)
  setup          Setup wizard: language, account groups, HUD, sleep, auto-swap (runs on first `baton`)
  uninstall      Remove what cc-baton added (--purge: settings and state too, --yes: don't ask)
  upgrade        Upgrade cc-baton to the latest release and re-apply the wiring
  lang [en|ko]   Show or change the display language

  swap           Account swap: request <account> | consume | handoff <account> | hook prompt|limit
  pick           Account picker (prints the chosen account number)
  toggle         Feature switches: update|hib|swap on|off, is-on <name>
  hib            Idle-session sleep: scan | tick | sleep | wake | list | resume …
  statusline     Claude Code statusLine command
  claude-update  Update Claude Code (npm install; `baton` runs it before launch)
  cswap          Run the bundled claude-swap""", """cc-baton <하위명령> [인자…]

  install        Claude Code 에 연결 (질문 없음, 여러 번 돌려도 같은 결과)
  setup          설정 위저드: 언어, 계정 그룹, HUD, 절전, 한도 자동 스왑 (baton 첫 실행 때 자동)
  uninstall      연결 제거 (--purge: 설정·기록까지, --yes: 묻지 않음)
  upgrade        cc-baton 을 최신 릴리스로 올리고 다시 연결
  lang [en|ko]   화면 언어 보기/바꾸기

  swap           계정 스왑: request <계정> | consume | handoff <계정> | hook prompt|limit
  pick           계정 피커 (선택한 계정 번호를 stdout 으로)
  toggle         기능 스위치: update|hib|swap on|off, is-on <이름>
  hib            유휴 세션 재우기: scan | tick | sleep | wake | list | resume …
  statusline     Claude Code statusLine 명령
  claude-update  Claude Code 최신화 (npm 설치본, baton 이 실행 전에 부른다)
  cswap          함께 설치된 claude-swap 실행""")


def cmd_lang(rest):
    if not rest:
        print(lang())
        return 0
    if rest[0] not in LANGS:
        print(L("usage: cc-baton lang en|ko", "사용법: cc-baton lang en|ko"), file=sys.stderr)
        return 1
    from . import i18n, install
    try:
        install.check_config()
    except install.BrokenConfig as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    install.save_config(language=rest[0])
    i18n.set_lang(rest[0])
    install._refresh_hook_messages(install.bin_path())  # 훅 안내 문구도 새 언어로
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sub, rest = (argv[0], argv[1:]) if argv else ("", [])
    if sub in ("-V", "--version"):
        print(f"cc-baton {__version__}")
        return 0
    if sub == "lang":
        return cmd_lang(rest)
    if sub == "claude-update":
        # 쉘 스크립트가 다시 cc-baton(toggle) 을 부를 수 있게 지금 인터프리터와 언어를 넘긴다.
        env = {**os.environ, "CC_BATON_PY": sys.executable, "CC_BATON_LANG": lang()}
        return subprocess.call(["/bin/bash", str(Path(__file__).with_name("claude_update.sh")), *rest], env=env)
    if sub == "cswap":
        from .state import CSWAP_BIN
        try:
            os.execv(str(CSWAP_BIN), ["cswap", *rest])
        except OSError:
            print(L(f"claude-swap isn't installed ({CSWAP_BIN}). Reinstall cc-baton: uv tool install --reinstall git+https://github.com/juunghyun/cc-baton",
                    f"claude-swap 이 설치돼 있지 않습니다 ({CSWAP_BIN}). cc-baton 을 다시 설치하세요: uv tool install --reinstall git+https://github.com/juunghyun/cc-baton"),
                  file=sys.stderr)
            return 1
    if sub in INSTALL_CMDS:
        from . import install
        if sub in ("install", "setup"):
            try:
                install.check_config()  # 깨진 설정을 {} 로 덮어쓰지 않게 시작 전에 멈춘다
            except install.BrokenConfig as e:
                print(f"✗ {e}", file=sys.stderr)
                return 1
        return getattr(install, INSTALL_CMDS[sub])(rest) or 0
    if sub not in MODULES:
        print(usage(), file=sys.stderr)
        return 0 if sub in ("-h", "--help") else 2
    sys.argv = [f"cc-baton {sub}", *rest]  # 각 모듈은 sys.argv 를 그대로 읽는다
    return importlib.import_module(f".{sub}", __package__).cli() or 0
