#!/usr/bin/env python3
"""Claude Code statusline — 지금 어느 계정인지 + 컨텍스트/비용 + 계정 잔량.

설계 규칙 3개:
  1. 네트워크 금지. 잔량은 cswap의 로컬 캐시(cache/usage.json)만 읽는다.
     캐시가 낡으면 백그라운드로 detached 갱신을 1회 던지고, 이번 렌더는 낡은 값을 표시한다.
  2. 절대 죽지 않는다. 어떤 예외든 최소 1줄로 폴백한다 (cswap이 포맷을 바꿔도 statusline은 살아야 함).
  3. 계정 성격(TEAM/PERSONAL)을 눈에 띄게 박는다 — 팀 계정과 개인 계정을 헷갈리는 게 이 시스템의 유일한 치명적 사고.
"""
import colorsys
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from . import state as st
from .i18n import L

R = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
SEP = f"{DIM} · {R}"
PIPE = f"{DIM} │ {R}"

OTHERS_SHOWN = 2        # 2행에 이름까지 보여줄 다른 계정 수. 나머지는 "외 k개"
REFRESH_AFTER = 180.0   # 캐시가 이보다 낡으면 백그라운드 갱신을 던진다
STALE_MARK = 300.0      # 이보다 낡으면 화면에 낡았다고 표시한다


def pct_color(pct):
    if pct is None:
        return DIM
    if pct >= 90:
        return "\033[38;5;196m"
    if pct >= 80:
        return "\033[38;5;208m"
    if pct >= 50:
        return "\033[38;5;220m"
    return "\033[38;5;42m"


def gauge(pct, width=6):
    if pct is None:
        return DIM + "░" * width + R
    filled = max(0, min(width, round(pct / 100 * width)))
    c = pct_color(pct)
    return f"{c}{'█' * filled}{DIM}{'░' * (width - filled)}{R}"


def pct_txt(pct):
    return "—" if pct is None else f"{pct:.0f}%"


def window(label, entry, show_reset=True):
    if not entry or entry.get("pct") is None:
        return f"{DIM}{label} —{R}"
    pct = entry.get("pct")
    out = f"{DIM}{label}{R} {gauge(pct)} {pct_color(pct)}{pct:.0f}%{R}"
    if show_reset:
        cd = st.countdown(entry.get("resets_at"))
        if cd:
            out += f" {DIM}{cd}{R}"
    return out


def maybe_refresh(age):
    """캐시가 낡았고 최근에 갱신을 던진 적 없으면, detached 로 1회 던진다 (usageRefresh 를 켰을 때만)."""
    if not st.feature("usageRefresh")["enabled"]:
        return
    if age is not None and age < REFRESH_AFTER:
        return
    if not any(st.poll_due(a["num"]) for a in st.accounts()):
        return  # cswap 스케줄상 아직 때가 아니다 — 던져봐야 캐시만 돌아온다
    try:
        st.STATE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = st.REFRESH_STAMP
        if stamp.exists() and time.time() - stamp.stat().st_mtime < REFRESH_AFTER:
            return
        stamp.touch()
        if not st.CSWAP_BIN.exists():
            return
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        subprocess.Popen(
            [str(st.CSWAP_BIN), "list", "--json"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            start_new_session=True, env=env,
        )
    except Exception:
        pass


def git_bit(cwd):
    """(브랜치, 더티여부) 또는 None."""
    try:
        if not (Path(cwd) / ".git").exists():
            r = subprocess.run(["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
                               capture_output=True, timeout=0.4, text=True)
            if r.returncode != 0:
                return None
        br = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, timeout=0.4, text=True)
        if br.returncode != 0:
            return None
        dirty = subprocess.run(["git", "-C", cwd, "diff", "--quiet"],
                               capture_output=True, timeout=0.4).returncode != 0
        return (br.stdout.strip(), dirty)
    except Exception:
        return None


# 브랜치 타입 접두사 축약: feature/<user>_proj-302_dashboard → ft/proj-302_dashboard
BRANCH_PREFIX = {"feature": "ft", "fix": "fx", "hotfix": "hf", "chore": "ch",
                 "refactor": "rf", "release": "rel", "bugfix": "fx", "docs": "doc"}
try:  # 브랜치명에 붙이는 본인 이름 = 대개 로그인 이름
    USER_TOKEN = re.compile(rf"\b{re.escape(getpass.getuser())}[_-]", re.I)
except Exception:
    USER_TOKEN = re.compile(r"(?!)")  # 아무것도 안 지운다


def short_branch(branch, cap=22):
    """긴 브랜치명을 뜻이 남는 방향으로 줄인다 (티켓·설명이 있는 꼬리를 지킨다)."""
    b = branch
    if "/" in b:
        head, _, tail = b.partition("/")
        b = f"{BRANCH_PREFIX.get(head.lower(), head[:3])}/{tail}"
    b = USER_TOKEN.sub("", b)          # 항상 본인이라 이름은 정보가 아니다
    if len(b) <= cap:
        return b
    return "…" + b[-(cap - 1):]        # 앞을 버리고 꼬리(티켓·설명)를 남긴다


def short_dir(path, cap=26):
    """워크트리 디렉토리는 '<repo>--<branch슬러그>' 라 브랜치와 중복된다 — repo 만 남기고 ⑂ 로 표시."""
    home = str(Path.home())
    p = path.replace(home, "~", 1) if path.startswith(home) else path
    parts = p.split("/")
    if parts and "--" in parts[-1]:
        parts[-1] = parts[-1].split("--")[0] + "⑂"
        p = "/".join(parts)
    return p if len(p) <= cap else "…" + p[-(cap - 1):]


# effort: 시그널 미터 + 이름 + F1 타이밍 타워 Lv 칩. max 만 무지개가 리프레시마다 45°씩 흐른다.
EFFORT_LV = {"low": 1, "medium": 30, "high": 60, "xhigh": 100, "max": 999}


def tc(rgb):
    return "\033[38;2;%d;%d;%dm" % rgb


def hue(deg):
    return tuple(round(v * 255) for v in colorsys.hls_to_rgb(deg % 360 / 360, 0.63, 0.88))


def effort_bit(level):
    if level not in EFFORT_LV:
        return f"{DIM}({level}){R}"  # 모르는 레벨이 새로 생겨도 표시는 한다
    k = list(EFFORT_LV).index(level) + 1
    shift = int(time.time() // 10) * 45  # settings.json refreshInterval(10초) 주기
    if level == "max":
        cells = [tc(hue(i * 60 + shift)) for i in range(5)]
    elif level == "xhigh":
        cells = [tc((255, round(208 - 113 * i / 3), 0)) for i in range(4)]  # #FFD000 → #FF5F00
    else:
        n256 = {"low": 242, "medium": 67, "high": 33}[level]
        cells = [f"\033[38;5;{n256}m"] * k
    off = {"low": 236, "medium": 237}.get(level, 238)
    meter = "".join(f"{col}▰" for col in cells) + f"\033[38;5;{off}m" + "▱" * (5 - k) + R
    name = {"low": f"{DIM}\033[38;5;240mlow", "medium": "\033[38;5;67mmedium",
            "high": f"{BOLD}\033[38;5;75mhigh", "xhigh": f"{BOLD}\033[38;5;208mxhigh",
            "max": f"{BOLD}\033[38;5;231mMAX"}[level] + R
    stripe = {"low": "\033[38;5;240m", "medium": "\033[38;5;67m", "high": "\033[38;5;33m",
              "xhigh": tc((255, 135, 0)), "max": tc(hue(shift))}[level]
    chip = "\033[3;38;5;245;48;5;235m" if level == "low" else "\033[1;3;38;5;231;48;5;237m"
    return f"{meter} {name} {stripe}▌{R}{chip} Lv.{EFFORT_LV[level]} {R}"


# --- line 3: 기능 스위치 현황 (cc-toggle). 블록 스위치 = 배경색 칸이라 폰트와 무관하게 높이가 맞는다 ---
GRN, ORG = "\033[38;5;42m", "\033[38;5;214m"
SWITCH = {
    "on": "\033[48;5;22m  \033[48;5;42m \033[0m",
    "off": "\033[48;5;244m \033[48;5;236m  \033[0m",
    "warn": "\033[48;2;90;51;0m  \033[48;5;214m \033[0m",
}


def setting(icon, name, state, val="", warn=""):
    sw = SWITCH[state]
    if state == "off":
        return f"{DIM}\033[38;5;240m{icon} {name}{R} {sw}"
    if state == "warn":
        return f"{ORG}{icon}{R} {name} {sw} {BOLD}{ORG}{warn}{R}"
    return f"{GRN}{icon}{R} {name} {sw}" + (f" {DIM}{val}{R}" if val else "")


def hib_loaded():
    try:
        return subprocess.run(["launchctl", "list", st.HIB_LABEL],
                              capture_output=True, timeout=0.4).returncode == 0
    except Exception:
        return True  # 확인 못 하면 경고하지 않는다


def swap_target_exists(num, min_headroom):
    """swap.pick_limit_target 과 같은 기준: 5h 여유가 기준 이상이거나 측정 없는 다른 계정."""
    for a in st.accounts():
        if a["num"] == num:
            continue
        pct = ((st.usage(a["num"]) or {}).get("five_hour") or {}).get("pct")
        if pct is None or 100 - pct >= min_headroom:
            return True
    return False


def hib_idle_text(cfg_min):
    """설정값 대신 hib tick 이 실제로 쓴 기준. 메모리가 부족하면 tick 이 기준을 낮춘다."""
    try:
        d = json.loads((st.STATE_DIR / "hib" / "threshold.json").read_text())
        if time.time() - d["at"] < 600 and d["idleMin"] < cfg_min:
            return f"{d['idleMin']:g}m " + L("low memory", "메모리부족")
    except Exception:
        pass
    return f"{cfg_min:g}m"


def settings_parts(num):
    up, hib, lim = st.feature("autoUpdate"), st.feature("hibernate"), st.feature("onLimit")
    state = lambda on, bad: "off" if not on else ("warn" if bad() else "on")  # noqa: E731
    return [
        setting("⟳", L("Update", "업데이트"), state(up["enabled"], st.UPDATE_FAILED.exists), warn=L("failed", "실패")),
        setting("⏾", L("Sleep", "절전"), state(hib["enabled"], lambda: not hib_loaded()),
                val=hib_idle_text(hib["idleMin"]), warn=L("not loaded", "미등록")),
        setting("⇄", L("Auto-swap", "한도스왑"),
                state(lim["enabled"], lambda: not swap_target_exists(num, lim["minHeadroomPct"])),
                val=L("any group", "교차허용") if lim["approveCrossing"] else L("same group", "승인필요"),
                warn=L("no target", "대상없음")),
    ]


def vislen(s):
    """ANSI 이스케이프를 뺀 실제 표시 폭 (한글 등 전각은 2칸)."""
    plain = re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", s)
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in plain)


def wrap(parts, sep, limit):
    """세그먼트 단위 줄바꿈. 한 줄이 폭을 넘으면 CC 가 '…' 로 잘라버리니, 넘기 전에 다음 줄로 보낸다."""
    lines = []
    for p in parts:
        if lines and vislen(lines[-1] + sep + p) <= limit:
            lines[-1] += sep + p
        else:
            lines.append(p)
    return lines


def main():
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}

    ident = st.identity()
    num, label = ident["num"], ident["label"]
    kcolor = st.color(num) if num else "\033[38;5;245m"
    ktext = st.group(num).upper() if num and st.group(num) != "unknown" else "?"

    # --- line 1: 정체성 + 세션 상태 ---
    badge = f"{kcolor}●{R} {BOLD}{kcolor}{label}{R} {kcolor}[{ktext}]{R}"
    if num is None:
        # 라이브 자격증명이 어느 슬롯에도 없다 — cswap 이 모르는 계정으로 돌고 있다.
        badge += f" {DIM}" + L("(not in cswap)", "(cswap 미등록)") + R
    elif ident["drift"]:
        badge += " \033[38;5;196m⚠" + L("drift", "드리프트") + R
    parts = [badge]

    # CC 는 statusline 을 tty 없이 띄우지만 COLUMNS 를 넘겨준다 (get_terminal_size 가 그걸 읽는다).
    try:
        width = shutil.get_terminal_size((110, 24)).columns
    except Exception:
        width = 110
    limit = max(20, width - 4)

    model = ((data.get("model") or {}).get("display_name") or "").strip()
    model = model.replace(" (1M context)", "[1M]").replace(" (1m context)", "[1M]")
    effort = ((data.get("effort") or {}).get("level") or "").strip()
    if model:
        parts.append(model + (f" {effort_bit(effort)}" if effort else ""))

    # ctx 는 계정 다음으로 중요한데다 계속 변하는 값이라, 긴 경로/브랜치보다 앞에 둔다.
    # 뒤에 두면 워크트리 이름이 길 때 화면 밖으로 밀려난다(실제로 겪은 문제).
    ctx = (data.get("context_window") or {}).get("used_percentage")
    if ctx is not None:
        parts.append(f"{DIM}ctx{R} {pct_color(ctx)}{ctx:.0f}%{R}")

    cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or ""
    if cwd:
        g = git_bit(cwd)
        # 마지막 줄에 남은 폭을 재서 경로/브랜치 예산을 정한다. 너무 좁으면 경로는 다음 줄로 넘긴다.
        budget = limit - vislen(wrap(parts, SEP, limit)[-1]) - vislen(SEP)
        if budget < 20:
            budget = limit
        bshort = short_branch(g[0], cap=min(24, max(10, budget // 2))) if g else ""
        dcap = max(10, budget - (vislen(bshort) + 3 if bshort else 0))
        seg = short_dir(cwd, cap=min(30, dcap))
        if g:
            seg += f" {DIM}({bshort}{'*' if g[1] else ''}){R}"
        parts.append(seg)

    lines = wrap(parts, SEP, limit)

    # --- line 2: 이 계정의 잔량 + 다른 계정 요약 ---
    good = st.usage(num)
    age = good.get("_age") if good else None
    maybe_refresh(age)

    if good:
        seg = [window("5h", good.get("five_hour")), window("1w", good.get("seven_day"))]
        fable = st.scoped(good, "Fable")
        if fable:
            mark = "⚠" if (fable.get("pct") or 0) >= 100 else ""
            seg.append(window("Fable", fable, show_reset=False) + mark)
        if age is not None and age > STALE_MARK:
            seg[0] = f"{DIM}~{R}" + seg[0]
            seg[-1] += f" {DIM}(" + L(f"{age / 60:.0f}m ago", f"{age / 60:.0f}m전") + f"){R}"
    else:
        seg = [f"{DIM}5h — │ 1w — ({st.usage_note(num)}){R}"]

    # 다른 계정: 최근에 쓴 순서(기록 없는 계정은 등록 순서로 뒤에). 동시에 여러 계정을 써도 계정별 시각으로 줄 세운다.
    others = []
    for i, a in enumerate(st.accounts()):
        if a["num"] == num:
            continue
        g2 = st.usage(a["num"])
        if not g2:
            continue
        f5 = (g2.get("five_hour") or {}).get("pct")
        f7 = (g2.get("seven_day") or {}).get("pct")
        if f5 is None and f7 is None:
            continue
        used = st.last_used(a["num"])
        text = f"{st.color(a['num'])}{a['label']}{R} {pct_color(f5)}{pct_txt(f5)}{R}/{pct_color(f7)}{pct_txt(f7)}{R}"
        others.append(((used is None, -(used or 0), i), text))
    others = [t for _, t in sorted(others)]
    if others:
        more = len(others) - OTHERS_SHOWN
        seg.append(f"{DIM}↔{R} " + f"{DIM},{R} ".join(others[:OTHERS_SHOWN])
                   + (f" {DIM}" + L(f"+{more} more", f"외 {more}개") + R if more > 0 else ""))

    lines += wrap(seg, PIPE, limit)
    lines += wrap(settings_parts(num), SEP, limit)
    print("\n".join(lines))


def cli():
    try:
        main()
    except Exception as exc:  # statusline 은 무슨 일이 있어도 한 줄은 뱉는다
        print(f"\033[2m[statusline] {type(exc).__name__}: {str(exc)[:60]}\033[0m")
