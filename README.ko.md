<div align="center">

<img src="docs/assets/logo.svg" alt="" width="96" />

# cc-baton

**내 Claude Code 계정끼리 대화를 옮겨도, 한 글자도 잃지 않습니다.**

[![CI](https://github.com/juunghyun/cc-baton/actions/workflows/ci.yml/badge.svg)](https://github.com/juunghyun/cc-baton/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8250DF?style=flat-square)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-57606A?style=flat-square&logo=apple)](#설치)

[English](README.md) | 한국어

</div>

팀 시트와 개인 요금제는 서로 다른 Claude 계정이고, 대화는 보통 시작한 계정에 묶여 있습니다. `/swap personal` 을 치고 Ctrl+D 를 누르면, 같은 대화가 트랜스크립트째로 다른 계정에서 이어집니다. statusline HUD 에서 지금 어느 계정인지 항상 보여서, 엉뚱한 계정에서 작업하는 일이 없습니다.

![Claude Code 에서 계정을 바꿔도 대화가 그대로 이어지는 장면](docs/demo-swap.gif)

## 설치

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv 가 없을 때만
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && ~/.local/bin/cc-baton install
```

새 터미널을 열고 **`baton`** 을 실행하세요. 처음 실행하면 몇 가지(언어, 계정 그룹, HUD, 절전, 자동 스왑)를 묻고 계정 피커가 뜹니다. 함께 설치되는 [claude-swap](https://github.com/realiti4/claude-swap) 에 계정이 하나 이상 있어야 합니다. Claude Code 에 로그인한 상태에서 `cswap add` 를 실행하세요.

`cc-baton install` 은 아무것도 묻지 않고, 여러 번 돌려도 결과가 같습니다. `~/.claude/settings.json` 은 먼저 백업하고, 이미 쓰는 statusline 은 묻지 않고 바꾸지 않습니다.

<details>
<summary>AI 에이전트로 설치한다면</summary>

위 두 줄을 그대로 실행하세요(`uv --version` 이 되면 첫 줄은 건너뜁니다). `~/.claude/settings.json` 과 `~/.zshrc` 는 직접 고치지 마세요. `cc-baton install` 이 출력한 내용을 사용자에게 보여 주고, 새 터미널에서 `baton` 을 실행해 설정을 마치라고 전하세요.

</details>

## 왜 cc-baton 인가

**요약이 아니라 대화 전체가 옮겨 갑니다.** 트랜스크립트 파일 자체를 다른 계정으로 복사해서 이어 갑니다. 큰 도구 출력과 서브에이전트 기록도 함께 옮겨 가고, 다른 대화는 원래 계정에 그대로 남습니다.

**지금 어느 계정인지 항상 압니다.** 입력창 아래 세 줄에 계정과 그룹, 계정마다의 사용량, 켜 둔 기능이 보입니다.

![HUD 각 부분의 의미](docs/assets/hud.ko.png)

**엉뚱한 계정으로 넘기기 어렵습니다.** 계정에 `work`, `personal` 같은 그룹을 정해 두면, 그룹이 다른 계정으로 옮길 때 먼저 묻습니다.

**쉬는 탭이 메모리를 먹지 않습니다.** 선택 기능입니다. 오래 쉬는 세션은 프로세스를 멈추고, 아무 키나 누르면 대화 그대로 돌아옵니다. `/sleep` 은 바로 재웁니다.

**계정 하나를 다 쓰면 알아서 넘겨 줍니다.** 선택 기능이고 기본은 꺼져 있습니다. 내 계정 하나가 사용 한도에 걸리면 다른 내 계정으로 스왑을 예약해 두어서, Ctrl+D 만 누르면 됩니다.

## 명령

| 명령 | 하는 일 |
|---|---|
| `baton` | 계정을 골라 Claude Code 실행 (`baton work` 처럼 지정도 가능) |
| `/swap <계정>` | 이 대화를 다른 계정으로 옮김, 그다음 Ctrl+D |
| `/sleep` | 이 세션을 지금 재우기 |
| `baton --wake` | 재운 세션 깨우기 |
| `cc-baton toggle update\|hib\|swap on\|off` | 기능 켜기·끄기 |
| `cc-baton setup` · `lang en\|ko` · `upgrade` | 설정 다시 · 화면 언어 · cc-baton 업데이트 |

## 제거

```sh
cc-baton uninstall
```

cc-baton 이 넣은 것만 걷어내고, 원래 쓰던 statusline 을 되돌립니다. claude-swap 계정 데이터(`~/.claude-swap-backup`)는 절대 건드리지 않습니다.

## 쓰기 전에

- **본인 계정끼리만 쓰세요.** 계정이나 로그인 정보를 다른 사람과 나눠 쓰는 것은 Anthropic [소비자 약관](https://www.anthropic.com/legal/consumer-terms)에서 허용하지 않습니다.
- **계정마다 한도는 그대로입니다.** cc-baton 은 Claude Code 가 내 계정 중 어느 것을 쓸지만 바꿀 뿐, 한도를 늘리거나 초기화하지 않습니다. 자동 스왑은 기본으로 꺼져 있습니다. 켜기 전에 Anthropic [사용 정책](https://www.anthropic.com/legal/aup)과 [Claude Code 약관](https://code.claude.com/docs/en/legal-and-compliance)을 읽고 직접 판단하세요.
- **업무 계정은 회사 규칙을 따릅니다.** 팀·엔터프라이즈 시트는 소속 조직의 계약을 따릅니다. 업무 대화를 개인 계정으로 옮기는 게 회사 정책에 어긋날 수 있고, 그룹 확인이 그 전에 한 번 묻습니다.
- **로그인은 Claude Code 에 맡깁니다.** cc-baton 은 수정하지 않은 Claude Code 를 실행할 뿐, 로그인 정보를 읽거나 저장하지 않습니다. 내 컴퓨터에서 로그인을 바꾸는 일은 claude-swap 이 합니다.
- claude-swap 의 상태 파일과 Claude Code 의 비공식 부분을 읽습니다. 확인한 환경: macOS 15.3, Claude Code 2.1.285, claude-swap 0.25–0.26.

## 더 보기

![계정 피커, effort 단계별 HUD, 그룹 확인](docs/demo.ko.gif)

개발: 클론에서 `uv tool install -e . && cc-baton install`, 테스트는 `uv run --group dev python -m pytest tests -q`.

## 만든 사람의 말

팀 시트와 개인 요금제를 매일 오갑니다. 작업이 한 계정에서 다른 계정으로 옮겨 갈 때마다 대화는 원래 계정에 남아서, 처음부터 다시 설명해야 했습니다. cc-baton 은 그 흐름을 잇기 위해 만든 가장 작은 도구입니다.

cc-baton 은 Anthropic·claude-swap 과 관계없고 그들이 보증하지 않는 비공식 도구입니다. Claude 와 Claude Code 는 Anthropic 의 제품입니다. MIT 라이선스.
