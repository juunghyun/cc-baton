#!/usr/bin/env python3
"""세션 최대절전 — 오래 유휴인 claude 세션을 죽이되, 탭과 맥락은 남긴다.

메모리를 무는 건 세션당 200~450MB 짜리 node 프로세스 하나다. 맥락은 이미 트랜스크립트로
디스크에 증분 기록되므로(유휴 세션은 진행 중인 턴이 없다) 죽여도 잃을 게 없다.
그래서 "가볍게 유지"가 아니라 "죽이고 되살린다".

cc 래퍼가 이미 while 루프라 같은 탭에서 재실행할 수 있다. 재우기는 그 루프에
"재실행 전에 멈춰서 기다린다"는 분기 하나를 더한 것이다.

서브커맨드:
  scan [--idle N]     후보 판정표 (읽기 전용)
  tick                launchd 용 1회 패스: 유예 걸기 → 만료분 재우기
  sleep [대상] [옵션]   지금 즉시 재우기 (유예·알림 없음)
                        대상 생략 = 지금 이 세션. pid 나 이름(앞부분)도 받는다
                        --all [분]  조건 맞는 유휴 세션 전부
                        --dry       무엇을 재울지만 보여준다
                        --force     차단신호·래퍼 미지원을 무시한다
  claim               cc 래퍼가 호출. 내 tty 앞으로 온 재우기 요청을 소비
  banner <sid>        대기 화면 출력
  wake <sid>          parked 해제 (성공 시 exit 0)
  list                재운/복원 가능 세션
  restore [--script P] 여러 개를 한 번에 되살릴 때 쓸 명령 목록 (재부팅 후)
  resume <번호|ID>    그 자리에서 하나를 되살린다
  pick                피커 → 선택 결과를 stdout 으로 (cc --wake 용)
  clean               죽은 마커 청소
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import state as st
from .i18n import L


def vtxt(v):
    """판정 값(내부는 한국어 그대로) → 화면 문구."""
    return L("sleep", "재우기") if v == "재우기" else L("skip", "제외")


def otxt(o):
    """출처 값(재움 = 우리가 재움, 끊김 = 재부팅 등으로 끊김) → 화면 문구."""
    return L("slept", "재움") if o == "재움" else L("lost", "끊김")

# launchd 로그로 갈 땐 색을 빼야 읽힌다.
if sys.stdout.isatty() or sys.stderr.isatty():
    R, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
    RED, YEL, GRN, CYA, MAG = ("\033[38;5;196m", "\033[38;5;208m", "\033[38;5;42m",
                               "\033[38;5;39m", "\033[38;5;170m")
else:
    R = DIM = BOLD = RED = YEL = GRN = CYA = MAG = ""
STATE = st.STATE_DIR / "hib"
REQ, PARKED, PENDING = STATE / "req", STATE / "parked", STATE / "pending"
CAP = STATE / "cap"          # 재우기를 받을 줄 아는 래퍼가 붙은 tty
IDLE_MIN_PRESSURE = 30.0         # 메모리 압박 시 임계
GRACE_SEC = 300.0                # 유예 (초)
REQ_TTL = 180.0                  # 재우기 요청 신선도 — tty 재사용 오발동 방지
TAIL = 400_000                   # 트랜스크립트 꼬리 읽기 크기
E = sys.stderr


# ── 기본 ─────────────────────────────────────────────────────────────
def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def _save(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    tmp.replace(p)


def profiles():
    """계정마다 레지스트리가 따로 논다. 한쪽만 보면 절반을 놓친다."""
    out = [Path.home() / ".claude"]
    out += sorted(p for p in (Path.home() / ".claude-swap-backup/sessions").glob("*") if p.is_dir())
    return out


def account_of(profile):
    """프로필 디렉토리 → 계정 번호."""
    m = re.match(r"(\d+)-", Path(profile).name)
    if m:
        return m.group(1)
    try:
        return st.active_num()
    except Exception:
        return None


def live_claude():
    """pid → {tty, args}. pgrep 은 자기 조상 세션을 놓치므로 ps 를 쓴다."""
    out = subprocess.run(["ps", "-Ao", "pid=,tty=,rss=,args="],
                         capture_output=True, text=True).stdout
    m = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or "bin/claude" not in parts[3]:
            continue
        m[int(parts[0])] = {"tty": parts[1], "rss": int(parts[2]) // 1024, "args": parts[3]}
    return m


def proc_start_epoch(pid):
    """ps lstart 은 로컬시각."""
    o = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                       capture_output=True, text=True).stdout.strip()
    if not o:
        return None
    try:
        return datetime.datetime.strptime(" ".join(o.split()), "%a %b %d %H:%M:%S %Y").timestamp()
    except Exception:
        return None


def reg_start_epoch(s):
    """레지스트리 procStart 은 UTC. 그대로 비교하면 9시간 어긋나 전부 걸러진다."""
    if not s:
        return None
    try:
        dt = datetime.datetime.strptime(" ".join(s.split()), "%a %b %d %H:%M:%S %Y")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return None


def my_tty():
    for fd in (0, 1, 2):
        try:
            return Path(os.ttyname(fd)).name
        except Exception:
            continue
    return None


LIVE = STATE / "live.json"          # 살아있는 세션 스냅샷 — 재부팅으로 끊긴 것을 알아내는 유일한 근거
LIVE_TTL_DAYS = 14


def boot_epoch():
    """커널 부팅 시각. '재부팅으로 끊겼다'와 '사용자가 닫았다'를 가르는 기준선."""
    try:
        o = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
        m = re.search(r"sec\s*=\s*(\d+)", o)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def snapshot(rows):
    """매 tick 마다 살아있는 세션을 적어둔다.

    claude 는 정상 종료하면 자기 레지스트리 파일을 스스로 지운다. 그래서 껐다 켜면
    그때 떠 있던 세션들의 흔적이 사라져 아무도 그걸 모르게 된다. 이 스냅샷이 그 구멍을 메운다."""
    prev = _load(LIVE, {}) or {}
    keep = dict(prev.get("sessions") or {})
    now = time.time()
    for r in rows:
        keep[r["sid"]] = {"sid": r["sid"], "name": r["name"], "cwd": r["cwd"],
                          "profile": r["profile"], "account": r["account"],
                          "rss": r["rss"], "seenAt": now}
    cut = now - LIVE_TTL_DAYS * 86400
    keep = {k: v for k, v in keep.items() if v.get("seenAt", 0) > cut}
    _save(LIVE, {"v": 1, "updatedAt": now, "sessions": keep})


def cap_ok(tty):
    """그 탭의 cc 래퍼가 재우기를 받을 줄 아는가.

    .zshrc 를 고쳐도 이미 떠 있는 셸은 옛 함수 본문을 물고 있다. 그런 탭을 재우면
    대기 분기가 없어 탭이 그대로 프롬프트로 떨어진다. 래퍼가 매 루프마다 남기는
    표식이 있을 때만 자동으로 재운다 — 없으면 탭을 새로 열 때까지 건너뛴다."""
    d = _load(CAP / tty)
    if not isinstance(d, dict):
        return False
    shpid = d.get("shellPid")
    if not shpid:
        return False
    o = subprocess.run(["ps", "-o", "tty=", "-p", str(shpid)], capture_output=True, text=True).stdout.strip()
    return o == tty                                  # 셸이 살아있고 같은 tty 인가


def cmd_cap(argv):
    """cc 래퍼가 claude 를 띄우기 직전 호출한다."""
    tty = my_tty()
    if not tty:
        return 1
    shpid = int(argv[0]) if argv and argv[0].isdigit() else os.getppid()
    _save(CAP / tty, {"shellPid": shpid, "at": time.time()})
    return 0


def mem_pressure():
    """(여유%, 스왑사용MB). 압박이면 임계를 낮춘다."""
    free = None
    try:
        o = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"free percentage:\s*(\d+)%", o)
        if m:
            free = int(m.group(1))
    except Exception:
        pass
    swap = 0.0
    try:
        o = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
        m = re.search(r"used\s*=\s*([\d.]+)M", o)
        if m:
            swap = float(m.group(1))
    except Exception:
        pass
    return free, swap


def idle_threshold():
    free, swap = mem_pressure()
    if (free is not None and free < 25) or swap > 512:
        return IDLE_MIN_PRESSURE, L(f"memory pressure (free {free}%, swap {swap:.0f}MB)", f"메모리 압박(여유 {free}%, 스왑 {swap:.0f}MB)")
    return float(st.feature("hibernate")["idleMin"]), None  # 유휴 임계(분), config.json


# ── 트랜스크립트 구조 분석 ────────────────────────────────────────────
def tail_records(path):
    sz = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, sz - TAIL))
        raw = f.read().decode("utf-8", "replace")
    lines = raw.splitlines()
    if sz > TAIL and lines:
        lines = lines[1:]                      # 잘린 첫 줄 버림
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def blockers(recs, now):
    """재우면 안 되는 이유. 문자열 매칭은 못 쓴다 — 대화에서 도구 이름을 '언급'만 해도
    걸려서 전 세션이 제외돼 버린다. tool_use 레코드를 구조적으로 봐야 한다."""
    uses, results = {}, set()
    wakeup, ledger = None, None
    for d in recs:
        if d.get("type") == "artifact-autoreact-ledger":
            ledger = d
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for it in content:
            if not isinstance(it, dict):
                continue
            if it.get("type") == "tool_use":
                uses[it.get("id")] = (it.get("name"), it.get("input") or {})
                if it.get("name") == "ScheduleWakeup":
                    wakeup = (d.get("timestamp"), it.get("input") or {})
            elif it.get("type") == "tool_result":
                results.add(it.get("tool_use_id"))

    out = []
    for uid, (name, inp) in uses.items():
        if uid in results:
            continue
        if name == "Monitor":
            out.append(L("Monitor still running", "Monitor 미완료"))
        elif name == "Bash" and inp.get("run_in_background"):
            out.append(L("background Bash still running", "백그라운드 Bash 미완료"))
    if wakeup:
        ts, inp = wakeup
        if not inp.get("stop") and ts:
            try:
                t0 = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                left = (t0 + float(inp.get("delaySeconds") or 0)) - now
                if left > -300:
                    out.append(L(f"ScheduleWakeup due {left / 60:+.0f}m", f"ScheduleWakeup 예약 {left / 60:+.0f}분"))
            except Exception:
                out.append(L("ScheduleWakeup scheduled (time unknown)", "ScheduleWakeup 예약(시각 불명)"))
    if ledger:
        # 아티팩트가 있다는 것만으론 제외하지 않는다. resume 하면 watch 는 대체로 복구된다.
        # 실제로 코멘트 스레드가 오가는 중일 때만 하드로 올린다.
        for aid, info in (ledger.get("artifacts") or {}).items():
            if info.get("threads"):
                out.append(L(f"{len(info['threads'])} artifact comment thread(s)", f"아티팩트 코멘트 스레드 {len(info['threads'])}건"))
    return sorted(set(out))


# ── 세션 수집 ────────────────────────────────────────────────────────
def sessions():
    now = time.time()
    lp = live_claude()
    rows = []
    for prof in profiles():
        for f in sorted((prof / "sessions").glob("*.json")):
            d = _load(f)
            if not isinstance(d, dict):
                continue
            pid = d.get("pid")
            if pid not in lp:
                continue                                   # 죽은 엔트리
            a, b = proc_start_epoch(pid), reg_start_epoch(d.get("procStart"))
            if a and b and abs(a - b) > 2:
                continue                                   # pid 재사용
            sid = d.get("sessionId")
            hits = sorted((prof / "projects").glob(f"*/{sid}.jsonl"))
            rows.append({
                "pid": pid, "sid": sid, "profile": str(prof), "account": account_of(prof),
                "name": d.get("name"), "cwd": d.get("cwd"), "status": d.get("status"),
                "kind": d.get("kind"), "tty": lp[pid]["tty"], "rss": lp[pid]["rss"],
                "idle_min": (now - (d.get("statusUpdatedAt") or 0) / 1000) / 60,
                "transcript": str(hits[0]) if hits else None,
                "regfile": str(f),
            })
    return rows, lp, now


def verdict(row, now, idle_min_th):
    if row["kind"] and row["kind"] != "interactive":
        return "제외", f"kind={row['kind']}"
    if row["status"] != "idle":
        return "제외", f"status={row['status']}"
    if row["idle_min"] < idle_min_th:
        return "제외", L(f"idle {row['idle_min']:.0f}m < {idle_min_th:.0f}m", f"유휴 {row['idle_min']:.0f}분 < {idle_min_th:.0f}분")
    if not row["transcript"]:
        return "제외", L("no transcript", "트랜스크립트 없음")
    b = blockers(tail_records(row["transcript"]), now)
    if b:
        return "제외", ", ".join(b)
    if not cap_ok(row["tty"]):
        return "제외", L("tab not running baton (open a new tab to enable)", "래퍼 미지원 — 탭을 새로 열어야 적용됨")
    return "재우기", L(f"idle {row['idle_min']:.0f}m", f"유휴 {row['idle_min']:.0f}분")


# ── 명령 ─────────────────────────────────────────────────────────────
def cmd_scan(argv):
    th, why = idle_threshold()
    if "--idle" in argv:
        try:
            th = float(argv[argv.index("--idle") + 1]); why = L("set by hand", "수동 지정")
        except (IndexError, ValueError):
            print(L("usage: cc-baton hib scan [--idle <minutes>]", "사용법: cc-baton hib scan [--idle <분>]"), file=E)
            return 1
    rows, lp, now = sessions()
    unreg = sorted(set(lp) - {r["pid"] for r in rows})
    print(BOLD + L(f"threshold {th:.0f}m", f"임계 {th:.0f}분") + R + (f" {YEL}({why}){R}" if why else ""))
    print(f"{L('verdict', '판정'):<8} {'pid':>7} {'status':<7} {L('idle m', '유휴분'):>7} {'RSS':>7} {'name':<15} "
          + L("reason", "사유"))
    print("─" * 104)
    out = []
    for r in sorted(rows, key=lambda x: -x["idle_min"]):
        v, w = verdict(r, now, th)
        out.append((v, r, w))
    for v, r, w in sorted(out, key=lambda t: (t[0] != "재우기", -t[1]["idle_min"])):
        c = GRN if v == "재우기" else DIM
        mark = "★" if v == "재우기" else " "
        print(f"{c}{mark}{vtxt(v):<7}{R} {r['pid']:>7} {r['status'] or '?':<7} "
              f"{r['idle_min']:>7.1f} {r['rss']:>5}MB {str(r['name'])[:15]:<15} {w}")
    n = sum(1 for v, _, _ in out if v == "재우기")
    save = sum(r["rss"] for v, r, _ in out if v == "재우기")
    print("\n" + L(f"{n} to sleep / {len(out)} total", f"대상 {n}개 / 전체 {len(out)}개")
          + (L(f"  → frees about {save}MB", f"  → 회수 예상 {save}MB") if n else ""))
    if unreg:
        print(DIM + L(f"live pids without a session record {unreg}: can't identify them, left alone",
                      f"레지스트리 없는 살아있는 pid {unreg} — 식별 불가라 건드리지 않음") + R)
    return out


def notify(title, msg):
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification {json.dumps(msg, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)}'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def tty_note(row):
    """래퍼 없는 탭은 재우면 그냥 셸 프롬프트로 떨어진다. 무슨 일이 있었는지 화면에 남긴다.
    tty 에 직접 쓰면 TUI 를 건드리지 않고 프롬프트 옆에 찍힌다(검증됨)."""
    try:
        with open(f"/dev/{row['tty']}", "w") as t:
            t.write("\n  \033[38;5;170m💤 " + L("This session was hibernated to free memory", "이 세션은 메모리 회수를 위해 재워졌습니다")
                    + f"\033[0m  \033[2m({row['rss']}MB)\033[0m\n"
                    + "  \033[2m" + L("The conversation is intact. To come back:", "대화는 그대로입니다. 돌아가려면:")
                    + "\033[0m baton --wake\n\n")
    except OSError:
        pass                                    # 탭이 이미 닫혔으면 그만


def do_sleep(row, reason, selfkill=False):
    """마커를 남기고 SIGTERM. claude 는 SIGTERM 을 1초 안에 정상 처리한다(소켓까지 정리)."""
    rec = {"v": 1, "sid": row["sid"], "pid": row["pid"], "tty": row["tty"],
           "cwd": row["cwd"], "name": row["name"], "profile": row["profile"],
           "account": row["account"], "hibernatedAt": time.time(),
           "reason": reason, "rss": row["rss"]}
    _save(REQ / f"{row['tty']}.json", rec)
    _save(PARKED / f"{row['sid']}.json", rec)
    try:
        os.kill(row["pid"], 15)
    except ProcessLookupError:
        (REQ / f"{row['tty']}.json").unlink(missing_ok=True)
        (PARKED / f"{row['sid']}.json").unlink(missing_ok=True)
        return False, L("already exited", "이미 종료됨")
    if selfkill:
        return True, L(f"freed {row['rss']}MB (this session)", f"{row['rss']}MB 회수 (이 세션)")   # 내가 곧 같이 죽는다. 기다리지 않는다.
    for _ in range(100):
        time.sleep(0.1)
        try:
            os.kill(row["pid"], 0)
        except ProcessLookupError:
            if not cap_ok(row["tty"]):
                tty_note(row)
            return True, L(f"freed {row['rss']}MB", f"{row['rss']}MB 회수")
    try:
        os.kill(row["pid"], 9)
    except Exception:
        pass
    if not cap_ok(row["tty"]):
        tty_note(row)
    return True, L(f"freed {row['rss']}MB (escalated to SIGKILL)", f"{row['rss']}MB 회수 (SIGKILL 에스컬레이션)")


def cmd_tick(argv):
    if not st.feature("hibernate")["enabled"]:
        return 0  # cc-baton toggle hib off — 자동 재우기만 멈춘다 (/sleep 수동 재우기는 그대로)
    th, why = idle_threshold()
    (STATE / "threshold.json").write_text(json.dumps({"idleMin": th, "why": why, "at": time.time()}))  # HUD 가 읽는다
    rows, lp, now = sessions()
    by_tty = {r["tty"]: r for r in rows}
    acted = []
    slept = set()          # 이번 tick 에 재운 tty — rows 는 tick 시작 시점 스냅샷이라
                           # 그대로 두면 방금 재운 세션에 다시 유예를 건다.

    # 1) 유예 중인 것 처리
    for f in sorted(PENDING.glob("*.json")):
        p = _load(f) or {}
        tty = p.get("tty")
        row = by_tty.get(tty)
        if not row or row["sid"] != p.get("sid"):
            f.unlink(missing_ok=True)                       # 세션이 사라짐
            continue
        if row["status"] != "idle" or row["idle_min"] < p.get("idleAtRequest", 0) - 0.5:
            f.unlink(missing_ok=True)                       # 사용자가 돌아옴 → 취소
            acted.append(L(f"{row['name']} grace cancelled (active again)", f"{row['name']} 유예 취소(활동 재개)"))
            continue
        if time.time() >= p.get("dueAt", 0):
            ok, msg = do_sleep(row, p.get("reason", ""))
            f.unlink(missing_ok=True)
            slept.add(row["tty"])
            acted.append(L(f"{row['name']} hibernated: {msg}", f"{row['name']} 재움 — {msg}"))
            notify(L("Session hibernated", "세션 재움"), f"{row['name']} · {row['cwd'].split('/')[-1]} — {msg}")

    # 2) 새 후보에 유예 걸기
    pending_ttys = {(_load(f) or {}).get("tty") for f in PENDING.glob("*.json")}
    for r in rows:
        if r["tty"] in pending_ttys or r["tty"] in slept:
            continue
        v, w = verdict(r, now, th)
        if v != "재우기":
            continue
        _save(PENDING / f"{r['tty']}.json", {
            "tty": r["tty"], "sid": r["sid"], "name": r["name"], "reason": w,
            "dueAt": time.time() + GRACE_SEC, "idleAtRequest": r["idle_min"]})
        acted.append(L(f"{r['name']} grace started (hibernates in {GRACE_SEC/60:.0f}m)", f"{r['name']} 유예 시작({GRACE_SEC/60:.0f}분 뒤 재움)"))
        notify(L("Session about to hibernate", "세션 재우기 예고"),
               f"{r['name']} · {r['cwd'].split('/')[-1]} — "
               + L(f"hibernates in {GRACE_SEC/60:.0f}m. Type anything in that tab to cancel.",
                   f"{GRACE_SEC/60:.0f}분 뒤 재웁니다. 그 탭에서 아무거나 입력하면 취소됩니다."))
    snapshot(rows)

    # 소비되지 않은 재우기 요청 정리. 옛 래퍼 탭은 claim 을 호출하지 않아 그냥 남는다.
    for f in REQ.glob("*.json"):
        d = _load(f) or {}
        if time.time() - d.get("hibernatedAt", 0) > REQ_TTL:
            f.unlink(missing_ok=True)

    for a in acted:
        print(a)
    if not acted:
        print(DIM + L(f"no change (threshold {th:.0f}m", f"변화 없음 (임계 {th:.0f}분") + (f", {why}" if why else "")
              + L(f", {len(rows)} sessions)", f", 세션 {len(rows)}개)") + R)


def ancestors():
    """나부터 위로 올라가며 만나는 pid 들. 내가 속한 세션을 찾는 데 쓴다."""
    out, pid = [], os.getpid()
    for _ in range(12):
        out.append(pid)
        o = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                           capture_output=True, text=True).stdout.strip()
        if not o or o == "0":
            break
        pid = int(o)
    return out


def resolve(token, rows):
    """pid · 이름 · 생략(내가 지금 들어있는 세션) 셋 다 받는다."""
    if token and token.isdigit():
        return next((r for r in rows if r["pid"] == int(token)), None), token
    if token:
        hit = [r for r in rows if r["name"] == token]
        if not hit:
            hit = [r for r in rows if str(r["name"]).startswith(token)]
        return (hit[0] if len(hit) == 1 else None), token
    # 생략 → 자기 자신. CLAUDE_PID 가 가장 정확하고, 없으면 조상 체인을 훑는다.
    env = os.environ.get("CLAUDE_PID")
    if env and env.isdigit():
        r = next((r for r in rows if r["pid"] == int(env)), None)
        if r:
            return r, "self"
    anc = set(ancestors())
    return next((r for r in rows if r["pid"] in anc), None), "self"


def cmd_sleep(argv):
    """지금 당장 재운다. 유예도 알림도 없다 — 명시적으로 시킨 것이니까."""
    force = "--force" in argv
    dry = "--dry" in argv
    allmode = "--all" in argv
    pos = [a for a in argv if not a.startswith("-")]
    rows, lp, now = sessions()
    me = os.environ.get("CLAUDE_PID")
    me = int(me) if me and me.isdigit() else None

    if allmode:
        th = float(pos[0]) if pos and pos[0].isdigit() else idle_threshold()[0]
        targets, skipped = [], []
        for r in sorted(rows, key=lambda x: -x["idle_min"]):
            if r["status"] != "idle" or r["idle_min"] < th:
                continue
            b = blockers(tail_records(r["transcript"]), now) if r["transcript"] else [L("no transcript", "트랜스크립트 없음")]
            if b and not force:
                skipped.append((r, ", ".join(b))); continue
            if not cap_ok(r["tty"]) and not force:
                skipped.append((r, L("tab not running baton (would drop to the shell)", "래퍼 미지원 — 탭이 프롬프트로 떨어짐"))); continue
            targets.append(r)
        if not targets:
            print(DIM + L(f"No sessions idle for {th:.0f}m or more.", f"유휴 {th:.0f}분 이상인 재울 대상이 없습니다.") + R)
        for r in targets:
            if dry:
                print(f"{DIM}[dry]{R} {r['name']} ({r['pid']}, {r['rss']}MB, " + L(f"idle {r['idle_min']:.0f}m)", f"유휴 {r['idle_min']:.0f}분)"))
                continue
            ok, msg = do_sleep(r, L(f"manual batch {th:.0f}m", f"수동 일괄 {th:.0f}분"), selfkill=(r["pid"] == me))
            print(f"{GRN}✓{R} {r['name']} — {msg}" if ok else f"{RED}✗{R} {r['name']}: {msg}")
        if targets and not dry:
            print("\n" + L(f"{len(targets)} hibernated · freed {sum(r['rss'] for r in targets)}MB",
                   f"{len(targets)}개 재움 · {sum(r['rss'] for r in targets)}MB 회수"))
        for r, why in skipped:
            print(DIM + L(f"skipped {r['name']}: {why}", f"건너뜀 {r['name']} — {why}") + R)
        if skipped and not force:
            print(DIM + L("add --force to include them", "포함하려면 --force") + R)
        return 0

    row, token = resolve(pos[0] if pos else None, rows)
    if not row:
        if token == "self":
            print(f"{RED}✗{R} " + L("Couldn't find the current session. Give a pid or a name.", "지금 들어있는 세션을 못 찾았습니다. pid 나 이름을 주세요."), file=E)
        else:
            print(f"{RED}✗{R} " + L(f"No session matches '{token}'. Check with cc-baton hib scan.", f"'{token}' 에 맞는 세션이 없습니다. cc-baton hib scan 으로 확인하세요."), file=E)
        return 1

    b = blockers(tail_records(row["transcript"]), now) if row["transcript"] else [L("no transcript", "트랜스크립트 없음")]
    if b and not force:
        print(f"{YEL}!{R} {row['name']}: {', '.join(b)} — " + L("hibernating now would lose it. Use --force to do it anyway",
                                                              "재우면 잃습니다. 그래도 재우려면 --force"), file=E)
        return 1
    if b:
        print(f"{YEL}!{R} " + L(f"going ahead despite: {', '.join(b)}", f"차단신호 무시하고 진행: {', '.join(b)}"), file=E)
    if not cap_ok(row["tty"]) and not force:
        print(f"{YEL}!{R} {row['name']}: " + L("this tab isn't running baton, so it will drop to the shell prompt.",
                                               "이 탭은 대기 배너를 못 띄웁니다(셸 프롬프트로 떨어짐)."), file=E)
        print(f"  {DIM}" + L("The conversation is safe; baton --wake brings it back. Use --force to go ahead",
                             "대화는 안전하고 baton --wake 로 돌아옵니다. 그래도 재우려면 --force") + R, file=E)
        return 1
    if dry:
        print(f"{DIM}[dry]{R} " + L(f"would hibernate {row['name']} ({row['pid']}, {row['rss']}MB)", f"{row['name']} ({row['pid']}, {row['rss']}MB) 를 재웁니다"))
        return 0

    selfk = (row["pid"] == me) or (row["pid"] in set(ancestors()))
    if selfk:
        print(f"{MAG}💤{R} " + L(f"Hibernating this session ({row['rss']}MB). ", f"이 세션을 재웁니다 — {row['rss']}MB. ")
              + DIM + L("Press any key in this tab or run baton --wake to come back", "깨우려면 이 탭에서 키를 누르거나 baton --wake") + R)
    ok, msg = do_sleep(row, L("manual", "수동"), selfkill=selfk)
    if not selfk:
        print(f"{GRN}✓{R} " + L(f"{row['name']} hibernated: {msg}", f"{row['name']} 재움 — {msg}") if ok else f"{RED}✗{R} {msg}")
    return 0 if ok else 1


def cmd_claim(argv):
    """cc 래퍼가 claude 종료 직후 호출. 내 tty 앞으로 온 요청이면 sid 를 뱉는다."""
    tty = my_tty()
    if not tty:
        return 1
    f = REQ / f"{tty}.json"
    d = _load(f)
    if not d:
        return 1
    f.unlink(missing_ok=True)
    if time.time() - d.get("hibernatedAt", 0) > REQ_TTL or not st.valid_sid(d.get("sid")):
        return 1                                            # 낡은 요청 — tty 재사용 사고 방지
    print(d["sid"])
    return 0


def cmd_banner(argv):
    sid = argv[0] if argv else ""
    d = (_load(PARKED / f"{sid}.json") or {}) if st.valid_sid(sid) else {}
    name = st.clean(d.get("name") or sid[:8])
    cwd = st.clean(d.get("cwd") or "")
    rss = d.get("rss")
    when = datetime.datetime.fromtimestamp(d.get("hibernatedAt", time.time())).strftime("%H:%M")
    # 탭 제목. claude 가 죽으면 탭이 'zsh' 로 돌아가 어느 세션인지 모른다. 깨우면 claude 가 다시 덮어쓴다.
    print(f"\033]0;⏾ {name}\007", end="")
    print()
    print(f"  {MAG}💤 " + L("Hibernated", "재움") + f"{R}  {BOLD}{name}{R}  {DIM}{cwd}{R}")
    print(f"  {DIM}{when} · " + L(f"freed {rss}MB · conversation kept as is", f"{rss}MB 회수 · 맥락은 그대로 보존됨") + R)
    print(f"  {CYA}" + L("Press any key to pick up where you left off", "아무 키나 누르면 이어서 시작합니다") + f"{R} {DIM}"
          + L("(Ctrl-C closes the tab)", "(Ctrl-C 로 탭 종료)") + R)
    print()
    return 0


def cmd_wake(argv):
    sid = argv[0] if argv else ""
    f = PARKED / f"{sid}.json"
    if not st.valid_sid(sid) or not f.exists():                                      # 다른 데서 이미 깨움
        print("  " + L("This session was already woken somewhere else.", "이 세션은 다른 곳에서 이미 깨어났습니다."))
        return 1
    f.unlink(missing_ok=True)
    return 0


def parked_list():
    """재운 세션 + 재부팅 등으로 끊긴 세션. 후자는 레지스트리에 죽은 pid 로 남는다."""
    out = []
    for f in sorted(PARKED.glob("*.json")):
        d = _load(f)
        if d:
            d["origin"] = "재움"
            out.append(d)
    known = {d["sid"] for d in out}
    lp = live_claude()
    for prof in profiles():
        for f in sorted((prof / "sessions").glob("*.json")):
            d = _load(f)
            if not isinstance(d, dict) or d.get("pid") in lp:
                continue
            sid = d.get("sessionId")
            if not sid or sid in known:
                continue
            out.append({"sid": sid, "cwd": d.get("cwd"), "name": d.get("name"),
                        "profile": str(prof), "account": account_of(prof),
                        "hibernatedAt": (d.get("statusUpdatedAt") or 0) / 1000,
                        "origin": "끊김", "rss": None})
    # 재부팅으로 끊긴 세션. 스냅샷에 있는데 지금 안 살아있고, 마지막으로 본 게
    # 이번 부팅보다 전이면 기계가 꺼질 때 같이 죽은 것이다.
    # (부팅 후에 사라진 건 사용자가 직접 닫은 것이므로 목록에 올리지 않는다.)
    boot = boot_epoch()
    if boot:
        live_sids = set()
        for prof in profiles():
            for f in (prof / "sessions").glob("*.json"):
                d = _load(f)
                if isinstance(d, dict) and d.get("pid") in lp:
                    live_sids.add(d.get("sessionId"))
        for sid, d in ((_load(LIVE, {}) or {}).get("sessions") or {}).items():
            if sid in known or sid in live_sids:
                continue
            if d.get("seenAt", 0) >= boot:
                continue                            # 이번 부팅 중에 닫힌 것 — 의도적 종료
            hits = sorted(Path(d["profile"]).glob(f"projects/*/{sid}.jsonl")) if d.get("profile") else []
            if not hits:
                continue                            # 트랜스크립트가 없으면 되살릴 것도 없다
            out.append({**d, "hibernatedAt": d.get("seenAt"), "origin": "끊김"})

    out.sort(key=lambda d: -(d.get("hibernatedAt") or 0))
    return out


def cmd_list(argv):
    items = parked_list()
    if "--json" in argv:
        print(json.dumps(items, ensure_ascii=False))
        return 0
    if not items:
        print(DIM + L("No hibernated sessions", "재운 세션 없음") + R)
        return 0
    print(f"{'':>3} {L('from', '출처'):<5} {L('when', '언제'):<7} {'name':<15} {L('acct', '계정'):<4} cwd")
    print("─" * 92)
    for i, d in enumerate(items, 1):
        when = (datetime.datetime.fromtimestamp(d["hibernatedAt"]).strftime("%m-%d %H:%M")
                if d.get("hibernatedAt") else "—")
        c = MAG if d["origin"] == "재움" else YEL
        print(f"{i:>3} {c}{otxt(d['origin']):<5}{R} {when:<12} {st.clean(d.get('name'))[:15]:<15} "
              f"{str(d.get('account') or '-'):<4} {st.clean(d.get('cwd'))}")
    return 0


def cmd_pick(argv):
    """피커. 선택 결과를 'account\\tsid\\tcwd' 로 stdout 에 뱉는다(화면은 stderr)."""
    items = parked_list()
    if not items:
        print(DIM + L("No hibernated sessions.", "재운 세션이 없습니다.") + R, file=E)
        return 1
    print(f"\n  {BOLD}" + L("Hibernated sessions", "재운 세션") + f"{R}\n", file=E)
    for i, d in enumerate(items, 1):
        when = (datetime.datetime.fromtimestamp(d["hibernatedAt"]).strftime("%m-%d %H:%M")
                if d.get("hibernatedAt") else "—")
        c = MAG if d["origin"] == "재움" else YEL
        print(f"   {BOLD}{i}{R}) {c}{otxt(d['origin'])}{R} {DIM}{when}{R}  "
              f"{BOLD}{st.clean(d.get('name'))[:18]}{R}  {DIM}{st.clean(d.get('cwd'))}{R}", file=E)
    print("\n  " + L("Pick a number (Enter to cancel): ", "번호 선택 (Enter 취소): "), end="", file=E, flush=True)
    try:
        s = input().strip()
    except (EOFError, KeyboardInterrupt):
        print(file=E)
        return 1
    if not s.isdigit() or not (1 <= int(s) <= len(items)):
        return 1
    d = items[int(s) - 1]
    (PARKED / f"{d['sid']}.json").unlink(missing_ok=True)
    print(f"{d.get('account') or ''}\t{d['sid']}\t{d.get('cwd') or ''}")
    return 0


def cmd_resume(argv):
    """마커를 걷고 그 자리에서 세션을 되살린다. 목록 번호나 세션 ID 앞부분을 받는다."""
    if not argv:
        print(L("usage: cc-baton hib resume <number|session id>", "사용법: cc-baton hib resume <번호|세션ID>"), file=E)
        return 1
    token = argv[0]
    items = [x for x in parked_list() if st.valid_sid(x.get("sid"))]
    d = None
    if token.isdigit() and 1 <= int(token) <= len(items):
        d = items[int(token) - 1]
    else:
        hit = [x for x in items if x["sid"].startswith(token)]
        d = hit[0] if len(hit) == 1 else None
    if not d:
        print(f"{RED}✗{R} " + L(f"No session matches '{token}'. Check with cc-baton hib list.", f"'{token}' 에 맞는 세션이 없습니다. cc-baton hib list 로 확인하세요."), file=E)
        return 1
    marker = PARKED / f"{d['sid']}.json"
    saved = _load(marker)
    marker.unlink(missing_ok=True)
    cwd = d.get("cwd")
    if cwd and os.path.isdir(cwd):
        os.chdir(cwd)
    acct = d.get("account")
    print(f"{MAG}↻{R} " + L(f"restoring {d.get('name')}", f"{d.get('name')} 복원") + f" — {DIM}{cwd}{R}")
    try:
        if acct:
            # 셸 문자열에 끼워 넣지 않고 인자로 넘긴다 (세션 ID·계정이 셸 코드로 해석되지 않게). baton 루프 안에서 이어져야
            # 다시 스왑·절전이 된다.
            os.execvp("zsh", ["zsh", "-ic", 'baton "$1" -- --resume "$2"', "zsh", str(acct), d["sid"]])
        else:
            os.execvp("claude", ["claude", "--resume", d["sid"]])
    except OSError as e:
        if saved:
            _save(marker, saved)                # 못 띄웠으면 목록에 도로 올려둔다
        print(f"{RED}✗{R} " + L(f"failed to run: {e}", f"실행 실패: {e}"), file=E)
        return 1


def cmd_restore(argv):
    """재부팅 뒤처럼 여러 개를 한 번에 되살릴 때. 세션 하나당 탭 하나가 필요하다."""
    items = parked_list()
    if not items:
        print(DIM + L("Nothing to restore.", "되살릴 세션이 없습니다.") + R)
        return 0
    lines = [f"cc-baton hib resume {i}" for i in range(1, len(items) + 1)]
    if "--script" in argv:
        i = argv.index("--script")
        path = argv[i + 1] if i + 1 < len(argv) else "restore-sessions.sh"
        body = "#!/bin/zsh\n# " + L("Each session needs its own tab. Run one line per tab.", "세션 하나당 탭 하나가 필요합니다. 아래를 각 탭에서 한 줄씩 실행하세요.") + "\n"
        for d, ln in zip(items, lines):
            body += f"# {st.clean(d.get('name'))}  {st.clean(d.get('cwd'))}\n# {ln}\n"  # 줄바꿈이 명령 줄이 되지 않게
        pathlib_write = Path(path)
        pathlib_write.write_text(body)
        os.chmod(path, 0o755)
        print(f"{GRN}✓{R} " + L(f"wrote {len(items)} restore command(s) to {path}", f"{path} 에 {len(items)}개 복원 명령을 적었습니다"))
        return 0

    print(f"\n  {BOLD}" + L(f"{len(items)} session(s) to restore", f"되살릴 세션 {len(items)}개") + f"{R}  "
          + DIM + L("— each needs its own tab", "— 세션 하나당 탭 하나가 필요합니다") + f"{R}\n")
    for i, (d, ln) in enumerate(zip(items, lines), 1):
        when = (datetime.datetime.fromtimestamp(d["hibernatedAt"]).strftime("%m-%d %H:%M")
                if d.get("hibernatedAt") else "—")
        c = MAG if d["origin"] == "재움" else YEL
        print(f"   {c}{otxt(d['origin'])}{R} {DIM}{when}{R}  {BOLD}{st.clean(d.get('name'))[:16]:<16}{R} "
              f"{DIM}{st.clean(d.get('cwd'))}{R}")
        print(f"     {CYA}{ln}{R}\n")
    print(f"  {DIM}" + L("Open a new tab for each line and run it. Any order works.", "탭을 새로 열고 각 줄을 실행하세요. 순서는 상관없습니다.") + R)
    print(f"  {DIM}" + L("To save them to a file: cc-baton hib restore --script ~/restore.sh",
                         "파일로 받으려면: cc-baton hib restore --script ~/restore.sh") + f"{R}\n")
    return 0


def cmd_clean(argv):
    lp = live_claude()
    n = 0
    for f in list(REQ.glob("*.json")) + list(PENDING.glob("*.json")):
        d = _load(f) or {}
        age = time.time() - (d.get("hibernatedAt") or d.get("dueAt", 0) - GRACE_SEC)
        if age > 3600:
            f.unlink(missing_ok=True); n += 1
    print(L(f"cleaned {n}", f"{n}개 정리"))
    return 0


def main():
    st.private_dir(st.STATE_DIR)
    st.private_dir(STATE)
    for d in (REQ, PARKED, PENDING, CAP):
        d.mkdir(parents=True, exist_ok=True)
    cmds = {"scan": cmd_scan, "tick": cmd_tick, "sleep": cmd_sleep, "claim": cmd_claim,
            "banner": cmd_banner, "wake": cmd_wake, "list": cmd_list, "pick": cmd_pick,
            "clean": cmd_clean, "cap": cmd_cap, "resume": cmd_resume, "restore": cmd_restore}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(L("usage: cc-baton hib scan | tick | sleep [target] [--all [min]] [--dry] [--force] | wake <sid> | list | "
                "resume <n|sid> | restore [--script P] | pick | clean",
                "사용법: cc-baton hib scan | tick | sleep [대상] [--all [분]] [--dry] [--force] | wake <sid> | list | "
                "resume <번호|sid> | restore [--script P] | pick | clean"), file=E)
        return 2
    r = cmds[sys.argv[1]](sys.argv[2:])
    return 0 if r is None or r is True else (r if isinstance(r, int) else 0)


def cli():
    return main()
