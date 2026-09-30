# cc-baton

**Claude Code 계정 사이에 바통을 넘기세요.** 세션 도중에 계정을 바꿔도 대화가 요약이 아니라 트랜스크립트 원본 그대로 이어집니다. statusline HUD 에서 지금 어느 계정인지, 계정마다 사용량이 얼마나 남았는지, 컨텍스트가 얼마나 찼는지 한눈에 봅니다.

macOS 전용 · [English README](README.md) · [claude-swap](https://github.com/realiti4/claude-swap)(MIT) 위에 만들었습니다. Anthropic·claude-swap 과 관계없는 비공식 도구입니다.

> 정식 출시 전입니다. 의견은 Issues 에 남겨 주세요.

![cc-baton: 계정 피커, effort 단계별 HUD, 그룹이 다른 계정으로의 /swap](docs/demo.ko.gif)

<sub>가짜 계정을 넣은 데모 환경에서 녹화했습니다([docs/demo](docs/demo)). Claude Code 안에서는 HUD 가 입력창 아래에 붙습니다.</sub>

```
● work [WORK] · Opus 5.5[1M] ▰▰▰▱▱ high ▌ Lv.60  · ctx 12% · ~/src/app (ft/login-302*)
5h ██░░░░ 31% 2h04m │ 1w █░░░░░ 11% 3d11h │ ↔ personal 0%/3%, side 12%/40% 외 1개
⟳ 업데이트 ▮▮▮ · ⏾ 절전 ▮▮▮ 90m · ⇄ 한도스왑 ▮▮▮ 승인필요
```

- **1행:** 지금 계정과 그룹, 모델과 effort 미터, 컨텍스트 사용률, 폴더와 브랜치
- **2행:** 이 계정의 5시간·주간 사용량, 그리고 다른 계정들 (최근에 쓴 순)
- **3행:** 켜 둔 기능 (`cc-baton toggle`). 켜져 있는데 실제로 안 돌면 경고

## 할 수 있는 것

- **`/swap <계정>`**: 지금 대화를 다른 계정으로 옮깁니다. Ctrl+D 를 누르면 트랜스크립트 전체를 가진 채 그 계정에서 이어집니다. 모델을 부르기 전에 훅이 처리하므로, 사용 한도에 걸린 뒤에도 동작합니다.
- **그룹 확인**: 계정에 그룹(`work`, `personal` …)을 정해 두면, 그룹이 다른 계정으로 옮길 때 한 번 묻습니다. 업무 대화가 실수로 개인 계정으로 넘어가지 않게 합니다.
- **계정 피커**: `baton` 을 실행하면 계정마다 남은 사용량이 보여서, 여유 있는 계정으로 시작할 수 있습니다.
- **절전** (선택): 오래 쉬는 세션의 프로세스를 꺼서 메모리를 돌려받습니다. 탭과 대화는 남고, 아무 키나 누르면 이어집니다. `/sleep` 은 바로 재웁니다.
- **한도 도달 시 자동 스왑** (선택, 기본 꺼짐): 한도에 걸리면 여유 있는 계정으로 스왑을 예약해 둡니다.

## 설치

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv 가 없을 때만
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && ~/.local/bin/cc-baton install
```

새 터미널을 열고 **`baton`** 을 실행하세요. 처음 실행하면 짧은 설정(언어, 계정 그룹, HUD, 절전, 자동 스왑)을 거친 뒤 계정 피커가 뜹니다.

`cc-baton install` 은 아무것도 묻지 않고, 여러 번 돌려도 결과가 같습니다. `/swap`·`/sleep` 명령, `~/.zshrc` 의 표시 블록, `~/.claude/settings.json` 의 훅을 넣습니다(먼저 백업하고, JSON 이 깨져 있으면 손대지 않습니다). statusline 은 비어 있을 때만 넣고, 이미 쓰는 게 있으면 설정 위저드가 미리보기를 보여 주고 묻습니다.

claude-swap 에 계정이 하나 이상 등록돼 있어야 합니다. 처음이라면 Claude Code 에 로그인한 상태에서 `cswap add`, 다음 계정으로 `/login` 한 뒤 다시 `cswap add` 하세요.

### AI 에이전트가 설치할 때

그대로 실행하세요:

```sh
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && ~/.local/bin/cc-baton install
```

`~/.claude/settings.json` 과 `~/.zshrc` 는 직접 고치지 마세요. `cc-baton install` 이 출력한 요약을 사용자에게 보여 주고, 새 터미널에서 `baton` 을 실행해 설정을 마치라고 전하세요.

## 명령

| 명령 | 하는 일 |
|---|---|
| `baton [계정] [-- claude 인자]` | 계정을 골라(또는 지정해) Claude Code 실행. `/swap`·깨우기 때 다시 실행 |
| `baton --wake` | 재운 세션을 골라 원래 자리에서 깨우기 |
| `/swap <계정>` (Claude Code 안) | 대화 그대로 다른 계정으로. 그다음 Ctrl+D |
| `/sleep` (Claude Code 안) | 이 세션을 지금 재우기 |
| `cc-baton setup` | 설정 위저드 다시 실행 |
| `cc-baton toggle update\|hib\|swap on\|off` | 기능 켜기/끄기 |
| `cc-baton lang en\|ko` | 화면 언어 |
| `cc-baton upgrade` | cc-baton 을 최신으로 올리고 다시 연결 |
| `cc-baton uninstall` | cc-baton 이 넣은 것 전부 제거 |

## 제거

```sh
cc-baton uninstall          # 설정·패키지를 지울지 묻습니다. --purge / --yes 로 질문 생략
```

cc-baton 이 넣은 것만 걷어내고, 원래 쓰던 statusline 을 되돌립니다. claude-swap 계정 데이터(`~/.claude-swap-backup`)는 절대 건드리지 않습니다.

## 쓰기 전에

- **약관**: 여러 계정으로 사용 한도를 넘나드는 게 Anthropic 약관에 맞는지는 직접 확인하세요. 자동 스왑은 기본으로 꺼져 있습니다.
- **업무·개인 계정**: 업무 대화를 개인 계정으로(또는 반대로) 옮기는 게 회사 정책에 어긋날 수 있습니다. 그룹 확인은 그 순간 한 번 멈추게 하려고 있습니다.
- **내부 형식**: claude-swap 의 상태 파일, Claude Code 의 statusline 입력·트랜스크립트 경로 중 공식 API 가 아닌 부분을 읽습니다. 둘 중 하나가 업데이트되면 일부가 깨질 수 있고, 그때 cc-baton 은 멈추지 않고 빈 값으로 표시합니다.

확인한 환경: macOS 15.3, Claude Code 2.1.285, claude-swap 0.25–0.26, zsh, Warp. truecolor 터미널이 필요합니다. Python 3.12 는 uv 가 알아서 받습니다.

## 동작 방식

- `/swap` 은 요청만 남기고, Claude Code 가 종료되면 `baton` 이 그 대화 하나의 트랜스크립트(와 큰 도구 출력·서브에이전트 기록이 든 옆 폴더)를 대상 계정 프로필로 복사해 `claude --resume` 으로 이어서 실행합니다. 다른 대화와 프롬프트 기록은 계정별로 따로 남습니다.
- 피커·HUD·사용량 숫자는 claude-swap 의 로컬 캐시에서 읽습니다. statusline 은 네트워크를 기다리지 않습니다. 캐시가 오래되면 claude-swap 에 백그라운드 갱신을 맡기고, 그동안은 마지막 값을 보여 줍니다.

## 개발

```sh
uv tool install -e . && cc-baton install      # 클론에서. 고치면 바로 반영
uv run --group dev python -m pytest tests -q
```

테스트마다 가짜 `$HOME` 에 claude-swap 상태를 만들고 cc-baton 을 실제 프로세스로 실행합니다(피커와 위저드는 가상 터미널). 실제 환경은 건드리지 않습니다.

## 라이선스

MIT
