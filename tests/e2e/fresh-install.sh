#!/bin/zsh
# 새 Mac 사용자가 README 대로 설치 → 첫 실행 위저드 → HUD → 제거까지 가는 길을 진짜 HOME·진짜 명령으로 돈다.
# CI(macOS 실행기)에서 PATH 를 macOS 기본값만 남기고 부른다 (.github/workflows/ci.yml 의 install 작업).
# Claude 로그인은 사람이 해야 해서, claude-swap 계정 두 개를 파일로 넣어 두고 쓴다.
#   REPO=<클론 경로> zsh tests/e2e/fresh-install.sh
set -eu
setopt pipefail
REPO=${REPO:?clone path}
step() { print -P "\n%B== $* ==%b"; }
fail() { print -u2 "FAIL: $*"; exit 1; }

step "사용자가 이미 가진 설정"
mkdir -p ~/.claude
print '{\n  "theme": "dark"\n}' > ~/.claude/settings.json   # cc-baton 이 쓰는 모양 그대로 (제거 뒤 바이트 비교)
print 'export USER_LINE=1' >> ~/.zshrc
cp ~/.claude/settings.json ~/.e2e-settings.before
cp ~/.zshrc ~/.e2e-zshrc.before
mkdir -p ~/.claude-swap-backup/cache
cat > ~/.claude-swap-backup/sequence.json <<'EOF'
{"activeAccountNumber": 1, "sequence": [1, 2],
 "accounts": {"1": {"email": "work@example.com", "alias": ""}, "2": {"email": "me@example.com", "alias": "home"}}}
EOF
print '{"oauthAccount": {"emailAddress": "work@example.com"}}' > ~/.claude.json

step "README 설치 블록 (저장소 주소만 이 커밋으로)"
git -C "$REPO" checkout -q -B e2e
block=$(awk '/^## Install/{f=1} f && /^```sh/{g=1; next} g && /^```/{exit} g' "$REPO/README.md")
[[ -n "$block" ]] || fail "README 에서 설치 블록을 못 찾음"
block=${block//git+https:\/\/github.com\/juunghyun\/cc-baton/git+file://$REPO@e2e}
print -r -- "$block"
eval "$block"
command -v uv >/dev/null || fail "uv 가 PATH 에 없음"
~/.local/bin/cc-baton --version

step "설치 결과"
PY="$(uv tool dir)/cc-baton/bin/python"
"$PY" - <<'EOF'
import json, os
s = json.load(open(os.path.expanduser("~/.claude/settings.json")))
assert s["theme"] == "dark", s
assert s["statusLine"]["command"].endswith("cc-baton statusline"), s
assert {"UserPromptSubmit", "StopFailure"} <= set(s["hooks"]), s
EOF
grep -q '# >>> cc-baton >>>' ~/.zshrc || fail "zshrc 블록 없음"
[[ "$(zsh -ic 'whence -w baton' 2>/dev/null)" == "baton: function" ]] || fail "새 zsh 에 baton 함수 없음"
zsh -ic 'command -v cswap' >/dev/null 2>&1 || fail "새 zsh 에 cswap 없음"

step "첫 실행 위저드 (가짜 터미널)"
"$PY" - <<'EOF'
import os, pty, select, sys, time
pid, fd = pty.fork()
if pid == 0:
    os.execv(os.path.expanduser("~/.local/bin/cc-baton"), ["cc-baton", "setup", "--if-needed"])
out = b""
def pump(sec):
    global out
    end = time.time() + sec
    while time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            try:
                out += os.read(fd, 65536)
            except OSError:
                return
# 언어 / 1번 그룹·짧은 이름 / 2번 그룹 / 절전 켬·30분 / 자동 스왑·사용량·권한 건너뛰기 끔
for answer in ("en", "work", "job", "personal", "y", "30", "n", "n", "n"):
    pump(2)
    try:
        os.write(fd, answer.encode() + b"\r")
    except OSError:
        sys.exit("wizard ended early:\n" + out.decode(errors="ignore"))
pump(5)
_, status = os.waitpid(pid, 0)
print(out.decode(errors="ignore"))
sys.exit(os.waitstatus_to_exitcode(status))
EOF
"$PY" - <<'EOF'
import json, os
c = json.load(open(os.path.expanduser("~/.config/cc-baton/config.json")))
assert c["setup"]["done"] and c["language"] == "en", c
assert c["1"]["group"] == "work" and c["2"]["group"] == "personal", c
assert c["hibernate"] == {"enabled": True, "idleMin": 30.0}, c
assert not c["onLimit"]["enabled"] and not c["skipPermissions"]["enabled"], c
seq = json.load(open(os.path.expanduser("~/.claude-swap-backup/sequence.json")))
assert seq["accounts"]["1"]["alias"] == "job", seq  # 위저드가 번들 cswap 으로 저장
EOF
launchctl print "gui/$(id -u)/io.github.juunghyun.cc-baton.hib" >/dev/null || fail "절전 launchd 미등록"

step "HUD"
hud=$(print '{"model": {"display_name": "Opus"}, "effort": {"level": "high"}}' | COLUMNS=120 ~/.local/bin/cc-baton statusline)
print -r -- "$hud"
[[ "$hud" == *job*WORK* && "$hud" == *Sleep* && "$hud" != *"[statusline]"* ]] || fail "HUD 이상"

step "/swap 훅: 다른 그룹이면 먼저 묻는다"
set +e
print '{"prompt": "/swap home", "session_id": "e2e-session"}' | CC_SWAP_LOOP=1 ~/.local/bin/cc-baton swap hook prompt
rc=$?
set -e
(( rc == 2 )) || fail "/swap 훅 rc=$rc"

step "제거"
~/.local/bin/cc-baton uninstall --yes --purge
[[ ! -e ~/.local/bin/cc-baton ]] || fail "cc-baton 이 남음"
cmp -s ~/.claude/settings.json ~/.e2e-settings.before || fail "settings.json 이 설치 전과 다름: $(cat ~/.claude/settings.json)"
# uv 설치 스크립트가 넣은 PATH 줄(과 그 앞 빈 줄)은 uv 몫이라 뺀다
cmp -s <(grep -v -e '.local/bin/env' -e '^$' ~/.zshrc) <(grep -v -e '.local/bin/env' -e '^$' ~/.e2e-zshrc.before) || { diff ~/.e2e-zshrc.before ~/.zshrc; fail "zshrc 가 설치 전과 다름"; }
! launchctl print "gui/$(id -u)/io.github.juunghyun.cc-baton.hib" >/dev/null 2>&1 || fail "절전 launchd 가 남음"
[[ ! -e ~/.config/cc-baton ]] || fail "설정 폴더가 남음 (--purge)"
[[ -f ~/.claude-swap-backup/sequence.json ]] || fail "claude-swap 계정 데이터가 사라짐"
[[ -x ~/.local/bin/cswap ]] || fail "claude-swap 을 따로 남기지 않음"
print -P "\n%F{green}fresh install e2e: ok%f"
