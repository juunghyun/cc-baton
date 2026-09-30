# demo.tape 가 source 한다: 데모 HOME 으로 바꾸고 HUD 를 찍는 함수 두 개를 둔다.
export CC_BATON_BIN="$(command -v cc-baton)"
export HOME="${DEMO_HOME:-/tmp/cc-baton-demo}"
rm -rf "$HOME" && python3 "${0:A:h}/make_home.py" "$HOME"
export CC_BATON_LANG="${DEMO_LANG:-en}" CC_PICK_NO_REFRESH=1 CC_SWAP_LOOP=1 COLUMNS=120
unset CLAUDE_CONFIG_DIR
cd "$HOME/src/app"
PROMPT='%F{245}~/src/app%f %F{141}❯%f '
_hudjson() { printf '{"model":{"display_name":"Opus 5.5 (1M context)"},"effort":{"level":"%s"},"context_window":{"used_percentage":%s},"workspace":{"current_dir":"%s"}}' "$1" "$2" "$PWD"; }
hud() { _hudjson "${1:-high}" 12 | "$CC_BATON_BIN" statusline; echo; }
hud-effort() { for e in low medium high xhigh max; do _hudjson $e 12 | "$CC_BATON_BIN" statusline | head -1; done; echo; }
