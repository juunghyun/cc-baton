#!/usr/bin/env bash
# cc 래퍼 전용: claude 실행 직전에 최신 버전을 확인하고, 있으면 npm 전역 설치를 갱신한다.
#
# 왜 있나: claude가 두 군데(npm 전역 / Homebrew cask) 깔려 있어 어느 쪽이 잡히느냐에 따라
# 옛 버전이 뜨고, 옛 버전은 새 모델(Fable 등)을 거부한다. "does not support this model" 사고.
# cc 는 항상 npm 전역 바이너리를 쓰고, 그걸 여기서 최신으로 맞춘다.
#
# 왜 락이 있나(2026-09-15): 탭이 여럿이면 cc 루프가 동시에 여기로 들어온다. 전부 설치를 걸면
# npm 이 reify 중에 ~/.npm-global/bin/claude 를 옆으로 치웠다가(mark retired) 다시 만든다.
# 그 찰나에 다른 탭의 cswap 이 미리 해석해 둔 절대경로로 exec 하면 ENOENT 로 죽는다
# (FileNotFoundError: '/Users/…/.npm-global/bin/claude'). 재운 세션을 깨우다 이 사고가 났다.
# → 락을 잡은 한 탭만 설치하고, 나머지는 기다렸다가 버전을 다시 본다(그때는 이미 최신이라 통과).
#   설치가 끝난 뒤엔 바이너리가 제자리에 돌아왔는지 확인하고 나서야 cswap 으로 넘어간다.
#
# 항상 exit 0 — 업데이트 확인/설치 실패가 claude 실행을 막으면 안 된다.
# 끄기: cc-baton toggle update off (한 번만: CC_NO_UPDATE=1)

PKG="@anthropic-ai/claude-code"
# npm 전역 경로: PATH 의 claude 가 npm 설치본이면 그 경로에서 바로 얻는다 (npm prefix -g 는 0.1초 걸린다).
if [[ -z "${NPM_PREFIX:-}" ]]; then
  real="$(realpath "$(command -v claude 2>/dev/null)" 2>/dev/null)"
  if [[ "$real" == */lib/node_modules/@anthropic-ai/claude-code/* ]]; then
    NPM_PREFIX="${real%%/lib/node_modules/*}"
  else
    NPM_PREFIX="$(npm prefix -g 2>/dev/null)"
  fi
fi
PKG_JSON="$NPM_PREFIX/lib/node_modules/$PKG/package.json"
BIN="$NPM_PREFIX/bin/claude"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/cc-baton"
LOCK="$STATE/claude-update.lock"
# 화면 문구: m "English" "한국어" (언어는 cc-baton 이 CC_BATON_LANG 으로 넘긴다)
m() { if [[ "$CC_BATON_LANG" == ko ]]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
R=$'\033[0m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; GRN=$'\033[38;5;42m'; YEL=$'\033[38;5;208m'

[[ -n "$CC_NO_UPDATE" ]] && exit 0
"${CC_BATON_PY:-python3}" -m cc_baton toggle is-on update || exit 0   # cc-baton toggle update off
FAILED="$STATE/update-failed"   # HUD 3행이 ⚠실패 로 띄운다
# npm 으로 깐 claude 가 아니면 할 일이 없다 (공식 설치본은 스스로 업데이트). 여기서 안 멈추면 bin 을 40초 기다린다.
command -v npm >/dev/null && command -v node >/dev/null && [[ -n "$NPM_PREFIX" ]] || exit 0
[[ -e "$BIN" || -d "$LOCK" ]] || exit 0

installed_version() {
  [[ -f "$PKG_JSON" ]] || return 0
  # node 를 띄우지 않고 읽는다. package.json 최상위 "version" 이 첫 번째로 나온다
  local v
  v="$(sed -n 's/^  "version": *"\([^"]*\)".*/\1/p' "$PKG_JSON" | head -n1)"
  # 모양이 달라 못 읽으면 node 로 (빈 값이면 매 실행 재설치로 이어진다)
  [[ -n "$v" ]] || v="$(PKG_JSON="$PKG_JSON" node -p "require(process.env.PKG_JSON).version" 2>/dev/null)"
  printf '%s' "$v"
}

# npm 이 bin 을 치워둔 순간을 넘긴다. 치운 건 길어야 설치 한 번 길이다.
wait_for_bin() {
  local i=0
  while (( i < 400 )); do          # 최대 40초
    [[ -x "$BIN" ]] && return 0
    sleep 0.1; (( i++ ))
  done
  return 1
}

lock_held=""
release_lock() { [[ -n "$lock_held" ]] && rm -rf "$LOCK"; lock_held=""; }
acquire_lock() {
  local i=0 holder
  mkdir -p "$(dirname "$LOCK")" 2>/dev/null
  while (( i < 900 )); do          # 최대 90초 — 설치 한 번보다 넉넉히
    if mkdir "$LOCK" 2>/dev/null; then
      echo $$ > "$LOCK/pid" 2>/dev/null
      lock_held=1
      trap release_lock EXIT INT TERM
      return 0
    fi
    # 죽은 홀더가 남긴 락은 회수한다 (Ctrl-C·강제종료·재부팅).
    holder="$(cat "$LOCK/pid" 2>/dev/null)"
    if [[ -z "$holder" ]] || ! kill -0 "$holder" 2>/dev/null; then
      rm -rf "$LOCK" 2>/dev/null
      continue
    fi
    sleep 0.1; (( i++ ))
  done
  return 1
}

installed="$(installed_version)"

# 최신 버전은 30분에 한 번만 레지스트리에 묻는다. 매 실행·매 /swap 재기동마다 묻으면 0.5~1초씩 늦어진다.
CHECKED="$STATE/claude-latest"
latest=""
[[ -n "$(find "$CHECKED" -mmin -30 2>/dev/null)" ]] && latest="$(cat "$CHECKED" 2>/dev/null)"
if [[ -z "$latest" ]]; then
  # 레지스트리 조회는 5초 안에 못 받으면 포기(오프라인·VPN). 그냥 깔린 걸로 켠다.
  latest="$(curl -fsS --max-time 5 "https://registry.npmjs.org/$PKG/latest" 2>/dev/null \
    | node -p "JSON.parse(require('fs').readFileSync(0,'utf8')).version" 2>/dev/null)"
  [[ -n "$latest" ]] && mkdir -p "$STATE" && printf '%s\n' "$latest" > "$CHECKED"
fi

if [[ -z "$latest" ]]; then
  echo "${DIM}[cc-baton] $(m "couldn't check the latest version (network); running the installed ${installed:-?}" "최신 버전을 확인하지 못해(네트워크) 설치된 ${installed:-?} 로 실행합니다")${R}" >&2
  wait_for_bin || echo "${YEL}![cc-baton] $(m "$BIN is missing; another tab may be installing" "$BIN 이 없습니다. 다른 탭에서 설치 중일 수 있습니다")${R}" >&2
  exit 0
fi

# 최신 == 설치본이면 조용히 통과. 설치본이 없거나 더 낮으면 갱신.
up_to_date() {
  local cur="$1"
  [[ -n "$cur" ]] || return 1
  [[ "$(printf '%s\n%s\n' "$cur" "$latest" | sort -V | tail -n1)" == "$cur" ]]
}

if up_to_date "$installed"; then
  rm -f "$FAILED"   # 이미 최신 — 예전 실패 표시는 낡은 것
  # 내가 설치할 일은 없다. 다만 다른 탭이 지금 reify 중일 수 있으니 bin 은 확인하고 넘긴다.
  wait_for_bin || echo "${YEL}![cc-baton] $(m "$BIN is missing; another tab may be installing" "$BIN 이 없습니다. 다른 탭에서 설치 중일 수 있습니다")${R}" >&2
  exit 0
fi

if ! acquire_lock; then
  echo "${YEL}![cc-baton] $(m "waited 90s for another tab's update; running the installed ${installed:-?}" "다른 탭의 업데이트를 90초 기다렸습니다. 설치된 ${installed:-?} 로 실행합니다")${R}" >&2
  wait_for_bin || echo "${YEL}![cc-baton] $(m "$BIN is missing" "$BIN 이 없습니다")${R}" >&2
  exit 0
fi

# 기다리는 동안 다른 탭이 이미 올려놨을 수 있다. 락 안에서 다시 본다.
installed="$(installed_version)"
if up_to_date "$installed"; then
  release_lock
  wait_for_bin || echo "${YEL}![cc-baton] $(m "$BIN is missing" "$BIN 이 없습니다")${R}" >&2
  exit 0
fi

echo "${YEL}⬆${R} Claude Code ${installed:-$(m "(none)" "(없음)")} → ${BOLD}$latest${R} $(m "updating…" "업데이트 중…")" >&2
if npm install -g "$PKG@$latest" --no-fund --no-audit --loglevel=error >&2; then
  echo "${GRN}✓${R} Claude Code $latest $(m "installed" "설치 완료")" >&2
  rm -f "$FAILED"
else
  mkdir -p "$(dirname "$FAILED")" && touch "$FAILED"
  echo "${YEL}![cc-baton] $(m "update failed; running the installed ${installed:-?}. By hand: npm i -g $PKG@latest" "업데이트에 실패해 설치된 ${installed:-?} 로 실행합니다. 직접: npm i -g $PKG@latest")${R}" >&2
fi
release_lock

# 여기서 bin 이 없으면 cswap 이 exec 단계에서 파이썬 트레이스백으로 죽는다. 먼저 알려준다.
if ! wait_for_bin; then
  echo "${YEL}![cc-baton] $(m "$BIN has been missing for 40s; launching may fail." "$BIN 이 40초째 없습니다. 실행이 실패할 수 있습니다.")" >&2
  echo "   $(m "Fix by hand" "수동 복구"): npm i -g $PKG@latest${R}" >&2
fi
exit 0
