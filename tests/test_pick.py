"""cc-pick 을 진짜 터미널(pty)에 띄워 방향키를 보낸다.

좁은 창에서 한 줄이 접히면 ↑n 으로 되돌아갈 위치가 어긋나 목록이 계속 쌓이던 버그의 회귀 방지.
"""
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

import pytest

from conftest import plain, width

UP, DOWN = b"\x1b[A", b"\x1b[B"


def drive(home, cols, keys, resize_to=None):
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-m", "cc_baton", "pick"],
                  home.env(CC_PICK_NO_REFRESH=1))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, cols, 0, 0))
    out = b""

    def pump(sec):
        nonlocal out
        end = time.time() + sec
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return

    pump(1.0)
    for i, k in enumerate(keys):
        if resize_to and i == 1:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, resize_to, 0, 0))
        os.write(fd, k)
        pump(0.3)
    _, status = os.waitpid(pid, 0)
    return out.decode(errors="ignore"), os.waitstatus_to_exitcode(status)


@pytest.mark.parametrize("cols", [40, 60, 120])
def test_rows_never_wrap(home, cols):
    out, code = drive(home, cols, [DOWN, DOWN, UP, b"q"])
    assert code == 1  # q = 취소
    lines = [x for x in plain(out).replace("\r", "\n").split("\n") if x]
    assert max(width(x) for x in lines) <= cols - 1


def test_enter_selects_highlighted_account(home):
    out, code = drive(home, 80, [DOWN, b"\r"])
    assert code == 0
    assert out.rstrip().endswith("2")  # 기본값(1) 에서 한 칸 내려간 계정 번호가 stdout 으로


def test_redraw_after_narrowing_accounts_for_folded_rows(home):
    # 120칸에서 그린 줄(40칸 넘음)은 40칸으로 줄이면 터미널이 두 줄로 접는다 → 계정 2개 = 4줄 위로
    out, _ = drive(home, 120, [DOWN, DOWN, b"q"], resize_to=40)
    assert "\x1b[4A\r\x1b[J" in out


def test_selection_message_starts_at_line_start(home):
    out, _ = drive(home, 80, [b"\r"])
    before = plain(out).split("→")[0]
    assert before.endswith("\r\n")  # raw 모드에서도 줄 맨 앞으로 돌아간 뒤에 찍힌다
