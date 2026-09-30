# cc-baton: 계정 선택 + 세션 중 계정 스왑(대화 원문 유지) 재기동 루프.
# ~/.zshrc 의 cc-baton 블록이 CC_BATON_BIN 을 정하고 이 파일을 source 한다 (cc-baton install).
#   baton                 계정 피커(↑↓) → 선택한 계정으로 실행. 처음이면 설정 위저드부터
#   baton personal        계정 지정 실행 (번호/별칭/이메일)
#   baton personal -- -c  '--' 뒤는 claude 로 그대로 전달
#   baton --wake          재운 세션을 골라 그 자리에서 깨운다
# 권한 확인 건너뛰기(--dangerously-skip-permissions)는 사용자가 켰을 때만 (cc-baton toggle bypass on|off, 한 번만 끄기: CC_NO_BYPASS=1).
# 세션 안에서 /swap 을 쓰면 마커가 남고, claude 종료 시 이 루프가 대화를 이관해 재개한다.
baton() {
  local b="${CC_BATON_BIN:-cc-baton}"
  local marker="${XDG_STATE_HOME:-$HOME/.local/state}/cc-baton/request.json"
  local target="" sid="" out rc hsid rest wcwd
  local -a args=() bypass=()

  "$b" setup --if-needed || return 1

  # 재운 세션 깨우기. 피커가 계정/세션/작업디렉토리를 주면 그 자리로 이동해 resume 한다.
  # 재부팅으로 탭이 통째로 사라진 뒤에도 이 경로는 살아있다 — 원본은 디스크의 마커다.
  if [[ "$1" == "--wake" ]]; then
    shift
    rest="$("$b" hib pick)" || return 1
    target="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
    sid="${rest%%$'\t'*}"; wcwd="${rest#*$'\t'}"
    [[ -n "$wcwd" && -d "$wcwd" ]] && builtin cd "$wcwd"
    set -- --resume "$sid" "$@"
  fi

  if [[ -n "$1" && "$1" != "--" && "$1" != -* ]]; then target="$1"; shift; fi
  [[ "$1" == "--" ]] && shift
  args=("$@")

  # 권한 확인 건너뛰기는 설정에서 켰을 때만. 사용자가 직접 권한 플래그를 넘겼으면 건드리지 않는다.
  if [[ -z "$CC_NO_BYPASS" && "${args[*]}" != *"--permission-mode"* \
        && "${args[*]}" != *"--dangerously-skip-permissions"* ]] && "$b" toggle is-on bypass; then
    bypass=(--dangerously-skip-permissions)
  fi

  if [[ -z "$target" ]]; then
    target="$("$b" pick)" || return 1
  fi

  rm -f "$marker"
  while true; do
    "$b" hib cap $$ 2>/dev/null
    "$b" claude-update
    if (( ${#args} + ${#bypass} )); then
      CC_SWAP_LOOP=1 env -u CLAUDE_CONFIG_DIR "$b" cswap run "$target" -- "${bypass[@]}" "${args[@]}"
    else
      CC_SWAP_LOOP=1 env -u CLAUDE_CONFIG_DIR "$b" cswap run "$target"
    fi
    rc=$?

    # 재우기 요청이 와 있으면 재실행하지 않고 이 탭에서 멈춰 기다린다.
    # 무거운 건 방금 죽은 node 쪽이고, 여기 남는 건 zsh 하나뿐이다.
    if hsid="$("$b" hib claim)" && [[ -n "$hsid" ]]; then
      "$b" hib banner "$hsid"
      read -k 1 -s
      "$b" hib wake "$hsid" || return 0   # 다른 데서 이미 깨웠으면 안내만 찍고 끝
      args=(--resume "$hsid")
      continue
    fi

    [[ -f "$marker" ]] || return $rc

    out="$("$b" swap consume)" || return $rc
    target="${out%%$'\t'*}"
    sid="${out#*$'\t'}"
    if [[ -n "$sid" ]]; then args=(--resume "$sid"); else args=(); fi
    echo
  done
}
