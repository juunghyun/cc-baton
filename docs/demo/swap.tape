# README GIF 2: 실제 Claude Code 에서 계정 스왑 후 맥락 유지. 실제 계정 두 개가 필요하다.
# 녹화 전: 한쪽 계정 별칭을 잠깐 work 로 (env -u CLAUDE_CONFIG_DIR cc-baton cswap alias <n> work), 녹화 뒤 원래대로.
# 이메일이 찍히는 claude-swap 실행 줄은 Hide 구간(시작, Ctrl+D 두 번 뒤 재실행)에만 나온다. 녹화 후 프레임을 반드시 확인.
# 레벨 칩·권한 우회 같은 개인 설정은 빼고 찍는다: 설정 사본(levelChip·skipPermissions 끔)을 <dir>/cc-baton/config.json 에 두고 DEMO_XDG=<dir>.
# 실행: env -i HOME="$HOME" USER="$USER" SHELL=/bin/zsh TERM=xterm-256color LANG=en_US.UTF-8 PATH="$PATH" DEMO_XDG=<dir> vhs docs/demo/swap.tape
Output "docs/demo-swap.gif"
Set Shell zsh
Set FontSize 14
Set Width 1280
Set Height 760
Set Padding 16
Set TypingSpeed 45ms
Set Theme "Catppuccin Mocha"

Hide
Type "export CC_BATON_BIN=$HOME/.local/bin/cc-baton CC_BATON_LANG=en XDG_CONFIG_HOME=$DEMO_XDG; source $HOME/personal/cc-baton/cc_baton/data/baton.zsh; cd ~/workspace && clear" Enter
Sleep 1s
Type "baton work" Enter
Sleep 14s
Show

Sleep 1.5s
Type "Remember this for later: our release codename is BLUEBIRD. Reply with just: Noted." Sleep 400ms Enter
Sleep 11s
Type "/swap personal" Sleep 500ms Enter
Sleep 4s
Type "/swap personal --yes" Sleep 500ms Enter
Sleep 4.5s

Hide
Ctrl+D
Sleep 300ms
Ctrl+D
Sleep 20s
Show

Sleep 1.5s
Type "What is our release codename? One word." Sleep 400ms Enter
Sleep 12s
