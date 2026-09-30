# cc-baton 작업 계획

여러 Claude Code 계정을 오가며 대화를 원문 그대로(트랜스크립트 원본, 요약 아님) 넘기고, 지금 계정·다른 계정 잔량·컨텍스트를 HUD로 보여주는 도구를 공개 가능한 형태로 만든다.

## 핵심 가치

- 계정 간 맥락 100% 전달: 트랜스크립트를 대상 계정 프로필로 복사하고 `--resume`
- 현재 사용 중인 계정 확인 (팀/개인 혼동 방지)
- 다른 계정 잔량 파악 (계정 N개)
- 컨텍스트·모델·effort 파악

## 확정 결정 (2026-09-30)

| 항목 | 결정 |
|---|---|
| 플랫폼 | macOS 전용 |
| 대상 도구 | Claude Code 전용. Codex 핸드오프는 v2 (원문 텍스트 주입 방식, 같은 세션 resume 은 불가) |
| 배포 | `uv tool install` 패키지. claude-swap 은 의존성으로 선언 (코드 복사 금지), 확인한 버전 범위로 고정 |
| 명령 구조 | `cc-baton <하위명령>` 하나로 통합: `swap` `pick` `toggle` `hib` `update` `statusline` `install` `uninstall` |
| 실행 명령 | `baton` (셸 함수). `cc` 는 macOS 기본 C 컴파일러라 공개판에서 쓰지 않는다. 작성자 환경은 별칭으로 유지 가능 |
| 셸 | zsh (macOS 기본). bash 는 요청이 오면 |
| 언어 | 화면 문구·README 영어 기본, `README.ko.md` 병행 |
| 기능 토글 | `cc-baton toggle update\|hib\|swap on\|off` 명령으로만. HUD 는 표시 전용 |
| HUD 디자인 | effort: 시그널 미터 + 타이밍 타워 Lv 칩 / 3행: 토글 글리프 + 블록 스위치, 절전 아이콘 ⏾ |
| 이름 | cc-baton. 이름에 "claude" 를 넣지 않는다 (상표) |
| 라이선스 | MIT. README 에 claude-swap 출처 표시, 비공식 확장임을 명시 |

## 단계

### 0. 결정 — 완료

### 1. 동작 고정 테스트 (S) — 완료

`tests/` 30개. 가짜 HOME 에 claude-swap 상태를 만들고 스크립트를 실제 프로세스로 돌린다 (피커는 pty).
실행: `uv run --group dev python -m pytest tests -q`


구조를 바꾸기 전에 지금 동작을 테스트로 고정한다.

- statusline: 샘플 입력 → 출력 비교 (effort 단계별, 폭별 줄바꿈, 3행 상태)
- 스왑 복사: 가짜 프로필 두 개로 트랜스크립트 복사
- 피커: pty 폭 테스트
- toggle: 켜기/끄기, 설정 파일을 못 읽을 때 덮어쓰지 않음

### 2. 패키지 구조로 재편 (M) — 완료

- `pyproject.toml` + `cc_baton/` 모듈, `cc-baton` 진입점 하나
- claude-swap 의존성 + 버전 범위 고정
- 작성자 환경은 `uv tool install -e` 로 개발 설치해 레포 수정이 바로 반영되게 유지
- 완료 기준: 1단계 테스트 통과, 작성자 환경에서 HUD·스왑·절전 동작

### 3. 설정 통합 + 개인 흔적 제거 (S~M) — 완료

- 설정 파일 하나로: `~/.config/cc-baton/config.json` (`XDG_CONFIG_HOME` 존중). 예전 `~/.claude/cc-accounts.json` 은 처음 읽을 때 복사해 오고 원본은 남긴다
- 브랜치명에서 지우는 본인 이름: 고정값(`<user>_`) → 로그인 이름(`getpass.getuser()`). 설정 없이 누구에게나 맞는다
- launchd 라벨: `com.<user>.cc-hib` → `io.github.juunghyun.cc-baton.hib`
- 5단계로 넘김: 상태 폴더 이름(`~/.local/state/cc-swap`, `cc-hib`) 통일, hib 로그 경로(`~/.claude/cc-hib.log`). 열려 있는 탭의 옛 `cc` 함수가 marker 경로를 들고 있어서, 설치 명령으로 탭을 새로 여는 시점에 함께 옮긴다

### 4. 여러 계정 지원 (M) — 완료

- 계정 설정 `"group"`(자유 이름) + `"color"`(선택, 256색 번호 또는 `#RRGGBB`). 색이 없으면 그룹이 처음 나온 순서대로 팔레트. 0.1 이전 `"kind"` 도 그룹 이름으로 읽는다
- 경계 교차 = 그룹이 다른 계정으로의 이동
- HUD 2행 다른 계정: 최근 사용순 2개 + "외 k개". 최근 사용 = `<프로필>/sessions/*.json`·`history.jsonl` 수정 시각 중 최신 (stat 만). 기록 없는 계정은 등록 순서로 뒤에

### 5. 설치·제거 (M) — 구현 완료 (새 macOS 사용자 계정 검증은 8단계 공개 전에)

사용자 시나리오: 에이전트에게 레포 주소를 주고 "설치해줘" 하는 경우가 많다. 그래서 설치는 질문 없이 끝나고,
선택이 필요한 설정은 사용자가 처음 `baton` 을 실행할 때 위저드로 받는다 (소프트랜딩).
근거: 비슷한 도구 16개 조사 (ccstatusline, claude-hud, gstack, claude-mem, gsd-core, SuperClaude, aider 등).

**설치 (README)** — uv 두 줄
```
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv 가 없을 때만
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && cc-baton install
```
- `--python 3.12`: uv 가 Python 까지 받아서 사용자는 Python 버전을 신경 쓰지 않는다
- uv 를 방금 깔아 `~/.local/bin` 이 아직 PATH 에 없는 셸도 `cc-baton install` 이 돌게 한다 (절대경로 또는 `uv tool update-shell`)
- README 에 "For AI agents" 섹션: 실행할 명령 그대로 + "settings.json·zshrc 를 직접 고치지 말고, 끝나면 사용자에게 새 터미널에서 `baton` 을 실행하라고 전해라"

**`cc-baton install` (질문 없음, 여러 번 돌려도 같은 결과)** — 안전한 것만
- `/swap`·`/sleep` 명령 파일을 `~/.claude/commands/` 에 (cc-baton 표시 포함)
- `~/.zshrc` 에 `# >>> cc-baton >>>` 표시 블록 한 번 (`baton` 함수)
- settings.json: 쓰기 전 시각별 백업, 깨진 JSON 이면 멈춤. 훅은 표시를 달아 추가만(중복 없음).
  statusline 은 비어 있을 때만 넣고, 있으면 원래 값을 기록해 두고 위저드로 넘김
- launchd 는 등록하지 않는다 (위저드에서만)
- 바꾼 것을 전부 출력. 옛 상태 폴더(`~/.local/state/cc-swap`, `cc-hib`)·hib 로그 이관도 여기서

**첫 실행 위저드 (`baton` 을 처음 실행할 때, `cc-baton setup` 으로 다시 실행)** — 끝나면 바로 계정 피커
- HUD: 기존 statusline 이 있으면 미리보기를 보여주고 교체/유지
- 계정 그룹: claude-swap 에 등록된 계정마다 그룹 이름(엔터 = 건너뛰기). 계정이 1개면 추가 방법 안내
- 절전: 켜기/끄기 + 유휴 기준. 켜면 launchd 등록
- 한도 도달 시 자동 스왑: 기본 꺼짐. 켜려면 약관 주의 문구 확인
- 따로 설치된 claude-swap 이 있으면 정리 제안 (계정 데이터는 그대로 씀)
- 자동 업데이트는 묻지 않는다: npm 으로 깐 Claude Code 면 켜짐, 공식 설치본이면 꺼짐(자체 자동 업데이트가 있음)

**claude-swap**: cc-baton 이 함께 설치하고 `cswap` 도 노출(`--with-executables-from claude-swap`).
cc-baton 은 자기와 같이 설치된 버전만 부른다.

**명령 이름**: Claude Code 업데이트는 `cc-baton claude-update`(래퍼 내부용), `cc-baton upgrade` 는 cc-baton 자신을
최신으로 올리고 `install` 을 다시 돌린다. 토글 이름 `update` 는 그대로.

**`cc-baton uninstall` (명령 하나)**
- 표시 단 훅·명령 파일·zshrc 블록·launchd 만 정확히 걷고, 설치 때 기록해 둔 원래 statusline 을 되돌린다
- 설정·기록(그룹 설정, 상태, 로그): 지울지 묻고 기본은 남김. `--purge` 면 전부
- claude-swap(`cswap`): 따로 남길지 묻는다. 계정 로그인 데이터(`~/.claude-swap-backup`)는 어떤 경우에도 지우지 않는다
- 마지막에 패키지 자체 삭제를 묻는다

**완료 기준**: 작성자 환경의 수동 연결을 걷고 `install` + 위저드만으로 같은 동작. 새 macOS 사용자 계정에서 처음부터 설치·제거.

### 6. 동작 보강 (S) — 완료

- 스왑 시 `<sid>/` 하위 폴더(떼어 저장한 도구 출력, 서브에이전트 기록)까지 복사
- 자동 스왑 기본값 꺼짐 (5단계에서 기능 기본값 전부 꺼짐 + 위저드로 켬). 약관은 공개 전 작성자 확인
- HUD: 잔량 값이 비어도 오류 한 줄로 떨어지지 않음 (`—` 표시)
- HUD 절전 기준: hib tick 이 실제로 쓴 값(메모리 부족 시 30분)을 기록하고 HUD 가 표시
- 피커: 그룹 열 9칸 고정(넘치면 `…`), 창을 줄인 뒤 다시 그릴 때 접힌 줄만큼 올라가고 아래를 비움,
  줄 끝을 `\r\n` 으로 해서 선택 후 메시지가 줄 맨 앞에서 시작
- 위저드 5단계(claude-swap 정리) 결과 문구

### 7. 문서 + CI (S~M)

- 화면 문구 영어화 (지금은 한국어). 한국어는 README.ko.md 로
- README (영어) + README.ko.md: 핵심 가치, 설치, 화면 GIF, 요구사항(claude-swap, Python 3.12+, zsh, truecolor 터미널), 약관·팀 계정 주의, 호환 확인 버전
- GitHub Actions: macOS 테스트, 비밀값 스캔

### 8. 공개 (S)

- 비밀값 스캔 → 새 사용자 계정 설치 테스트 → public 전환 → `v0.1.0` 태그
- claude-swap 원작자에게 알림 (이슈/디스커션). 가능하면 상태 JSON 출력 공식 명령 요청
- PyPI 등록은 반응을 보고

## 알려진 위험

- claude-swap 내부 파일(`sequence.json`, `cache/usage.json`)과 Claude Code 내부 형식(statusline 입력의 `effort`, 트랜스크립트 경로)은 공식 계약이 아니라 업데이트로 깨질 수 있다. 형식이 바뀌면 조용히 빈값으로 떨어지는 방어를 유지하고, 호환 확인 버전을 README 에 적는다
- 팀 계정 ↔ 개인 계정 이동은 회사 정책 문제가 될 수 있다. 경계 교차 승인을 안전장치로 유지한다
