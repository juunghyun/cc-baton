#!/usr/bin/env python3
"""세션 시작 시 계정 피커. 선택된 계정 번호만 stdout 으로 뱉는다(화면은 전부 stderr).

조작: ↑/↓ (또는 k/j) 이동 · Enter 선택 · 숫자키 즉시 선택 · q/Esc 취소
TTY가 없으면(파이프·CI) 한 줄 입력 방식으로 자동 폴백한다.

기본값은 cwd 매핑(cswap map) → 없으면 기본 로그인 계정. 잔량이 낡았으면 한 번만 동기 갱신한다 —
"어느 계정이 여유 있나"를 보고 고르는 게 이 화면의 존재 이유라, 낡은 숫자는 의미가 없다.
"""
import functools
import os
import re
import subprocess
import sys
import termios
import tty as ttymod
import unicodedata
from pathlib import Path

from . import state as st
from .i18n import L

R, DIM, BOLD, REV = "\033[0m", "\033[2m", "\033[1m", "\033[7m"
HIDE, SHOW = "\033[?25l", "\033[?25h"
STALE = 300.0
E = sys.stderr


def c(pct):
    if pct is None:
        return DIM
    return ("\033[38;5;196m" if pct >= 90 else "\033[38;5;208m" if pct >= 80
            else "\033[38;5;220m" if pct >= 50 else "\033[38;5;42m")


def cell(entry, width=4):
    if not entry or entry.get("pct") is None:
        return f"{DIM}{'—':>{width}}{R}"
    p = entry["pct"]
    return f"{c(p)}{p:>{width - 1}.0f}%{R}"


ANSI = re.compile(r"(\033\[[0-9;?]*[A-Za-z])")


def cw(ch):
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def clip(s, width):
    """한 줄을 터미널 폭 안으로 자른다. 넘쳐서 접히면 ↑n 으로 되돌아갈 위치가 어긋나 줄이 계속 쌓인다."""
    budget = width - 1  # 마지막 칸은 비워 둔다 (그 칸에 쓰면 줄바꿈하는 터미널이 있다)
    if sum(cw(ch) for ch in ANSI.sub("", s)) <= budget:
        return s
    out, used = [], 0
    for tok in ANSI.split(s):
        if ANSI.fullmatch(tok):
            out.append(tok)
            continue
        for ch in tok:
            if used + cw(ch) > budget - 1:  # '…' 한 칸 자리
                return "".join(out) + "…" + R
            out.append(ch)
            used += cw(ch)
    return s


def cols(fd):
    try:
        return os.get_terminal_size(fd).columns or 80  # 폭 0 을 알려 주는 pty 도 있다
    except OSError:
        return 80


def refresh_if_stale(accs):
    if os.environ.get("CC_PICK_NO_REFRESH") or not st.feature("usageRefresh")["enabled"]:
        return
    ages = [(st.usage(a["num"]) or {}).get("_age") for a in accs]
    fresh = [x for x in ages if x is not None]
    if fresh and min(fresh) < STALE:
        return
    # cswap 이 "아직 폴 할 때가 아니다"라고 하면 물어봐야 캐시만 돌아온다 — 그냥 있는 값을 쓴다.
    if not any(st.poll_due(a["num"]) for a in accs):
        return
    if not st.CSWAP_BIN.exists():
        return
    print(DIM + L("Refreshing usage…", "사용량 갱신 중…") + R, end="", file=E, flush=True)
    try:
        subprocess.run([str(st.CSWAP_BIN), "list", "--json"], capture_output=True, timeout=6)
    except Exception:
        pass
    print("\r\033[K", end="", file=E, flush=True)


def pad(text, n):
    """표시 폭 기준 왼쪽 정렬 (한글은 2칸)."""
    return text + " " * max(0, n - sum(cw(ch) for ch in text))


def short(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


@functools.cache
def fable_shown():
    """Fable 창이 있는 계정이 하나라도 있을 때만 그 칸을 보인다 (권한 없는 사람에겐 빈 칸일 뿐)."""
    return any(st.scoped(st.usage(a["num"]), "Fable") for a in st.accounts())


def row(a, selected, is_default):
    """계정 한 줄. selected=커서 위치, is_default=cwd 매핑 기본값."""
    g = st.usage(a["num"]) or {}
    kc = st.color(a["num"])
    fable = st.scoped(g, "Fable")
    age = g.get("_age")
    if not g:
        note = f"{DIM}{st.usage_note(a['num'])}{R}"
    else:
        cd = st.countdown((g.get("seven_day") or {}).get("resets_at"))
        note = f"{DIM}{cd}{R}" + (f"{DIM} ~{age / 60:.0f}m{R}" if age and age > STALE else "")
    cursor = f"{BOLD}❯{R}" if selected else " "
    mark = f"{DIM}·{R}" if is_default and not selected else " "
    name = f"{REV}{kc}{pad(a['label'], 12)}{R}" if selected else f"{kc}{pad(a['label'], 12)}{R}"
    return (f" {cursor}{mark}{a['num']}) {name} {kc}{pad(short(st.group(a['num']), 9), 9)}{R}"
            f"{cell(g.get('five_hour'))} {cell(g.get('seven_day'))}" + (f" {cell(fable, 6)}" if fable_shown() else "") + f"  {note}")


def header(interactive, width=10**4):
    hint = (L("↑↓ move · Enter select · number = pick now · q cancel", "↑↓ 이동 · Enter 선택 · 숫자 즉시선택 · q 취소")
            if interactive else L("number/alias · Enter = default · q cancel", "번호/별칭 입력 · Enter = 기본값 · q 취소"))
    print(clip(f"\n{BOLD}" + L("Choose an account", "계정 선택") + f"{R} {DIM}({hint}){R}", width), file=E)
    head = (f"      {pad(L('Account', '계정'), 12)} {pad(L('Group', '그룹'), 9)}"
            f"{'5h':>4} {'1w':>4}" + (f" {'Fable':>6}" if fable_shown() else "") + f"  {L('1w reset', '주간 리셋')}")
    print(clip(DIM + head + R, width), file=E)


def read_key(fd):
    """한 키 입력. 화살표는 ESC 시퀀스를 붙여 읽는다."""
    ch = os.read(fd, 1)
    if ch != b"\x1b":
        return ch
    seq = os.read(fd, 2) if select_ready(fd) else b""
    if seq.startswith(b"["):
        return {b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left"}.get(seq, "esc")
    return "esc"


def select_ready(fd, timeout=0.05):
    import select as _s
    return bool(_s.select([fd], [], [], timeout)[0])


def interactive_select(accs, default_idx, tty_fd):
    """raw 모드 커서 피커. 선택된 인덱스 or None(취소)."""
    idx = default_idx
    n = len(accs)
    drawn = []  # 마지막으로 그린 줄들의 표시 폭

    def draw():
        c = cols(tty_fd)
        if drawn:
            # 그 사이 창이 줄었으면 터미널이 예전 줄을 접어 두었다. 접힌 만큼 더 올라가고 아래를 비운다.
            up = sum(max(1, -(-w // c)) for w in drawn)
            E.write(f"\033[{up}A\r\033[J")
        drawn.clear()
        for i, a in enumerate(accs):
            line = clip(row(a, i == idx, i == default_idx), c)
            E.write(line + "\r\n")  # raw 모드에선 \n 만으로는 줄 맨 앞으로 안 돌아간다
            drawn.append(sum(cw(ch) for ch in ANSI.sub("", line)))
        E.flush()

    draw()
    print(HIDE, end="", file=E, flush=True)

    typed = ""  # 지금까지 누른 숫자 (10번 이상 계정용)
    old = termios.tcgetattr(tty_fd)
    try:
        ttymod.setraw(tty_fd)
        while True:
            k = read_key(tty_fd)
            if not (isinstance(k, bytes) and k.isdigit()):
                typed = ""
            if k in ("up", b"k", b"K"):
                idx = (idx - 1) % n
            elif k in ("down", b"j", b"J", b"\t"):
                idx = (idx + 1) % n
            elif k in (b"\r", b"\n"):
                return idx
            elif k in ("esc", b"q", b"Q", b"\x03", b"\x04"):
                return None
            elif isinstance(k, bytes) and k.isdigit():
                # 숫자는 이어 붙여 본다: 다른 번호가 그걸로 시작하지 않으면(1 뒤에 10 이 없으면) 바로 고른다
                typed = (typed + k.decode()) if any(a["num"].startswith(typed + k.decode()) for a in accs) else k.decode()
                hit = next((i for i, a in enumerate(accs) if a["num"] == typed), None)
                if hit is not None and not any(a["num"] != typed and a["num"].startswith(typed) for a in accs):
                    return hit
                if hit is not None:
                    idx = hit
                draw()
                continue
            else:
                continue
            draw()
    finally:
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, old)
        print(SHOW, end="", file=E, flush=True)


def line_select(accs, default_idx):
    """TTY가 없을 때의 폴백: 한 줄 입력."""
    for i, a in enumerate(accs):
        print(row(a, False, i == default_idx), file=E)
    print(f"\n{BOLD}> {R}", end="", file=E, flush=True)
    raw = (sys.stdin.readline() or "").strip()
    if raw.lower() in ("q", "quit", "exit"):
        return None
    if not raw:
        return default_idx
    chosen = st.resolve(raw)
    if not chosen:
        print(L(f"Unknown account '{raw}'.", f"'{raw}' 계정을 찾지 못했습니다."), file=E)
        return None
    return next((i for i, a in enumerate(accs) if a["num"] == chosen["num"]), None)


def main():
    accs = st.accounts()
    if not accs:
        print(L("No accounts in claude-swap yet. Log in to Claude Code and run `cswap add` first.",
                "claude-swap 에 등록된 계정이 없습니다. Claude Code 에 로그인한 뒤 `cswap add` 로 먼저 등록하세요."), file=E)
        return 1
    refresh_if_stale(accs)

    default = st.mapped_num() or st.active_num() or accs[0]["num"]
    default_idx = next((i for i, a in enumerate(accs) if a["num"] == default), 0)

    tty_fd = None
    try:
        tty_fd = os.open("/dev/tty", os.O_RDONLY)
        os.isatty(tty_fd)
    except Exception:
        tty_fd = None

    header(tty_fd is not None, cols(tty_fd) if tty_fd is not None else 10**4)
    try:
        if tty_fd is not None:
            idx = interactive_select(accs, default_idx, tty_fd)
        else:
            idx = line_select(accs, default_idx)
    finally:
        if tty_fd is not None:
            os.close(tty_fd)

    if idx is None:
        print(DIM + L("Cancelled.", "취소했습니다.") + R, file=E)
        return 1
    chosen = accs[idx]
    print(f"{DIM}→ {chosen['label']}{st.group_tag(chosen)}{R}", file=E)
    print(chosen["num"])
    return 0


def cli():
    return main()
