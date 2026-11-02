"""`cc-baton cswap run` — 번들 claude-swap 을 돌리되, 검증 실패 정리에서 대화 기록(projects/)은 남긴다.

claude-swap 은 `cswap run` 이 세션 프로필 검증에 실패하면 프로필 폴더를 통째로 지운다
(SessionManager._cleanup_failed_session). 그 폴더의 projects/ 가 그 계정의 대화 기록 전부이고,
/swap 이 방금 옮겨 온 대화도 거기 있다. 같은 계정을 다시 등록하면 claude-swap 은 이 폴더를 그대로
다시 쓰므로(_bootstrap 은 토큰과 .claude.json 만 새로 쓴다), projects/ 만 남겨 두면 기록이 이어진다.

그래서 run 만은 exec 하지 않고 같은 프로세스에서 claude-swap 을 돌리며 그 정리를 감싼다.
터미널에서 cswap run 을 직접 부르면 claude-swap 원래 동작 그대로다.
"""
import contextlib
import shutil
import sys
from pathlib import Path

from claude_swap import cli as cswap_cli
from claude_swap import session as cswap_session

from .i18n import L

R, DIM = "\033[0m", "\033[2m"


def keep_projects(cleanup):
    """claude-swap 의 검증 실패 정리를 projects/ 만 빼고 하도록 감싼다.

    projects/ 가 링크(--share-history)면 기록 원본은 ~/.claude 쪽이라 원래 정리를 그대로 쓴다.
    """
    def wrapped(self, session_dir):
        projects = Path(session_dir) / "projects"
        if projects.is_symlink() or not projects.is_dir():
            return cleanup(self, session_dir)
        # 원래 정리와 같은 순서: 키체인 항목 이름이 폴더 경로에서 나오므로 그것부터
        cswap_session.delete_macos_keychain_entry(session_dir)
        for p in Path(session_dir).iterdir():
            if p == projects:
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    p.unlink()
        cswap_session.clear_session_stale(session_dir)
        print(DIM + L(f"[cc-baton] Kept this account's conversation history: {projects}",
                      f"[cc-baton] 이 계정의 대화 기록은 지우지 않고 남겨 두었습니다: {projects}") + R, file=sys.stderr)
    return wrapped


def run(rest):
    mgr = cswap_session.SessionManager
    mgr._cleanup_failed_session = keep_projects(mgr._cleanup_failed_session)
    sys.argv = ["cswap", *rest]
    return cswap_cli.main()
