"""claude-swap 상태 읽기 전용 접근자 — cswap을 import하지 않고, 네트워크도 안 탄다.

cswap의 온디스크 포맷(sequence.json / mappings.json / cache/usage.json)만 읽는다.
포맷이 바뀌면 조용히 None/빈값으로 떨어지는 게 계약이다 — 호출자(statusline)는
절대 죽으면 안 되므로.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from .i18n import L

BACKUP = Path.home() / ".claude-swap-backup"
SEQ_PATH = BACKUP / "sequence.json"
MAP_PATH = BACKUP / "mappings.json"
USAGE_PATH = BACKUP / "cache" / "usage.json"
SESSIONS_DIR = BACKUP / "sessions"
# cc-baton 과 함께 설치된 claude-swap 을 쓴다 (테스트한 버전으로 고정). 없으면 따로 깔린 것.
_BUNDLED_CSWAP = Path(sys.executable).parent / "cswap"
CSWAP_BIN = _BUNDLED_CSWAP if _BUNDLED_CSWAP.exists() else Path.home() / ".local" / "bin" / "cswap"
_STATE_ROOT = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
STATE_DIR = _STATE_ROOT / "cc-baton"
# 0.1 이전 상태 폴더 → 새 위치. install 이 옮기고 옛 경로에 링크를 남긴다 (옛 셸 함수를 든 탭용).
LEGACY_STATE = {_STATE_ROOT / "cc-swap": STATE_DIR, _STATE_ROOT / "cc-hib": STATE_DIR / "hib"}
# /swap 예약은 탭마다 따로 (다른 탭의 claude 가 종료되며 가져가지 않게). baton 이 CC_BATON_TAB=$$ 를 넘긴다.
_TAB = os.environ.get("CC_BATON_TAB", "")
MARKER = STATE_DIR / (f"request-{_TAB}.json" if _TAB.isdigit() else "request.json")
REFRESH_STAMP = STATE_DIR / "usage-refresh.stamp"


# 세션 ID 는 경로·glob 에 들어간다. 슬래시·와일드카드·.. 가 못 들어오게 좁힌다.
SID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def valid_sid(sid):
    return bool(sid) and bool(SID_RE.match(str(sid))) and ".." not in str(sid)


def clean(text):
    """화면에 찍을 외부 문자열(별칭·경로·세션 이름)에서 제어문자를 뺀다. 터미널 제어 시퀀스 주입 방지."""
    return _CTRL.sub("", str(text or ""))


def private_dir(path):
    """상태·설정 폴더는 본인만 읽게 (0700). macOS 홈은 staff 그룹이 들어올 수 있다."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def write_json(path, data):
    """원자적으로 쓰되 원래 파일 권한을 유지한다 (새 파일은 0600). 임시 파일 이름도 겹치지 않게."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load(path, default):
    """JSON 을 읽되 모양(dict/list)이 기본값과 다르면 기본값. 남의 파일 형식이 바뀌어도 화면이 죽지 않게."""
    try:
        v = json.loads(Path(path).read_text())
    except Exception:
        return default
    return v if isinstance(v, type(default)) else default


def _dict(v):
    return v if isinstance(v, dict) else {}


def number(v):
    """숫자면 float, 아니면 None ("21" 같은 문자열·bool 은 숫자로 보지 않는다)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def sequence():
    return _load(SEQ_PATH, {})


def accounts():
    """슬롯 순서대로 [{num, email, alias, label}]."""
    seq = sequence()
    table = _dict(seq.get("accounts"))
    order = seq.get("sequence") if isinstance(seq.get("sequence"), list) else None
    order = order or sorted((k for k in table if str(k).isdigit()), key=int)
    out = []
    for n in order:
        a = table.get(str(n))
        if not isinstance(a, dict):
            continue
        alias = a.get("alias") if isinstance(a.get("alias"), str) else ""
        email = a.get("email") if isinstance(a.get("email"), str) else ""
        out.append({
            "num": str(n),
            "email": email,
            "alias": alias,
            "label": clean(alias or email),
        })
    return out


def active_num():
    """기본 로그인(~/.claude)이 들고 있는 계정 번호."""
    v = sequence().get("activeAccountNumber")
    return str(v) if v is not None else ""


def resolve(token):
    """번호 → 별칭 → 이메일(정확히) → 이메일(부분, 하나일 때) → 그룹 이름(그 그룹에 계정이 하나일 때). 못 찾으면 None."""
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
    for a in accs:
        if a["email"].lower() == t:
            return a
    hits = [a for a in accs if t in a["email"].lower()]
    if len(hits) == 1:
        return hits[0]
    in_group = [a for a in accs if group(a["num"]) == t]
    return in_group[0] if len(in_group) == 1 else None


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


def is_profile_dir(path):
    """claude-swap 이 관리하는 프로필 폴더인가 (~/.claude 또는 sessions/<n>-<email>). 예약 파일을 믿지 않기 위해."""
    try:
        p = Path(path).resolve()
    except (OSError, TypeError):
        return False
    return p == (Path.home() / ".claude").resolve() or p.parent == SESSIONS_DIR.resolve()


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
    entry = _dict(_dict(_load(USAGE_PATH, {}).get("accounts")).get(str(num)))
    if not isinstance(entry.get("lastGood"), dict) or not entry["lastGood"]:
        return None
    good = dict(entry["lastGood"])
    good["_age"] = time.time() - (number(entry.get("fetchedAt")) or 0)
    good["_error"] = entry.get("lastError")
    return good


def usage_error(num):
    return _dict(_dict(_load(USAGE_PATH, {}).get("accounts")).get(str(num))).get("lastError")


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
        return L("soon", "곧")
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
    sc = (good or {}).get("scoped")
    for s in sc if isinstance(sc, list) else []:
        if isinstance(s, dict) and str(s.get("name", "")).lower() == name.lower():
            return s
    return None


# --- 설정 파일 : 계정 성격 + 기능 스위치. cswap 이 모르는 정보라 우리가 따로 관리 ---
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "cc-baton" / "config.json"
LEGACY_CONFIG = Path.home() / ".claude" / "cc-accounts.json"  # 0.1 이전 위치


def config_path():
    """설정 파일 경로. 새 위치에 없고 예전 위치에 있으면 복사해 온다 (예전 파일은 남겨 둔다)."""
    if not CONFIG_PATH.exists() and LEGACY_CONFIG.exists():
        try:
            private_dir(CONFIG_PATH.parent)
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            shutil.copy2(LEGACY_CONFIG, tmp)
            os.replace(tmp, CONFIG_PATH)  # statusline 이 동시에 읽어도 반쪽 파일을 보지 않게
        except OSError:
            return LEGACY_CONFIG
    return CONFIG_PATH


def config():
    return _load(config_path(), {})


def _account_cfg(num):
    entry = config().get(str(num))
    return entry if isinstance(entry, dict) else {}


def group(num):
    """계정 그룹 이름 (자유롭게 짓는다: work, side, personal …). 없으면 'unknown'.

    0.1 이전 설정의 "kind"(team/personal) 도 그룹 이름으로 읽는다.
    """
    g = str(_account_cfg(num).get("group") or _account_cfg(num).get("kind") or "").strip().lower()
    return g or "unknown"


# 색을 안 정한 그룹은 처음 나온 순서대로 받는다. 앞의 둘은 0.1 이전 personal(파랑)·team(주황) 과 같다.
PALETTE = (39, 208, 170, 42, 220, 203, 81, 214)


def color(num):
    """계정 그룹 색 ANSI. 설정 "color": 256색 번호 또는 "#RRGGBB"."""
    c = _account_cfg(num).get("color")
    if isinstance(c, int) and 0 <= c <= 255:
        return f"\033[38;5;{c}m"
    if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        r, g_, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
        return f"\033[38;2;{r};{g_};{b}m"
    g = group(num)
    if g == "unknown":
        return "\033[38;5;245m"
    seen = []
    for a in accounts():
        ga = group(a["num"])
        if ga != "unknown" and ga not in seen:
            seen.append(ga)
    return f"\033[38;5;{PALETTE[seen.index(g) % len(PALETTE)]}m"


def last_used(num):
    """계정을 마지막으로 쓴 시각(epoch 초). 기록이 없으면 None.

    Claude Code 가 세션 상태가 바뀔 때마다 갱신하는 <프로필>/sessions/*.json 과 프롬프트 기록
    history.jsonl 의 수정 시각 중 최신. 여러 계정을 동시에 써도 계정마다 따로 잡힌다.
    stat 만 하므로 statusline 에서 매번 불러도 싸다.
    """
    prof = profile_dir(num)
    if not prof:
        return None
    times = []
    for p in [*Path(prof).glob("sessions/*.json"), Path(prof) / "history.jsonl"]:
        try:
            times.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(times) if times else None


def crosses_boundary(src_num, dst_num):
    """그룹 경계를 넘는 이동인가. 그룹을 아직 안 정한 계정이 끼면 안전하게 넘는 것으로 본다."""
    a, b = group(src_num), group(dst_num)
    return a != b or "unknown" in (a, b)


# --- 기능 스위치 (cc-toggle 로 켜고 끄고, HUD 3행이 보여준다) : 같은 파일의 최상위 키 ---
FEATURE_DEFAULTS = {
    # 설치 직후엔 전부 꺼져 있고, 위저드(cc-baton setup)가 켠다. autoUpdate 는 install 이 npm 설치본이면 켠다.
    "autoUpdate": {"enabled": False},
    "hibernate": {"enabled": False, "idleMin": 90},
    "onLimit": {"enabled": False, "approveCrossing": False, "minHeadroomPct": 15},
    # claude-swap 이 계정마다 Anthropic 사용량 API 를 부른다. 스크립트가 도는 접근이라 사용자가 켤 때만.
    "usageRefresh": {"enabled": False},
    # baton 이 Claude Code 를 --dangerously-skip-permissions 로 띄울지. 권한 확인이 사라지므로 사용자가 켤 때만.
    "skipPermissions": {"enabled": False},
}
UPDATE_FAILED = STATE_DIR / "update-failed"  # claude-update 가 설치 실패 시 남긴다
HIB_LABEL = "io.github.juunghyun.cc-baton.hib"  # hib tick 을 돌리는 launchd 에이전트


def claude_is_npm():
    """PATH 의 claude 가 npm 전역 설치본인가. 공식 설치본은 스스로 업데이트하므로 우리가 올리지 않는다."""
    c = shutil.which("claude")
    return bool(c) and "node_modules/@anthropic-ai/claude-code" in str(Path(c).resolve())


def feature(key):
    """빠진 키는 기본값, 모르는 키는 무시."""
    cfg = dict(FEATURE_DEFAULTS[key])
    raw = config().get(key)
    if isinstance(raw, dict):  # 기본값과 같은 종류의 값만 받는다 ("90" 같은 문자열 숫자는 버린다)
        cfg.update({k: v for k, v in raw.items() if k in cfg and (
            isinstance(v, bool) if isinstance(cfg[k], bool) else number(v) is not None if isinstance(cfg[k], (int, float)) else True)})
    return cfg


def usage_note(num):
    """측정치가 없을 때 화면에 띄울 사람이 읽을 사유."""
    err = str(usage_error(num) or "")
    if err == "http-403":
        return L("no usage data (setup-token / team seat)", "통계 미제공(setup-token/팀 시트)")
    if err == "http-429":
        return L("usage lookup rate-limited (429)", "통계 조회 제한(429)")
    if err.startswith("http-"):
        return L(f"usage lookup failed ({err[5:]})", f"통계 조회 실패({err[5:]})")
    return err or L("not measured yet", "측정 없음")


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
    email = _dict(cfg.get("oauthAccount")).get("emailAddress")
    return email.strip() if isinstance(email, str) else ""


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
