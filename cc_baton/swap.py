#!/usr/bin/env python3
"""세션 중 계정 스왑 — 컨텍스트는 유지하고, 히스토리 격리는 깨지 않는다.

cswap이 주는 선택지는 '히스토리 전부 공유(--share-history)' 아니면 '완전 격리' 둘뿐이다.
이 스크립트가 그 사이를 메운다: 스왑하는 그 대화 1건의 트랜스크립트만 대상 프로필로
복사하고 --resume 한다. 나머지 대화와 프롬프트 기록(history.jsonl)은 계정별로 남는다.

핵심 타이밍: 복사는 claude가 종료된 뒤(consume 시점)에 한다. 요청 시점에 복사하면
마지막 몇 턴이 아직 flush 되지 않아 잘린 대화를 넘기게 된다.

서브커맨드:
  request <계정> [--yes] [--sid S]   세션 안에서 호출. 마커만 남긴다.
  consume                  래퍼가 claude 종료 후 호출. 핸드오프 복사 + '계정번호/세션ID' 출력.
  handoff <계정> [--sid S] 수동 경로용. 지금 즉시 복사만 한다.
  hook prompt              UserPromptSubmit 훅. `/swap …` 을 모델 호출 없이 처리하고 프롬프트를 막는다.
  hook limit               StopFailure(rate_limit) 훅. 한도에 걸린 순간 스왑을 자동 예약한다.

훅 두 개가 있는 이유: /swap 이 슬래시커맨드(=모델에게 "스크립트 실행해라"는 프롬프트)로만 있으면,
정작 5h 한도에 걸렸을 때 그 모델 호출이 429 로 죽어서 스왑 자체를 못 한다. 훅은 모델 앞에서 돈다.
한도 훅의 정책(교차 승인·여유 기준)은 ~/.config/cc-baton/config.json 의 "onLimit" 이 원본이다.
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from . import state as st
from .i18n import L

R, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
RED, YEL, GRN, CYA = "\033[38;5;196m", "\033[38;5;208m", "\033[38;5;42m", "\033[38;5;39m"
MARKER_TTL = 3600.0  # 이보다 오래된 요청은 무시 (예전 마커가 뒤늦게 발화하는 사고 방지)
ANSI_RE = re.compile(r"\033\[[0-9;]*m")
# 한 줄짜리 `/swap …` 만. 여러 줄 프롬프트의 뒷부분(붙여 넣은 글 속 --yes 등)이 인자로 섞이지 않게.
SWAP_CMD_RE = re.compile(r"^/swap(?:[ \t]+([^\n]*))?$")
# 계정 전체 한도("You've hit your session/weekly limit")가 아니라 모델별 한도
# ("You've reached your Fable 5 limit. /model to switch models.")면 스왑이 아니라 /model 이 맞다.
MODEL_SCOPED_RE = re.compile(r"reached your .+? limit", re.I)


def onlimit_cfg():
    """config.json 의 "onLimit". 기본값은 state.FEATURE_DEFAULTS (HUD 와 공유)."""
    return st.feature("onLimit")


def _pop_flag(argv, name):
    """argv 에서 `name VALUE` 를 떼어낸다 → (남은 argv, VALUE|None)."""
    argv = list(argv)
    val = None
    if name in argv:
        i = argv.index(name)
        val = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    return argv, val


def die(msg, code=1):
    print(f"{RED}✗{R} {msg}", file=sys.stderr)
    sys.exit(code)


def current_profile():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(cfg) if cfg else Path.home() / ".claude"


def find_transcript(profile, sid):
    if not st.valid_sid(sid):
        return None
    if not sid:
        return None
    hits = sorted(Path(profile).glob(f"projects/*/{sid}.jsonl"))
    return hits[0] if hits else None


def do_copy(src, dst_profile):
    """트랜스크립트 1개를 대상 프로필의 같은 슬러그 아래로 복사한다.

    옆의 <sid>/ 폴더(떼어 저장한 큰 도구 출력, 서브에이전트 기록)도 같이 옮겨야 대화가 온전하다.
    """
    slug = src.parent.name
    dst_dir = Path(dst_profile) / "projects" / slug
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copy2(src, dst)
    side = src.with_suffix("")
    if side.is_dir():
        shutil.copytree(side, dst_dir / side.name, dirs_exist_ok=True)
    return dst


def cmd_request(argv, sid_override=None):
    argv, sid_flag = _pop_flag(argv, "--sid")
    yes = "--yes" in argv or "-y" in argv
    rest = [a for a in argv if not a.startswith("-")]
    if not rest:
        accs = st.accounts()
        if not accs:
            print(L("No accounts registered in claude-swap yet. In a terminal, while logged in to Claude Code: cswap add",
                    "claude-swap 에 등록된 계정이 없습니다. Claude Code 에 로그인한 상태로 터미널에서: cswap add"))
            return 0
        cur = st.identity()["num"] or st.current_num()
        now_label = st.resolve(cur)['label'] if st.resolve(cur) else cur
        print(BOLD + L("Accounts", "계정 목록") + R + "  " + L(f"(current: {now_label})", f"(현재: {now_label})"))
        for a in accs:
            k = st.group(a["num"])
            mark = "●" if a["num"] == cur else " "
            print(f"  {mark} {a['num']}) {a['label']:<12} [{k}]  {DIM}{a['email']}{R}")
        print("\n" + L("Usage: ", "사용법: ") + BOLD + L("/swap <number|alias>", "/swap <번호|별칭>") + R)
        return 0

    target = st.resolve(rest[0])
    if not target:
        die(L(f"No account '{rest[0]}'. Run `/swap` alone to list accounts.", f"'{rest[0]}' 계정을 찾지 못했습니다. `/swap` 만 입력하면 목록이 나옵니다."))
    cur = st.identity()["num"] or st.current_num()
    if target["num"] == cur:
        die(L(f"Already on {target['label']}.", f"이미 {target['label']} 계정입니다."))

    sid = sid_override or sid_flag or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    src_profile = current_profile()
    dst_profile = st.profile_dir(target["num"])
    if not dst_profile:
        die(L(f"Couldn't find the profile folder for {target['label']}.", f"{target['label']} 의 프로필 경로를 찾지 못했습니다."))

    cross = st.crosses_boundary(cur, target["num"])
    if cross and not yes:
        ck, tk = st.group(cur), st.group(target["num"])
        tl = f"{BOLD}{target['label']}{R}"
        print(YEL + L(f"⚠ This swap crosses groups: {ck.upper()} → {tk.upper()}", f"⚠ 그룹이 다른 계정으로 옮깁니다: {ck.upper()} → {tk.upper()}") + R)
        print(L(f"  The whole transcript of this conversation will be copied to {tl} ({tk}),",
                f"  이 대화의 트랜스크립트 전체가 {tl}({tk}) 프로필로 복사되고,"))
        print(L(f"  and further requests will count against {tl}'s usage.", f"  이후 요청은 {tl} 계정의 사용량으로 처리됩니다."))
        print(L("  Make sure it's OK to move this conversation between these accounts (e.g. work ↔ personal).\n",
                "  업무 대화를 개인 계정으로(또는 그 반대로) 옮겨도 되는지 확인하세요.\n"))
        print(L("  To go ahead: ", "  진행하려면: ") + f"{BOLD}/swap {rest[0]} --yes{R}")
        return 2

    if sid and not st.valid_sid(sid):
        die(L(f"Invalid session id: {sid!r}", f"세션 ID 가 올바르지 않습니다: {sid!r}"))
    st.private_dir(st.STATE_DIR)
    st.MARKER.write_text(json.dumps({
        "targetNum": target["num"], "targetLabel": target["label"],
        "fromNum": cur, "sid": sid,
        "srcProfile": str(src_profile), "dstProfile": str(dst_profile),
        "crossed": cross, "requestedAt": time.time(),
    }, ensure_ascii=False, indent=2))

    if not os.environ.get("CC_SWAP_LOOP"):
        st.MARKER.unlink(missing_ok=True)
        print(f"{YEL}!{R} " + L("Not running inside `baton`, so it can't relaunch for you. After exiting, run:\n",
                                "`baton` 으로 연 세션이 아니라 자동으로 다시 열 수 없습니다. 종료 후 아래를 실행하세요:\n"))
        if sid:
            print(f"  {BOLD}cc-baton swap handoff {target['num']} --sid {sid} --src-profile {src_profile} && \\")
            print(f"  cc-baton cswap run {target['num']} -- --resume {sid}{R}")
        else:  # 세션 ID 를 모르면 대화를 옮길 수 없다. 새 대화로 연다
            print(f"  {BOLD}cc-baton cswap run {target['num']}{R}  {DIM}"
                  + L("(session id unknown, so this starts a new conversation)", "(세션 ID 를 몰라 새 대화로 엽니다)") + R)
        return 3

    print(f"{GRN}✓{R} " + L("Next account: ", "다음 계정: ") + f"{BOLD}{target['label']}{R} [{st.group(target['num'])}]"
          + (f"  {YEL}" + L("(group crossing approved)", "(그룹 교차 승인됨)") + R if cross else ""))
    print(f"  {DIM}" + L(f"Press Ctrl+D to continue this exact conversation on {target['label']}.",
                         f"Ctrl+D 를 누르면 이 대화 그대로 {target['label']} 계정에서 이어집니다.") + R)
    return 0


def cmd_consume(argv):
    """래퍼 전용. stdout 은 '<계정번호>\\t<세션ID>' 한 줄 (세션ID가 비면 resume 없이 새 세션).

    예약 파일은 믿지 않는다: 대상 프로필은 계정 번호로 다시 계산하고, 출발 프로필은 claude-swap 이
    관리하는 폴더일 때만, 세션 ID 는 형식이 맞을 때만 쓴다. 깨진 예약 파일은 조용히 버린다.
    """
    if not st.MARKER.exists():
        return 1
    try:
        req = json.loads(st.MARKER.read_text())
        requested = float(req.get("requestedAt") or 0)
        target_num = str(req["targetNum"])
    except Exception:
        st.MARKER.unlink(missing_ok=True)
        return 1
    st.MARKER.unlink(missing_ok=True)
    if time.time() - requested > MARKER_TTL:
        print(DIM + L("[cc-baton] Ignored a stale swap request.", "[cc-baton] 오래된 스왑 요청을 무시했습니다.") + R, file=sys.stderr)
        return 1
    target = st.resolve(target_num)
    dst_profile = st.profile_dir(target_num) if target else None
    if not dst_profile:
        print(YEL + L(f"![cc-baton] Account {target_num} is gone; not switching.", f"![cc-baton] {target_num}번 계정이 없어 전환하지 않습니다.") + R,
              file=sys.stderr)
        return 1

    sid = str(req.get("sid") or "")
    src_profile = str(req.get("srcProfile") or "")
    src = find_transcript(src_profile, sid) if st.is_profile_dir(src_profile) else None
    if src:
        try:
            dst = do_copy(src, dst_profile)
            size = dst.stat().st_size
            print(f"{CYA}→{R} " + L("Moving conversation: ", "대화 이관: ") + f"{DIM}{src.name[:8]}… ({size // 1024}KB) → "
                  + L(f"{target['label']} profile", f"{target['label']} 프로필") + R, file=sys.stderr)
        except Exception as exc:
            print(YEL + L(f"![cc-baton] Couldn't copy the transcript ({exc}); switching without resuming.",
                          f"![cc-baton] 트랜스크립트를 복사하지 못해({exc}) 이어 가지 않고 전환합니다.") + R,
                  file=sys.stderr)
            sid = ""
    else:
        print(YEL + L("![cc-baton] Couldn't find the transcript; switching without resuming.",
                      "![cc-baton] 트랜스크립트를 찾지 못해 이어 가지 않고 전환합니다.") + R, file=sys.stderr)
        sid = ""

    print(f"{target_num}\t{sid}")
    return 0


def cmd_handoff(argv):
    rest = [a for a in argv if not a.startswith("-")]
    if not rest:
        die(L("usage: cc-baton swap handoff <account> [--sid S] [--src-profile P]", "사용법: cc-baton swap handoff <계정> [--sid S] [--src-profile P]"))
    target = st.resolve(rest[0]) or die(L(f"No account '{rest[0]}'", f"'{rest[0]}' 계정 없음"))
    sid = ""
    src_profile = str(current_profile())
    for i, a in enumerate(argv):
        if a == "--sid" and i + 1 < len(argv):
            sid = argv[i + 1]
        if a == "--src-profile" and i + 1 < len(argv):
            src_profile = argv[i + 1]
    sid = sid or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    src = find_transcript(src_profile, sid)
    if not src:
        die(L(f"No transcript for session {sid or '(none)'} in {src_profile}", f"세션 {sid or '(미지정)'} 의 트랜스크립트를 {src_profile} 에서 못 찾음"))
    dst = do_copy(src, st.profile_dir(target["num"]))
    print(f"{GRN}✓{R} {dst}")
    return 0


# --- Claude Code 훅 ---------------------------------------------------------
# 훅은 모델 호출 앞(또는 실패 직후)에 로컬에서 돈다. stdin 으로 훅 JSON(session_id 포함)을 받는다.

def _run_captured(fn, *a, **kw):
    """cmd_* 를 실행해 (rc, ANSI 뺀 출력) 을 돌려준다. die() 의 sys.exit 도 흡수한다."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = fn(*a, **kw) or 0
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, ANSI_RE.sub("", buf.getvalue()).strip()


def hook_prompt(data):
    """UserPromptSubmit. `/swap …` 이면 여기서 처리하고 exit 2 로 프롬프트를 막는다.

    exit 2 = 프롬프트 차단 + stderr 를 사용자에게 표시 + 모델 호출 0회. 그래서 한도에 걸린
    세션에서도 동작한다. /swap 이 아니면 exit 0 으로 그냥 통과.
    """
    m = SWAP_CMD_RE.match((data.get("prompt") or "").strip())
    if not m:
        return 0
    args = (m.group(1) or "").split()
    _, out = _run_captured(cmd_request, args, sid_override=data.get("session_id") or "")
    print(out or L("[cc-baton] (no output)", "[cc-baton] (출력 없음)"), file=sys.stderr)
    return 2


def _five_hour_pct(num):
    good = st.usage(num)
    return ((good or {}).get("five_hour") or {}).get("pct") if good else None


def pick_limit_target(cur, cfg):
    """여유가 가장 큰 다른 계정. 5h 여유가 minHeadroomPct 미만이면 제외, 측정 없는 계정은 최후순위."""
    cands = []
    for a in st.accounts():
        if a["num"] == cur:
            continue
        pct = _five_hour_pct(a["num"])
        if pct is not None and 100 - pct < cfg["minHeadroomPct"]:
            continue
        cands.append((pct is None, pct if pct is not None else 0.0, a))
    cands.sort(key=lambda t: (t[0], t[1]))
    return cands[0][2] if cands else None


def _emit(msg):
    """훅 JSON 출력. systemMessage 는 모든 훅에서 사용자에게 그대로 표시된다."""
    print(json.dumps({"systemMessage": msg}, ensure_ascii=False))


def hook_limit(data):
    """StopFailure(matcher=rate_limit). 한도에 걸린 순간 스왑을 예약해 Ctrl+D 만 누르면 되게 한다.

    Claude Code 는 429 를 error="rate_limit" 인 API 에러 메시지로 바꾸고 그때 StopFailure 를 쏜다.
    항상 exit 0 — 여기서 2 를 내면 Stop 계열 의미(모델 재호출)로 흐를 수 있어 절대 쓰지 않는다.
    """
    if data.get("error") != "rate_limit":
        return 0
    cfg = onlimit_cfg()
    if not cfg["enabled"]:
        return 0
    msg = (data.get("last_assistant_message") or "").strip()
    if MODEL_SCOPED_RE.search(msg):
        _emit(L(f"[cc-baton] This is a per-model limit: {msg} Try /model first. To swap accounts anyway: /swap <account> --yes",
                f"[cc-baton] 모델별 한도입니다: {msg} 계정보다 /model 로 모델을 먼저 바꿔 보세요. 그래도 계정을 바꾸려면 /swap <계정> --yes"))
        return 0

    cur = st.identity()["num"] or st.current_num()
    if st.MARKER.exists():
        try:
            req = json.loads(st.MARKER.read_text())
            if time.time() - (req.get("requestedAt") or 0) <= MARKER_TTL:
                _emit(L(f"[cc-baton] Already scheduled to {req.get('targetLabel')}. Press Ctrl+D to continue this conversation there.",
                        f"[cc-baton] 이미 {req.get('targetLabel')} 계정으로 예약돼 있습니다. Ctrl+D 를 누르면 이 대화 그대로 이어집니다."))
                return 0
        except Exception:
            pass

    target = pick_limit_target(cur, cfg)
    if not target:
        _emit(L("[cc-baton] Rate limit reached. No other account has room left; wait for the reset or pick one with /swap.",
                "[cc-baton] 한도에 도달했습니다. 여유 있는 다른 계정이 없으니 리셋을 기다리거나 /swap 으로 직접 고르세요."))
        return 0
    tk = st.group(target["num"])
    cross = st.crosses_boundary(cur, target["num"])
    if cross and not cfg["approveCrossing"]:
        _emit(L(f"[cc-baton] Rate limit reached. Moving to {target['label']}[{tk}] crosses groups and needs your OK: ",
                f"[cc-baton] 한도에 도달했습니다. {target['label']}[{tk}] 는 그룹이 달라 승인이 필요합니다: ")
              + f"/swap {target['num']} --yes")
        return 0

    rc, out = _run_captured(cmd_request, [target["num"], "--yes"],
                            sid_override=data.get("session_id") or "")
    pct = _five_hour_pct(target["num"])
    head = (L("[cc-baton] Rate limit reached → ", "[cc-baton] 한도 도달 → ") + f"{target['label']}[{tk}]"
            + (L(f" (5h {pct:.0f}% used)", f" (5h {pct:.0f}% 사용)") if pct is not None else ""))
    if rc == 0:
        _emit(head + L(" scheduled. Press Ctrl+D to continue this conversation there.", " 로 예약했습니다. Ctrl+D 를 누르면 이 대화 그대로 이어집니다.")
              + (L(" · your settings allow moving to another group", " · 설정에서 다른 그룹으로 옮기는 것을 허용해 두었습니다") if cross else ""))
    elif rc == 3:
        _emit(head + L(f": not inside `baton`, so it can't relaunch for you. After exiting, run:\n{out}",
                       f" — baton 으로 연 세션이 아니라 자동으로 다시 열 수 없습니다. 종료 후 직접 실행하세요:\n{out}"))
    else:
        _emit(head + L(f": scheduling failed (rc={rc}).\n{out}", f" 예약에 실패했습니다(rc={rc}).\n{out}"))
    return 0


def _hook_log(kind, data, rc):
    """훅이 실제로 돌았는지 나중에 확인할 흔적. 실패해도 훅을 죽이지 않는다."""
    try:
        st.private_dir(st.STATE_DIR)
        head = (data.get("prompt") or data.get("last_assistant_message") or "")[:80].replace("\n", " ")
        with (st.STATE_DIR / "hook.log").open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {kind} rc={rc} error={data.get('error')} "
                    f"sid={(data.get('session_id') or '')[:8]} cfg={os.environ.get('CLAUDE_CONFIG_DIR', '~/.claude')} "
                    f"| {head}\n")
    except Exception:
        pass


def cmd_hook(argv):
    kind = argv[0] if argv else ""
    if kind not in ("prompt", "limit"):  # stdin 을 기다리기 전에 사용법부터
        die(L("usage: cc-baton swap hook <prompt|limit>   (hook JSON on stdin)", "사용법: cc-baton swap hook <prompt|limit>   (stdin 으로 훅 JSON)"))
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    if kind == "prompt":
        rc = hook_prompt(data)
        if rc:  # /swap 이 아닌 일반 프롬프트는 기록하지 않는다
            _hook_log(kind, data, rc)
        return rc
    if kind == "limit":
        rc = hook_limit(data)
        _hook_log(kind, data, rc)
        return rc
    die(L("usage: cc-baton swap hook <prompt|limit>   (hook JSON on stdin)", "사용법: cc-baton swap hook <prompt|limit>   (stdin 으로 훅 JSON)"))


def main():
    if len(sys.argv) < 2:
        print(L("usage: cc-baton swap request <account> [--yes] | consume | handoff <account> | hook prompt|limit",
                "사용법: cc-baton swap request <계정> [--yes] | consume | handoff <계정> | hook prompt|limit"))
        return 0
    cmd, argv = sys.argv[1], sys.argv[2:]
    return {"request": cmd_request, "consume": cmd_consume, "handoff": cmd_handoff,
            "hook": cmd_hook}.get(cmd, lambda a: die(L(f"Unknown subcommand: {cmd}", f"알 수 없는 서브커맨드: {cmd}")))(argv)


def cli():
    return main() or 0
