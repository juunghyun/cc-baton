# README GIF: vhs docs/demo/demo.tape  (언어: DEMO_LANG=en|ko, 출력: DEMO_OUT)
# 가짜 계정이 든 데모 HOME 에서 실제 cc-baton 명령을 돌린다.
Output docs/demo.gif
Set Shell zsh
Set FontSize 15
Set Width 1180
Set Height 560
Set Padding 18
Set TypingSpeed 35ms
Set Theme "Catppuccin Mocha"

Hide
Type "source docs/demo/env.zsh && clear" Enter
Sleep 1s
Show

Type "cc-baton pick" Sleep 400ms Enter
Sleep 1.8s
Down Sleep 700ms Down Sleep 700ms Down Sleep 700ms Up Sleep 700ms Up Sleep 900ms Enter
Sleep 1.5s

Type "hud high" Sleep 300ms Enter
Sleep 3s
Type "hud-effort" Sleep 300ms Enter
Sleep 3.5s

Type "cc-baton swap request personal" Sleep 300ms Enter
Sleep 3.5s
Type "cc-baton swap request personal --yes" Sleep 300ms Enter
Sleep 3.5s
