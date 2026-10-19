# cc-baton Go 전환 계획

기준일 2026-10-19. 결정 사항은 `claude-swap-spec.md` §8 을 따른다.

**목표 지점: M3**(claude-swap 의존성 제거). M4·M5 는 M3 이 끝난 뒤 확인하면서 진행할지 정한다(아래 "M3 뒤 판단").

## 지금 위치

| 트랙 | 범위 | 상태 |
|---|---|---|
| A. claude-swap → Go (`ccswap`) | cc-baton 이 쓰는 계정 기능 | 1단계(list/status 읽기) ✓ 실데이터 비교 · 3a(alias/map) ✓ 실데이터 · 3b(사용량 받기) ✓ 실데이터(2026-10-19: 차례 아닐 땐 안 받고, 차례에 Go 가 받아 기록, Python·statusline 이 그대로 읽고 다시 받지 않음, usage.json 외 변경 없음) · 3c(백업 맞추기) ✓ 코드·가짜 HOME, 실키체인 쓰기/읽기/삭제는 시험 항목으로 확인, 실데이터 테스트 대기 · 3d(run) ✓ 코드·가짜 HOME, 실제 바이너리 exec 확인 · 3e(add/add-token/remove) ✓ 코드·가짜 HOME · 3f(쉬는 계정 갱신) ✓ 코드·가짜 HOME → **M1 코드 완료**. 남은 M1 조건: 설치 마법사 별칭 테스트를 Go cswap 으로 통과(M2 준비 때) |
| B. cc-baton 본체 → Go | Python 3,462줄(11개 모듈), 테스트 73개 + e2e 설치 | 시작 전 |

## 마일스톤

각 마일스톤 끝은 그대로 멈춰도 되는 안정 상태다. 다음으로 넘어갈지는 그때 정한다.

### M1. Go cswap 완성 (트랙 A)
3b 실데이터 → 3c 활성 계정 백업 맞추기·프로필 토큰 채택 → 3d `run` → 3e `add`·`add-token`·`remove` → 3f 쉬는 계정 토큰 갱신.
- cc-baton 이 실제로 부르는 모양 그대로: `run <계정> [-- 인자…]`, `add`(대화형), `alias N 이름`, `list --json`.
- 완료 조건: 단계마다 가짜 HOME 테스트 + 일부러 망가뜨리기 검사 통과(키체인 쓰기가 있는 단계는 시험 항목으로 실키체인 확인). 단계별 실데이터 테스트는 하지 않고 M2 직전 일괄 실테스트로 모은다(2026-10-19 결정). Python 테스트 중 cswap 을 실제로 부르는 건 설치 마법사 별칭 테스트(`test_install.py:173`) 하나라, 그건 Go cswap 으로 바꿔 끼워 통과시키고 나머지 호환은 실데이터 `run`·`add` 로 확인한다.

### M2. baton 이 Go cswap 을 쓰도록 전환
- 전환 직전 일괄 실테스트: 파일 백업 → 체크리스트대로 3c~3f 를 실계정에서 확인(3c 처럼 자연히 안 생기는 상황은 `usageRefresh` 를 잠깐 꺼서 만든다) → 통과하면 전환.
- 바꾸는 곳: `state.CSWAP_BIN` 한 곳(statusline·pick·baton.zsh·설치 마법사가 모두 이걸 통한다). Python claude-swap 은 지우지 않고 남겨 둔다.
- 되돌리기: 그 한 줄을 되돌린다.
- 완료 조건: 7일 실사용 동안 로그아웃 0회, `claude-swap.log` 에 `invalid_grant` 0건, 사용량 갱신 정상, run·swap·재우기 정상. 이 기간 Python cswap 실행 기록 없음.

### M3. claude-swap 의존성 제거
- `pyproject.toml` 에서 `claude-swap` 제거, 설치가 번들 cswap 대신 Go 바이너리를 `cswap` 으로 링크.
- Go 바이너리 배포 경로가 필요하다: GitHub 릴리스에 darwin arm64/amd64 바이너리를 CI 로 올리고, 설치가 그걸 받는다(M5 배포 인프라를 여기서 먼저 만든다).
- 완료 조건: 새 맥 e2e 설치(CI)가 claude-swap 없이 통과. 기존 `~/.claude-swap-backup` 그대로 동작(이전 변환 없음).
- **여기가 원래 목표("claude-swap 없이 내 자산")의 도달점.**

## M3 뒤 판단 (여기서부터는 확정 아님)

M3 완료 시점에 다음을 보고 M4 착수 여부를 정한다.
- statusline 처럼 자주 도는 명령이 Python 이라 느린 정도(지금 118ms, 그중 git 대기 93ms)
- Go cswap 운영 중 나온 문제 수와 유지보수 부담
- `baton.zsh`·mods(TypeScript)를 Go 바이너리에 실어 그 경로로 동작하는지 미리 확인한 결과

### M4. cc-baton 본체 Go 이식 (트랙 B)
- 방식: Go `cc-baton` 이 옮긴 하위 명령은 직접 처리하고, 아직 안 옮긴 건 Python 으로 넘긴다. 모듈 하나씩 바꿔도 매번 전체가 동작한다.
- 순서(효과 큰 것·위험 낮은 것 먼저): statusline(10초마다 실행, 체감 속도) → state·toggle·i18n → pick → swap → hib → install·setup·uninstall·upgrade → cli.
- `baton.zsh`, 슬래시 명령 md, mods(TypeScript) 는 그대로 두고 Go 바이너리에 `go:embed` 로 싣는다.
- 합격 기준: conftest 의 실행 명령을 Go 바이너리로 바꿔 기존 테스트 73개 통과. 옮긴 모듈마다 실사용 1~2일.

### M5. 배포 전환과 Python 제거
- 설치: 한 줄 설치(릴리스 바이너리 받기). `upgrade` 는 최신 태그 바이너리로 교체.
- 기존 uv 설치에서 넘어오는 경로: 새 설치가 uv 판을 정리하고 설정·상태(`~/.config/cc-baton`, `~/.claude-swap-backup`)는 그대로 쓴다.
- CI: 테스트를 Go 로, e2e 설치를 Python 없는 기본 PATH 로.
- README·한국어 README 갱신, Python 코드 삭제, v0.2.0 태그.

## 전체 종료 조건

1. 실행에 Python·uv·claude-swap 이 필요 없다. 기본 PATH 만 있는 새 맥에서 설치 한 줄로 모든 기능이 동작한다(CI e2e).
2. 기존 테스트 73개(실행 명령만 Go 로) + Go 단위 테스트가 통과한다.
3. 기존 데이터(`~/.claude-swap-backup`, `~/.config/cc-baton`)를 변환 없이 쓴다.
4. 마지막 전환 뒤 7일 실사용 동안 로그아웃이 없다.
5. 릴리스 태그가 있고 README 가 새 설치를 안내한다. main 에 Python 코드가 없다.

## 전환 내내 지키는 것

- 메인 폴더(`~/personal/cc-baton`)는 editable 설치라 실사용 코드다. 브랜치를 바꾸지 않는다. 작업은 `cc-baton--go-port` 워크트리에서 한다.
- 실데이터 테스트는 M2 직전에 몰아서, 사용자 확인 후 파일 백업 뒤에. 키체인 토큰은 백업하지 않는다(복구는 재로그인).
- Python 과 Go 는 같은 잠금 파일·임대를 쓴다. 전환 전에도 둘이 같은 토큰을 두 번 쓰지 않는다.
- 단계마다 커밋하고 `feature/go-port` 에 푸시. main 병합은 M2(전환) 직전에.
