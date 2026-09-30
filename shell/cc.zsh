# claude-swap: 계정 선택 + 세션 중 계정 스왑(컨텍스트 유지) 재기동 루프
#   cc                 계정 피커(↑↓) → 선택한 계정으로 실행
#   cc personal        계정 지정 실행 (번호/별칭/이메일)
#   cc personal -- -c  '--' 뒤는 claude 로 그대로 전달
# 항상 bypass permissions 로 연다. 끄려면 CC_NO_BYPASS=1 cc ... 로 실행.
# 최초 실행·매 스왑 직전에 cc-update 가 최신 버전을 확인해 npm 전역을 갱신한다. 끄려면 CC_NO_UPDATE=1.
# claude 는 항상 npm 전역(~/.npm-global/bin) 바이너리를 쓴다 — brew cask 의 옛 버전이 잡히는 사고 방지.
# 세션 안에서 /swap 을 쓰면 마커가 남고, claude 종료 시 이 루프가 대화를 이관해 재개한다.
cc() {
  local marker="$HOME/.local/state/cc-swap/request.json"
  local target="" sid="" out rc hsid rest wcwd
  local -a args=() bypass=()

  # 재운 세션 깨우기. 피커가 계정/세션/작업디렉토리를 주면 그 자리로 이동해 resume 한다.
  # 재부팅으로 탭이 통째로 사라진 뒤에도 이 경로는 살아있다 — 원본은 디스크의 마커다.
  if [[ "$1" == "--wake" ]]; then
    shift
    rest="$(cc-baton hib pick)" || return 1
    target="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
    sid="${rest%%$'\t'*}"; wcwd="${rest#*$'\t'}"
    [[ -n "$wcwd" && -d "$wcwd" ]] && builtin cd "$wcwd"
    set -- --resume "$sid" "$@"
  fi

  if [[ -n "$1" && "$1" != "--" && "$1" != -* ]]; then target="$1"; shift; fi
  [[ "$1" == "--" ]] && shift
  args=("$@")

  # bypass permissions 기본 ON. 사용자가 직접 권한 플래그를 넘겼으면 건드리지 않는다.
  if [[ -z "$CC_NO_BYPASS" && "${args[*]}" != *"--permission-mode"* \
        && "${args[*]}" != *"--dangerously-skip-permissions"* ]]; then
    bypass=(--dangerously-skip-permissions)
  fi

  if [[ -z "$target" ]]; then
    target="$(cc-baton pick)" || return 1
  fi

  rm -f "$marker"
  local -x PATH="$HOME/.npm-global/bin:$PATH"
  while true; do
    cc-baton hib cap $$ 2>/dev/null
    cc-baton update
    if (( ${#args} + ${#bypass} )); then
      CC_SWAP_LOOP=1 env -u CLAUDE_CONFIG_DIR cswap run "$target" -- "${bypass[@]}" "${args[@]}"
    else
      CC_SWAP_LOOP=1 env -u CLAUDE_CONFIG_DIR cswap run "$target"
    fi
    rc=$?

    # 재우기 요청이 와 있으면 재실행하지 않고 이 탭에서 멈춰 기다린다.
    # 무거운 건 방금 죽은 node 쪽이고, 여기 남는 건 zsh 하나뿐이다.
    if hsid="$(cc-baton hib claim)" && [[ -n "$hsid" ]]; then
      cc-baton hib banner "$hsid"
      read -k 1 -s
      if ! cc-baton hib wake "$hsid"; then
        echo "  이 세션은 다른 곳에서 이미 깨어났습니다."
        return 0
      fi
      args=(--resume "$hsid")
      continue
    fi

    [[ -f "$marker" ]] || return $rc

    out="$(cc-baton swap consume)" || return $rc
    target="${out%%$'\t'*}"
    sid="${out#*$'\t'}"
    if [[ -n "$sid" ]]; then args=(--resume "$sid"); else args=(); fi
    echo
  done
}
