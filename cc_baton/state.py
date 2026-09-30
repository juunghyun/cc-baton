"""claude-swap 상태 읽기 전용 접근자 — cswap을 import하지 않고, 네트워크도 안 탄다.

cswap의 온디스크 포맷(sequence.json / mappings.json / cache/usage.json)만 읽는다.
포맷이 바뀌면 조용히 None/빈값으로 떨어지는 게 계약이다 — 호출자(statusline)는
절대 죽으면 안 되므로.
"""
import json
import os
import re
import shutil
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

BACKUP = Path.home() / ".claude-swap-backup"
SEQ_PATH = BACKUP / "sequence.json"
MAP_PATH = BACKUP / "mappings.json"
USAGE_PATH = BACKUP / "cache" / "usage.json"
SESSIONS_DIR = BACKUP / "sessions"
CSWAP_BIN = Path.home() / ".local" / "bin" / "cswap"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "cc-swap"
MARKER = STATE_DIR / "request.json"
REFRESH_STAMP = STATE_DIR / "usage-refresh.stamp"


def _load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def sequence():
    return _load(SEQ_PATH, {})


def accounts():
    """슬롯 순서대로 [{num, email, alias, label}]."""
    seq = sequence()
    table = seq.get("accounts", {}) or {}
    order = seq.get("sequence") or sorted((int(k) for k in table), key=int)
    out = []
    for n in order:
        a = table.get(str(n))
        if not a:
            continue
        alias = a.get("alias") or ""
        out.append({
            "num": str(n),
            "email": a.get("email", ""),
            "alias": alias,
            "label": alias or a.get("email", ""),
        })
    return out


def active_num():
    """기본 로그인(~/.claude)이 들고 있는 계정 번호."""
    v = sequence().get("activeAccountNumber")
    return str(v) if v is not None else ""


def resolve(token):
    """번호/별칭/이메일(부분) → 계정 dict. 못 찾으면 None."""
    if not token:
        return None
    t = str(token).strip().lower()
    accs = accounts()
    for a in accs:
        if a["num"] == t:
            return a
    for a in accs:
        if a["alias"].lower() == t:
            return a
    hits = [a for a in accs if t in a["email"].lower()]
    return hits[0] if len(hits) == 1 else None


def slugify_email(email):
    """claude_swap.session.slugify_email 과 동일 규칙(프로필 디렉토리명 산출)."""
    n = unicodedata.normalize("NFC", email or "")
    return "".join(c if (c.isascii() and (c.isalnum() or c in "._-")) else "_" for c in n)


def profile_dir(num):
    """해당 계정의 CLAUDE_CONFIG_DIR.

    기본 로그인 계정은 cswap fast path 때문에 세션 프로필이 아니라 ~/.claude 를 쓴다.
    """
    num = str(num)
    if num == active_num():
        return Path.home() / ".claude"
    for a in accounts():
        if a["num"] == num:
            return SESSIONS_DIR / f"{num}-{slugify_email(a['email'])}"
    return None


def current_num():
    """지금 이 프로세스가 어느 계정으로 돌고 있는지."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        m = re.match(r"(\d+)-", Path(cfg).name)
        if m:
            return m.group(1)
    return active_num()


def usage(num):
    """마지막 성공 측정치 + 나이(초). 측정 이력이 없으면 None."""
    entry = (_load(USAGE_PATH, {}).get("accounts", {}) or {}).get(str(num))
    if not entry or not entry.get("lastGood"):
        return None
    good = dict(entry["lastGood"])
    good["_age"] = time.time() - (entry.get("fetchedAt") or 0)
    good["_error"] = entry.get("lastError")
    return good


def usage_error(num):
    entry = (_load(USAGE_PATH, {}).get("accounts", {}) or {}).get(str(num)) or {}
    return entry.get("lastError")


def mapped_num(cwd=None):
    """cwd(또는 가장 가까운 상위 디렉토리)에 매핑된 계정 번호."""
    try:
        here = Path(cwd or Path.cwd()).resolve()
    except Exception:
        return None
    best = None
    for path, info in (_load(MAP_PATH, {}).get("mappings", {}) or {}).items():
        p = Path(path)
        if here == p or p in here.parents:
            if best is None or len(str(p)) > len(str(best[0])):
                best = (p, info)
    if not best:
        return None
    email = (best[1] or {}).get("email")
    for a in accounts():
        if a["email"] == email:
            return a["num"]
    return None


def countdown(resets_at):
    """ISO 시각 → '2d 1h' / '3h 27m' / '12m'. 계산 불가면 빈 문자열."""
    if not resets_at:
        return ""
    try:
        ts = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
        delta = (ts - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return ""
    if delta <= 0:
        return "곧"
    d, rem = divmod(int(delta), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def scoped(good, name):
    """per-model 창(예: Fable) 찾기."""
    for s in (good or {}).get("scoped", []) or []:
        if str(s.get("name", "")).lower() == name.lower():
            return s
    return None


# --- 설정 파일 : 계정 성격 + 기능 스위치. cswap 이 모르는 정보라 우리가 따로 관리 ---
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "cc-baton" / "config.json"
LEGACY_CONFIG = Path.home() / ".claude" / "cc-accounts.json"  # 0.1 이전 위치


def config_path():
    """설정 파일 경로. 새 위치에 없고 예전 위치에 있으면 복사해 온다 (예전 파일은 남겨 둔다)."""
    if not CONFIG_PATH.exists() and LEGACY_CONFIG.exists():
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            shutil.copy2(LEGACY_CONFIG, tmp)
            os.replace(tmp, CONFIG_PATH)  # statusline 이 동시에 읽어도 반쪽 파일을 보지 않게
        except OSError:
            return LEGACY_CONFIG
    return CONFIG_PATH


def config():
    return _load(config_path(), {})


def kind(num):
    """'team' | 'personal' | 'unknown'."""
    entry = config().get(str(num))
    if isinstance(entry, dict):
        k = str(entry.get("kind", "")).lower()
        if k in ("team", "personal"):
            return k
    return "unknown"


def crosses_boundary(src_num, dst_num):
    """team ↔ personal 경계를 넘는 이동인가 (둘 다 알려진 성격일 때만 True)."""
    a, b = kind(src_num), kind(dst_num)
    return "unknown" not in (a, b) and a != b


# --- 기능 스위치 (cc-toggle 로 켜고 끄고, HUD 3행이 보여준다) : 같은 파일의 최상위 키 ---
FEATURE_DEFAULTS = {
    "autoUpdate": {"enabled": True},
    "hibernate": {"enabled": True, "idleMin": 90},
    "onLimit": {"enabled": True, "approveCrossing": False, "minHeadroomPct": 15},
}
UPDATE_FAILED = STATE_DIR / "update-failed"  # cc-update 가 설치 실패 시 남긴다
HIB_LABEL = "io.github.juunghyun.cc-baton.hib"  # hib tick 을 돌리는 launchd 에이전트


def feature(key):
    """빠진 키는 기본값, 모르는 키는 무시."""
    cfg = dict(FEATURE_DEFAULTS[key])
    raw = config().get(key)
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if k in cfg})
    return cfg


def usage_note(num):
    """측정치가 없을 때 화면에 띄울 사람이 읽을 사유."""
    err = str(usage_error(num) or "")
    if err == "http-403":
        return "통계 미제공(setup-token/팀 시트)"
    if err == "http-429":
        return "통계 조회 제한(429)"
    if err.startswith("http-"):
        return f"통계 조회 실패({err[5:]})"
    return err or "측정 없음"


def poll_due(num):
    """cswap 자신의 폴 스케줄 기준으로 지금 다시 가져올 때가 됐는가.

    cswap 은 계정별로 nextPollAt/backoffUntil 을 잡아두고, 그 전에 물어보면
    네트워크를 타지 않고 캐시만 돌려준다. 이걸 무시하고 호출하면
    '아무 것도 갱신되지 않는 동기 호출'로 화면만 멈춘다.
    """
    entry = (_load(USAGE_PATH, {}).get("accounts", {}) or {}).get(str(num))
    if not entry:
        return True
    now = time.time()
    backoff = entry.get("backoffUntil")
    if isinstance(backoff, (int, float)) and now < backoff:
        return False
    nxt = entry.get("nextPollAt")
    if not isinstance(nxt, (int, float)):
        return True
    return now >= nxt


def live_email():
    """기본 프로필(~/.claude)에 지금 실제로 로그인된 계정의 이메일.

    cswap 의 activeAccountNumber 는 '마지막으로 cswap 이 전환한 슬롯'일 뿐이라,
    사용자가 세션 안에서 /login 을 하면 실제 로그인과 어긋난다(드리프트).
    화면에 계정을 표시할 때는 기록이 아니라 이 실제 값을 믿어야 한다.
    """
    cfg = _load(Path.home() / ".claude.json", {})
    return ((cfg.get("oauthAccount") or {}).get("emailAddress") or "").strip()


def live_num():
    """라이브 자격증명이 대응하는 슬롯 번호. 어느 슬롯도 아니면 None(미등록)."""
    email = live_email()
    if not email:
        return None
    for a in accounts():
        if a["email"].lower() == email.lower():
            return a["num"]
    return None


def identity():
    """지금 이 프로세스의 계정 신원.

    반환 {num, label, email, drift}:
      num    슬롯 번호 (미등록이면 None)
      drift  cswap 기록과 실제 로그인이 어긋났는지 (기본 프로필에서만 판정)
    """
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        # 세션 프로필은 cswap 이 디렉토리명에 슬롯을 박아두므로 그대로 신뢰한다.
        m = re.match(r"(\d+)-", Path(cfg).name)
        num = m.group(1) if m else None
        acc = next((a for a in accounts() if a["num"] == num), None)
        return {"num": num, "label": acc["label"] if acc else "?",
                "email": acc["email"] if acc else "", "drift": False}

    email = live_email()
    num = live_num()
    recorded = active_num()
    acc = next((a for a in accounts() if a["num"] == num), None)
    return {
        "num": num,
        "label": acc["label"] if acc else (email.split("@")[0] if email else "?"),
        "email": email,
        "drift": bool(email) and (num is None or (recorded and num != recorded)),
    }
