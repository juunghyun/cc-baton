"""README GIF 용 데모 환경: 가짜 계정 3개가 든 HOME 을 만든다. 실제 계정·설정은 건드리지 않는다.

    python3 docs/demo/make_home.py /tmp/cc-baton-demo
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

home = Path(sys.argv[1])
now = time.time()


def iso(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def write(rel, data):
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


accounts = [("1", "work", "work@example.com", 31, 11), ("2", "personal", "me@example.com", 4, 23),
            ("3", "side", "side@example.com", 12, 40), ("4", "spare", "spare@example.com", 0, 2)]
write(".claude-swap-backup/sequence.json", {
    "activeAccountNumber": 1, "sequence": [int(a[0]) for a in accounts],
    "accounts": {n: {"email": e, "alias": alias} for n, alias, e, _, _ in accounts}})
write(".claude-swap-backup/cache/usage.json", {"accounts": {
    n: {"lastGood": {"five_hour": {"pct": h5, "resets_at": iso(2.1)}, "seven_day": {"pct": w1, "resets_at": iso(83)},
                     "scoped": [{"name": "Fable", "pct": 0}]},
        "fetchedAt": now, "nextPollAt": now + 86400}
    for n, _, _, h5, w1 in accounts}})
for n, alias, e, _, _ in accounts[1:]:
    (home / ".claude-swap-backup/sessions" / f"{n}-{e.replace('@', '_')}").mkdir(parents=True, exist_ok=True)
write(".config/cc-baton/config.json", {
    "1": {"group": "work"}, "2": {"group": "personal"}, "3": {"group": "personal"}, "4": {"group": "personal"},
    "autoUpdate": {"enabled": True}, "hibernate": {"enabled": True, "idleMin": 90},
    "onLimit": {"enabled": True, "approveCrossing": False, "minHeadroomPct": 15},
    "setup": {"done": True}})
write(".claude.json", {"oauthAccount": {"emailAddress": "work@example.com"}})
# 최근 사용순: side 를 방금, personal 을 한 시간 전에 쓴 것으로
for n, e, ago in (("3", "side_example.com", 60), ("2", "me_example.com", 3600)):
    f = home / ".claude-swap-backup/sessions" / f"{n}-{e}" / "sessions" / "1.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{}")
    import os
    os.utime(f, (now - ago, now - ago))
repo = home / "src" / "app"
repo.mkdir(parents=True, exist_ok=True)
import subprocess
g = ["git", "-C", str(repo), "-c", "user.email=demo@example.com", "-c", "user.name=demo"]
subprocess.run([*g, "init", "-q", "-b", "feature/login-302"], check=True)
subprocess.run([*g, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
(repo / "wip.txt").write_text("x")
subprocess.run([*g, "add", "wip.txt"], check=True)
(repo / "wip.txt").write_text("y")  # 작업 중(*) 표시
