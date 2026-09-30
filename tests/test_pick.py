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

from conftest import BIN, plain, width

UP, DOWN = b"\x1b[A", b"\x1b[B"


def drive(home, cols, keys):
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, str(BIN / "cc-pick")],
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
    for k in keys:
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
