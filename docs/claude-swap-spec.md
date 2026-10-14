# claude-swap 0.26.0 → Go 포팅 v1 스펙 (cc-baton 내장용)

소스 기준: `~/.local/share/uv/tools/cc-baton/lib/python3.12/site-packages/claude_swap/` (이하 경로 생략, `file.py:line`).
대상 환경: macOS 단일 사용자, 백업 루트 `~/.claude-swap-backup`, 계정 2개(1=forcomfe81@gmail.com 세션 프로필, 2=ljh0327@midasit.com 기본 로그인).
토큰/시크릿 값은 이 문서에 없다. 키 이름·파일명·서비스명만 적었다.

---

## 0. 요약: 로그 이벤트의 정체 (질문에 대한 답)

| 로그 | 횟수 | 만드는 코드 경로 | 이유 |
|---|---|---|---|
| `Resynced account 2's backup to the rotated live credential (rotation completed outside a collect pass).` | 76 | `_collect_usage_entries` → `_fetch_account_usage` (active) → `_fetch_active_usage` fresh-token fast path → `_resync_rotated_backup` → `_write_account_credentials` (switcher.py:4048-4069, 4541-4669) | 계정 2는 기본 프로필(`~/.claude.json`)의 라이브 계정이다. 기본 프로필에서 도는 Claude Code가 액세스 토큰을 약 8시간마다 회전시킨다(로그상 Resynced 간격 중앙값 8.12h). 회전하면 refresh token이 바뀌어 백업(Keychain `claude-swap`/`account-2-…`)의 lineage fingerprint가 라이브와 달라진다. cc-baton statusline이 띄운 `cswap list --json`이 라이브 토큰으로 usage를 성공적으로 받은 직후, lineage가 다르면 `/api/oauth/profile` 오라클로 소유권(uuid)을 확인한 뒤 락 아래에서 라이브를 다시 읽어 백업에 덮어쓴다. 목적: 백업이 이미 소비된 refresh token을 들고 있다가 나중에 POST되어 `invalid_grant` → 계정 격리되는 것을 막는 것(switcher.py:4054-4066 주석). 이어지는 `_post_backup_write(2)`는 `sessions/2-…` 디렉토리가 없어서 조용히 끝난다(switcher.py:2768-2770). |
| `Invalidated session credentials for account 1` | 76 (위와 같은 수는 우연. 시간이 2초 안으로 겹치는 건 2건뿐) | `_write_account_credentials(1, …)` → `_post_backup_write` → 라이브 세션 없음 → `_invalidate_session_credentials` (switcher.py:812-848, 783-807, 2755-2776) | 계정 1 **백업**이 바뀔 때마다 실행된다. 프로필에 살아 있는 claude가 없으면 프로필의 hashed Keychain 항목과 `.credentials.json`을 지워서 다음 `cswap run`이 새 백업으로 다시 bootstrap하게 만든다. 계정 1 백업을 쓰는 곳은 세 군데다. (a) `setup_session`의 pre-lock refresh(session.py:671-675). 76건 중 22건이 5초 안에 Bootstrapped로 이어진다. (b) usage 수집 중 비활성 계정 1의 토큰이 만료(또는 401)되어 consume gate가 POST → persist(switcher.py:4783-4788, 2351). (c) gate의 "세션 프로필이 더 새 세대" resync(switcher.py:2184-2215) 직후 POST persist. 이 경우 1초 간격으로 Invalidated가 2번 찍힌다(11쌍 관측). |
| `Bootstrapped session profile for account 1 at …/sessions/1-forcomfe81_gmail.com` | 25(+계정2 1건, 8/27 add-token 시절) | `cswap run 1`(baton.zsh:47-49) → `SessionManager.setup_session` → `_bootstrap` (session.py:641-800, 825-896) | 프로필이 reuse 검사(`claude auth status --json`)를 통과하지 못하면(대개 위의 invalidation으로 자격증명이 지워져서) bootstrap이 백업으로 다시 seed한다. 그 직전에 refresh POST를 한 번 하고(session.py:671), 그 persist가 Invalidated 로그를 하나 더 남긴다. 결국 "Invalidated → (≤1s) Bootstrapped" 쌍이 생긴다. |
| `Usage fetch failed for account 2: http-429, retry-after 3600s (usage-endpoint budget reached; backing off)` | 2 (8/27) | `oauth._log_usage_failure` (oauth.py:406-427). 429일 때 괄호 문구를 붙인다(417-425) | usage 엔드포인트는 non-first-party UA에 ~60분 trailing window 기준 약 28-30회의 요청 예산을 건다(poll_policy.py:1-62). 8/27 계정 추가·삭제를 반복하면서 폴링이 몰렸다. 429면 `backoffUntil = now + min(3600+900, 4500)`(usage_store.py:567-624, 764), `last429At`를 기록한다. 이후 한 시간 동안 폴링 간격 하한이 360s로 오르고 AIMD로 ×1.5씩 늘어난다(poll_policy.py:114-138, 246-252). |
| `Usage fetch failed …: network/timeout` | 다수 | 같은 함수. URLError/Timeout 분류(oauth.py:395-400) | 네트워크 단절. 지수 backoff 30s·2^(n-1), 최대 600s(usage_store.py:112-118, 531-536). |

결론: 이 로그들은 정상 동작이다. 다만 다음 두 가지는 비용이다.
(1) 계정 1 프로필이 쉬는 동안 usage 패스가 백업을 refresh하면 프로필이 무효화된다. 그러면 다음 실행 때 refresh grant를 하나 더 쓰고 다시 bootstrap한다.
(2) `setup_session`은 reuse하지 않는 모든 실행에서 grant 하나를 쓴다.

---

## 1. 온디스크 레이아웃 (v1 범위)

백업 루트: macOS는 `~/.claude-swap-backup`(paths.py:85-108). Linux XDG 분기는 무시해도 된다. 루트 하위 경로는 switcher.py:326-331에서 정한다.

| 경로 | 권한(관측/코드) | 쓰는 방식 | 락 | 비고 |
|---|---|---|---|---|
| `./` | 0700 (switcher.py:436-441 `_setup_directories`, add 시) | — | — | |
| `.lock` | 0644 (FileLock이 `open("w")`로 생성) | 빈 파일 | `fcntl.flock(LOCK_EX\|LOCK_NB)`를 0.1s 간격으로 폴링, 기본 10s 타임아웃(locking.py:28-59). bootstrap은 30s(session.py:206) | "slot lock". consume gate 재읽기·CAS, active refresh, resync, bootstrap, uuid backfill이 잡는다. **add/remove/alias/map은 이 락을 잡지 않는다**(그대로 재현하되, Go에서 잡아도 무해하다). |
| `sequence.json` | 0600 | `_write_json`: `sequence.<pid>.tmp`에 쓰고 → 다시 읽어 JSON 검증 → chmod 0600 → `shutil.move`(rename). fsync 없음(switcher.py:558-579) | 없음 | 읽기는 **strict**다. 존재하는데 파싱이 안 되면 `ConfigError`로 중단하고 절대 덮어쓰지 않는다(switcher.py:2832-2851, 443-496). |
| `mappings.json` | 0600 | mkstemp(`.mappings-*.tmp`) → fchmod 0600 → `os.replace`. 부모는 chmod 0700(mappings.py:120-142) | 없음 | 손상되면 `{}`로 본다(mappings.py:46-57). |
| `configs/.claude-config-{num}-{email}.json` | 0600 | `write_text` + chmod. **비원자적**(switcher.py:935-942) | 없음 | add 시점의 `~/.claude.json` **전체** 사본(관측 ~110KB). bootstrap은 `oauthAccount`, `theme`만 쓴다(session.py:855-889). 파일명의 email은 slug가 아닌 원문이다. |
| `credentials/` | 0700 | — | — | 현재 `.enc` 파일 없음. 백업은 전부 Keychain에 있다. |
| `credentials/.consume-{num}.lock` | 0644 | 빈 파일 | flock, 10s(switcher.py:2076-2088, 4188) | 슬롯별 refresh POST 직렬화. 이 락을 못 잡으면 `consume-busy`를 반환한다. |
| `credentials/.creds-{num}-{email}.enc` | 0600 | base64(UTF-8 JSON). mkstemp(`*.tmp`) → replace → chmod(credentials.py:1113-1138) | — | Keychain을 못 쓸 때의 fallback 백업. 읽을 때는 **.enc가 이긴다**(credentials.py:1162-1237). |
| `credentials/.creds-{num}-{email}.enc.prev` | 0600 | 같음 | — | 파일 모드일 때 직전 세대 보관(credentials.py:1454-1526). |
| `credentials/.unclaimed-{id}.enc`, `.unclaimed-manifest.json`, `.unclaimed-manifest.lock` | 0600 | entry를 먼저 쓰고 manifest는 그다음 `atomic_write_json`(credentials.py:1769-1793, 1681-1707) | manifest RMW는 `.unclaimed-manifest.lock`(flock)(credentials.py:1740-1767) | consume gate가 POST 후 persist에 실패했을 때 successor를 보관하는 stash. id = `YYYYmmddTHHMMSS-<sha256(creds)[:12]>-<hex6>`(credentials.py:1781-1786). 현재 없음. |
| `cache/` | 0700 | — | — | |
| `cache/usage.json` | 0600 | `atomic_write_json`: mkstemp(같은 dir, `.tmp`) → replace → chmod 0600, 부모 chmod 0700, symlink는 따라가서 쓴다(settings.py:443-485) | `cache/.usage.lock` flock, 10s(usage_store.py:836-837). 읽기는 락 없음 | 스키마는 §4.5 |
| `cache/update_check.json` | — | — | — | v1 범위 밖이니 건드리지 않는다. |
| `settings.json` | — | — | — | 현재 없음. 있으면 `autoswitch.threshold`(기본 90), `autoswitch.model`을 poll planning에 쓴다(switcher.py:1806-1822, settings.py:46, 217). |
| `.migrations.json` | 0600 | — | — | `{"version":int,"applied":{"macos_keyring_to_security":str}}`. v1은 읽지도 쓰지도 않는다(Python cswap이 함께 깔려 있으면 Python이 관리). |
| `claude-swap.log` | 0644 | RotatingFileHandler 1MB×3, 포맷 `%(asctime)s - %(levelname)s - %(message)s`(logging_config.py:23-55) | — | Go도 같은 파일에 append하면 진단 연속성이 유지된다(선택). |
| `sessions/` | 0755(관측) | — | — | |
| `sessions/{num}-{slug(email)}/` | 0700(session.py:867-869) | — | — | slug는 NFC 정규화 후 ASCII 영숫자와 `._-`만 남기고 나머지는 `_`(session.py:209-219). 예: `1-forcomfe81_gmail.com`. |
| `sessions/.{dirname}.cswap-stale-credentials` | — | touch | — | 지연 무효화 마커. 프로필 디렉토리의 **형제** 위치에 둔다(session.py:115-130). 읽기는 형제 위치와 구 child 위치 `…/{dir}/.cswap-stale-credentials` 둘 다(133-143). |
| 프로필 내부 `.credentials.json` | 0600 | bootstrap에서 `write_text`(비원자적)(session.py:871-874) | — | 평문 seed. Claude가 첫 쓰기 때 hashed Keychain으로 옮긴 뒤 지운다(관측: 현재 없음). |
| 프로필 내부 `.claude.json` | 0600 | 기존 내용에 `oauthAccount` 덮어쓰기, `hasCompletedOnboarding=true`, `theme` setdefault 후 `write_text`(session.py:876-892) | MCP mirror만 `<profile>/.claude.json.lock` 디렉토리 락(proper-lockfile)(session.py:1197-1243) | Claude Code 소유 파일. |
| 프로필 내부 `.cswap-shared.json` | 0600(mkstemp) | `{"items":[...],"mode":"symlink"}`, mkstemp(`.cswap-shared-*.tmp`) + replace(session.py:1439-1453) | — | 관측: items 5개 |
| 프로필 내부 `.cswap-mcp-mirror-v1` | — | touch | — | MCP mirror 채택 마커(session.py:108, 1276-1282) |
| 프로필 내부 `.cswap-mcp-displaced.json` | — | 1회성 stash | — | 현재 없음 |
| 프로필 내부 `settings.json`, `CLAUDE.md`, `skills`, `commands`, `agents`(`keybindings.json`) | symlink → `~/.claude/*`(resolve한 타깃) | — | — | `_sync_sharing`(session.py:1018-1135) |
| 프로필 내부 `sessions/{pid}.json` | Claude 소유 | — | — | **라이브 세션 판정의 유일한 근거**다(§3.6). |

Claude Code 쪽 파일(읽기 위주):
- `~/.claude.json`(또는 `~/.claude/.config.json`이 있으면 그것. paths.py:42-53): `oauthAccount.{emailAddress, organizationUuid, accountUuid, organizationName}`이 활성 계정 판정의 근거다(switcher.py:2871-2894). CLAUDE_CONFIG_DIR가 있으면 `$CLAUDE_CONFIG_DIR/.claude.json`.
- `~/.claude/.credentials.json`: 현재 없음(Keychain 전용). cswap은 Keychain 쓰기에 성공하면 **이미 있는 경우에만** 이 파일을 다시 써서 mtime을 갱신한다(credentials.py:1012-1038).
- Claude Code 락(디렉토리 mkdir 방식, proper-lockfile 호환, claude_locks.py): `~/.claude/.oauth_refresh.lock` → `~/.claude.lock` 순서로 잡고 stale 60s(46-50, 157-180). `~/.claude.json.lock`은 stale 10s(79-82, 183-187). 잡고 있는 동안 3s마다 utime으로 touch하고, 타임아웃은 락당 9s(55-61, 85-154).

### 1.1 JSON 스키마

`sequence.json`(관측 키 + 코드):
```
{
  "activeAccountNumber": int|null,   // add_account가 매번 해당 슬롯으로 덮어쓴다(switcher.py:3481,3636). add-token은 안 바꾼다.
  "lastUpdated": "YYYY-MM-DDTHH:MM:SSZ",   // models.py:209-211
  "sequence": [int,...],             // 정렬 유지(3633-3635)
  "accounts": {
    "<num 문자열>": {
      "email": str, "uuid": str, "organizationUuid": str, "organizationName": str,
      "added": str(ts),
      "alias"?: str,                 // normalize_alias: 소문자, ^[a-z0-9_.-]+$, 숫자만 금지, '-'로 시작 금지 (models.py:25-47)
      "kind"?: "api_key",            // add-token sk-ant-api…일 때만 (3836-3837)
      "disabled"?: true
    }
  }
}
```
add-token의 레코드는 uuid/org가 `""`다(3829-3835). `organizationUuid` 키가 없는 레코드는 `_migrate_org_fields`가 채운다(3331-3390). 현재 데이터는 이미 다 있다.

`mappings.json`: `{"schemaVersion":1,"mappings":{"<normalize_path(resolve+expanduser+normcase)>":{"email","organizationUuid","added"}}}`(mappings.py:26-37, 67-75).

`configs/*.json`: `~/.claude.json` 원문. 최소 필요 키는 `oauthAccount`, `theme`. add-token은 `{"oauthAccount":{"emailAddress":email,"accountUuid":"","organizationUuid":null,"organizationName":null}}`(switcher.py:3719-3726).

크리덴셜 JSON(Keychain 값 / .enc 디코드 값):
```
{ "claudeAiOauth": { "accessToken", "refreshToken", "expiresAt"(ms epoch), "scopes":[...], ...그 외 Claude가 넣은 필드(보존) },
  "mcpOAuth"?, "mcpOAuthClientConfig"?, "mcpXaaIdp"?, "mcpXaaIdpConfig"?, "pluginSecrets"?,  // machine-shared (credentials.py:199-205)
  "trustedDeviceToken"? }                                                                   // account-scoped (210-213)
```
setup-token: `{"claudeAiOauth":{"accessToken":tok,"scopes":["user:inference"]}}`(switcher.py:105, 3713-3718). refreshToken과 expiresAt이 없으므로 "절대 만료되지 않음"으로 본다(oauth.py:62-68: 비숫자 expiresAt → False).

---

## 2. Keychain

공통(macos_keychain.py):
- 바이너리는 반드시 `/usr/bin/security` 절대경로(60). 호출마다 타임아웃 5s(54).
- account 이름: `$USER` → `pwd.getpwuid(euid).pw_name` → `"claude-code-user"` 순(76-93). 이 머신은 `ljh0327`.
- 읽기: `security find-generic-password -a <acct> -w -s <svc>`. rc 0이면 stdout에서 **끝의 `\n` 하나만** 떼어 쓰고, rc 44는 없음(None), 그 외는 에러(107-134).
- 존재 확인(비밀 미접근): `security find-generic-password -a <acct> -s <svc>`. rc 0만 True다(137-156).
- 쓰기: `security -i`에 stdin으로 한 줄을 넣는다. `add-generic-password -U -a "<acct>" -s "<svc>" -X <hex(utf8 value)>\n`. 따옴표 안의 `\`와 `"`는 이스케이프한다(96-104). 줄 길이가 4096-64를 넘으면 argv 폴백 `security add-generic-password -U -a <acct> -s <svc> -X <hex>`(45, 159-202).
- 삭제: `security delete-generic-password -a <acct> -s <svc>`. rc 0과 44 모두 성공(205-225).

(a) Claude Code 라이브 OAuth 자격증명:
- 기본 프로필: service `Claude Code-credentials`, account `$USER`(credentials.py:117).
- `CLAUDE_CONFIG_DIR`가 있으면 `Claude Code-credentials-<sha256(NFC(raw env 값))[:8] hex>`. raw 문자열을 해시하며 resolve나 trailing-slash 정리는 하지 않는다(session.py:232-243). `CLAUDE_SECURESTORAGE_CONFIG_DIR`가 *정의돼* 있으면 그 값의 해시를 쓰고, 빈 값이면 접미사 없는 이름을 쓴다(credentials.py:69-107).
- `CLAUDE_CONFIG_DIR`가 기본 프로필을 가리키는 특수한 경우에는 해시 이름을 먼저 보고, 없으면 접미사 없는 이름을 본다(credentials.py:104-107).
- 관리형 API key는 service `Claude Code`(124). v1 대상 계정에는 api_key가 없다(`kind` 키 없음).
- **쓰기는 항상 접미사 없는 `Claude Code-credentials`에 한다**(credentials.py:974-981, 787-795). 읽기만 env를 따른다. §6의 R6 참고.

(b) 계정별 백업: service `claude-swap`, account `account-{num}-{email}`. 직전 세대는 `account-{num}-{email}.prev`(credentials.py:113, 1064-1065, 1457-1458). email은 원문이고 slug가 아니다. 삭제할 때는 레거시 별칭 `account-None-{email}`도 함께 지운다(1356-1368).

(c) 세션 프로필: `cswap run`은 `CLAUDE_CONFIG_DIR=str(session_dir)`로 exec한다(session.py:585-589). `session_dir = Path.home()/".claude-swap-backup"/"sessions"/f"{num}-{slug}"`(session.py:222-229, paths.py:85-87). Claude는 이 문자열을 해시해서 service 이름을 만든다.
- 현재 라이브 세션 값: `"/Users/ljh0327/.claude-swap-backup/sessions/1-forcomfe81_gmail.com"` → **`Claude Code-credentials-8a0172f8`**(로컬에서 sha256으로 계산. Keychain 조회로 확인하지는 않았다).
- cswap은 이 항목을 **절대 쓰지 않는다**. 읽기(session.py:339-394)와 삭제(255-269)만 한다. 평문 seed를 깔아 두면 Claude가 스스로 옮긴다(session.py:11-16).

Keychain 실패 처리(credentials.py:339-501):
- 첫 실패가 나면 그 프로세스는 파일 모드로 내려간다(`_keychain_usable_cache=False`, 60s 후 재검사 deadline).
- 라이브 읽기는 0.3s 간격으로 2회 재시도한다(132-133, 536-564).
- 라이브 읽기가 실패했는데 평문으로 메워졌으면 `degraded=True`다. 이 바이트의 refresh token은 **절대 소비하면 안 된다**(144-163, switcher.py:4105-4119, 4067).
- 백업 쓰기 중 Keychain이 실패하면 `.enc`를 쓰고 Keychain 사본은 best-effort로 지운다(credentials.py:1324-1345). Keychain 쓰기가 성공하면 남은 `.enc`를 반드시 지운다. 지우기 실패 시 같은 값으로 덮어쓴다(1140-1160).

---

## 3. 크리덴셜 라이프사이클 (상태 기계)

용어:
- `fp(c)` = `"sha256:"+hex(sha256(refreshToken))`. refreshToken이 없으면 `"sha256-full:"+hex(sha256(전체 문자열))`. 빈 값이면 None(oauth.py:43-59). lineage 비교, strike 바인딩, stash 키 모두 이 값으로 한다. Go도 바이트 단위로 같아야 한다(usage.json `struckFingerprint`와 호환).
- `expired(o)` = `now_ms + 300000 >= expiresAt`. expiresAt이 숫자가 아니면 False(oauth.py:17, 62-68).

### 3.1 백업 쓰기 공통 경로 `_write_account_credentials(num,email,creds)` (switcher.py:812-868)
1. `.prev` 보관: 현재 백업을 `_read_account_credentials_ex`로 읽는다. unreadable이면 경고만 남기고 보관은 생략한다. 현재 값이 비어 있지 않고 새 값과 다르면 Keychain `account-…​.prev`(파일 모드면 `.enc.prev`)에 저장한다. 실패해도 무시(credentials.py:1460-1526).
2. Keychain을 쓸 수 있으면 `set_password("claude-swap","account-{n}-{e}",creds)` 후 `.enc`를 정리한다(1324-1335). 실패하면 `.enc`를 쓰고 Keychain 사본을 지운다(1337-1345). `.enc` 쓰기까지 실패하면 **예외**(이 경우 무효화는 하지 않는다).
3. `_post_backup_write`(783-807): 그 슬롯 프로필에 라이브 PID가 있으면 stale 마커를 touch하고, 없으면 `_invalidate_session_credentials`를 부른다.
4. 3에서 OSError가 나면 stale 마커로 대체한다. 그것도 실패하면 ERROR 로그만 남긴다. **2 이후에는 절대 raise하지 않는다**(846-868). raise하면 호출자가 "persist 실패"로 읽고, 이미 쓴 grant를 다시 POST하게 된다.

### 3.2 add (`cswap add [--slot N] [--alias A]`) (switcher.py:3392-3644)
0. `_refuse_session_shell`: CLAUDE_CONFIG_DIR가 `sessions/` 아래이면 거부(6508-6537). `_setup_directories`, `_init_sequence_file`(`{"activeAccountNumber":null,"lastUpdated","sequence":[],"accounts":{}}`, 2821-2830), `_migrate_org_fields`.
1. 신원: `~/.claude.json` `oauthAccount`를 **한 번만 읽어** (email, org, accountUuid) triple을 얻는다(2871-2894). 없으면 "Please log in first" 에러.
2. 크리덴셜 캡처 `_read_capture_credentials`(660-737). env가 없으면 `_refuse_degraded_capture`로 라이브를 읽고, degraded면 거부한다(627-658). env가 있으면 그 프로필의 hashed 항목을 strict로 읽는다. 잠김이면 거부하고, 없으면(rc44) 평문으로 간다(session.py:339-394).
3. 가드 순서:
   - `_reject_live_api_key_capture`(3206-3219)
   - `_reject_foreign_credential_capture`: 액세스 토큰이 유효하면 `GET /api/oauth/profile`로 uuid(없으면 email)와 org를 대조한다. 불일치면 거부, 판정 불가면 Notice를 띄우고 통과(3078-3204).
   - `_reject_credential_drift_since_verify`: 다시 읽어 fp가 바뀌었으면 거부(3043-3076).
   - `~/.claude.json` 원문 읽기 → `_reject_identity_drift_since_verify`: triple을 다시 읽어 달라졌으면 거부(3020-3040).
4. 분기:
   - **slot 미지정 + 이미 등록된 (email,org)**: in-place 갱신(3426-3491). `_write_account_credentials` → `_write_account_config` → `usage_store.clear_dead_token` → alias 반영 → `activeAccountNumber=int(num)` → sequence 저장.
   - **새 슬롯 / 지정 슬롯**: 점유 슬롯이 다른 계정이면 y/N 확인(3528-3536). 그다음 displace/migrate 슬롯을 `_delete_account_files`로 지우고 sequence와 mappings를 정리한다(3594-3612). 이어서 백업 → config → clear_dead_token → 레코드 `{email, uuid, organizationUuid, organizationName, added, alias?}` 기록, sequence에 추가·정렬, `activeAccountNumber` 설정, 저장(3614-3639). 로그는 `Added account N: email (org: …)`.
5. 실패 지점: 크리덴셜/config를 읽기 전에는 파괴적 작업이 없다. displace 삭제 이후에 실패하면 그 슬롯은 이미 사라진 상태다. Python도 트랜잭션이 아니다.

### 3.3 add-token (switcher.py:3646-3852)
- 토큰 입력: `-`면 stdin 한 줄, 빈 값이면 getpass(3675-3682). `sk-ant-api…`이고 `{`로 시작하지 않으면 API key로 본다(credentials.py:166-177).
- email이 없으면 `setup-token-{slot}@token.local` 또는 `api-key-{slot}@token.local`(3696-3700). 형식 검사 정규식(431-434). 같은 email이 다른 kind로 있으면 거부(3221-3247).
- 이미 같은 (email,"")가 있으면 in-place: 백업·config·clear_dead_token·lastUpdated를 갱신한다. **activeAccountNumber는 바꾸지 않는다**(3729-3753).
- 아니면 add와 같은 displace/migrate 처리 후 레코드 `{email, uuid:"", organizationUuid:"", organizationName:"", added, kind?}`(3828-3844). 네트워크 호출은 없다.

### 3.4 refresh (토큰 엔드포인트)
- `POST https://platform.claude.com/v1/oauth/token`. 헤더 `Content-Type: application/json`, `User-Agent: claude-swap/1.0`. 바디 `{"grant_type":"refresh_token","refresh_token":<rt>,"client_id":"9d1c250a-e61b-44d9-88ed-5944d1962f5e"}`. 타임아웃은 기본 10s, 락을 잡은 active 경로에서는 6s(oauth.py:18-19, 112-153, switcher.py:4390-4392).
- 응답 처리: `access_token` → accessToken, `now_ms + expires_in*1000` → expiresAt, `refresh_token`이 있으면 교체, `scope`가 있으면 공백으로 나눠 scopes. 나머지 필드와 최상위 형제 키는 **보존한 채** 다시 직렬화한다(155-166). `account.uuid`, `account.email_address`, `organization.uuid`는 opportunistic identity로만 쓴다(198-224).
- 오류 분류(167-195):
  - JSON 파싱 불가나 dict가 아니면 `transient`. claudeAiOauth.refreshToken이 없으면 `no_refresh_token`(영구).
  - HTTP 400/401/403이고 JSON 최상위 `error` ∈ {`invalid_grant`, `invalid_client`}면 그 kind. 그 외는 전부 `transient`.
- 트리거:
  1. 비활성 계정 usage 수집에서 토큰이 만료됐을 때 → consume gate(oauth.py:631-669).
  2. 비활성 계정 usage가 401일 때 → gate 후 한 번 재시도(675-727).
  3. 활성 계정이 만료됐거나 서버가 401을 줬을 때 → `_fetch_active_usage` 복구 분기(switcher.py:4105-4509).
  4. `setup_session`의 pre-lock refresh(session.py:662-728).

#### 3.4.1 consume gate `consume_backup_grant(num,email,snapshot)` (switcher.py:2010-2424)
1. `CLAUDE_SECURESTORAGE_CONFIG_DIR`가 있으면 `store-unmirrored` 반환(2054-2066).
2. `.consume-{num}.lock`을 10s 안에 못 잡으면 `consume-busy`(2076-2088).
3. `.lock` 아래에서(2107-2216):
   - 백업을 `_ex`로 다시 읽는다. unreadable이면 `transient`.
   - **stash adopt**: manifest 행 중 `configSlot==num`이고 `consumedFp==fp(current)`인 것의 바이트로 백업을 쓰고 행을 retire한다(2452-2609). manifest가 unreadable/corrupt인데 entry 파일이 있으면 `stash-unreadable`.
   - current가 비었으면 `transient`(슬롯 삭제된 경우).
   - **프로필 우선**: 라이브 세션이 없고, 프로필 크리덴셜이 있고, stale 마커가 없고, 신원 drift가 없고, access/refresh 둘 다 있고, fp가 다르고, `prof.expiresAt > backup.expiresAt`이면 → 백업을 프로필 값으로 쓰고(이때 Invalidated 로그), 그 값을 POST 대상으로 삼는다.
   - `consumed_fp = fp(refresh_input)`.
   - `LockError`나 기타 예외는 `transient`(아직 아무것도 소비하지 않았다).
4. POST 생략 조건: 다시 읽은 accessToken이 snapshot과 다르고 아직 만료되지 않았으면, 그 값을 채택하고 POST하지 않는다(2256-2272).
5. POST(2274). 실패하면 `consumed_fp`를 붙여 그대로 반환(strike 바인딩용).
6. 성공하면 `.lock` 아래에서 CAS(2304-2353):
   - 백업 unreadable → stash `consume-gate-store-unreadable`
   - 백업이 비었음 → stash `consume-gate-slot-removed`
   - `fp(store_now) != consumed_fp` → stash `consume-gate-cas-conflict`하고 store 값을 채택
   - 그 외 → `_write_account_credentials(successor)`(이때 Invalidated 로그)
   - 락 실패 → stash `persist-lock-failed`. 기타 예외 → stash `persist-failed`. stash마저 실패 → `consume-gate-unpersisted` ERROR(2354-2390).
7. demoting reason(174-179)이면 `RefreshOutcome(creds,"transient",stashed=…)`, 아니면 `(creds,None)`(2391-2424).

#### 3.4.2 활성 계정 `_fetch_active_usage` (switcher.py:3982-4539)
- 액세스 토큰이 없으면 `no credentials` 센티널.
- 만료되지 않았으면 `GET usage`(is_active=True, 401 자동 refresh 없음).
  - 성공 + degraded 아님 → `_resync_rotated_backup`(§3.5).
  - probe verdict가 False면 `foreign credential` 센티널. 아니면 usage를 반환.
  - 401이면 `force_refresh` 기록을 만들고 복구 분기로 간다(4048-4103).
- 복구 분기:
  - degraded → defer(`keychain unavailable`/`token expired`)(4115-4119). SECURESTORAGE env → `store-unmirrored`(4130-4138).
  - 백업 lineage와 대조: `attributable` = 라이브==백업 또는 fp 동일. attributable도 아니고 백업도 쓸 수 없으면 경고 1회 후 defer(4145-4168).
  - 락 순서 `.consume-N.lock` → `.lock` → Claude `~/.claude/.oauth_refresh.lock` → `~/.claude.lock`(4187-4191). 그 아래에서:
    - 라이브를 다시 읽는다. None이면 defer. 신원 불일치면 defer(4192-4219).
    - 라이브가 이미 회전돼 있고(다른 값, 토큰 쌍 완비, 미만료) → 채택. verdict가 True거나 fp==backup_fp면 백업에도 쓴다(4220-4283).
    - 아니면 POST 대상을 고른다. 라이브 lineage==백업이면 라이브(rt 있으면) / 라이브가 비었으면 백업 / 라이브==수집 시 값이면 expiresAt 비교와 verdict / 그 외는 defer(4284-4365).
    - 백업이 이미 유효하면 POST 없이 restore(4367-4382). 아니면 POST(6s)한다. `invalid_grant`/`no_refresh_token`이면 소스가 그새 움직였는지 확인한다. 움직였으면 `refresh-failed`, 아니면 strike 오류를 `struck_fp`와 함께 반환(4384-4453). 성공하면 `_probe_verdicts[lineage]=True`.
    - **persist**: 백업 쓰기(실패 허용), 그다음 `claude_config_lock()` 아래에서 `_write_credentials`로 라이브 Keychain에 쓰기. 라이브 쓰기가 실패하면 `token expired` 센티널(4468-4509).
  - `LockError`나 예외는 defer(4510-4530). 마지막으로 working으로 usage를 GET(4532-4539).

### 3.5 회전 감지와 resync `_resync_rotated_backup` (switcher.py:4541-4677)
조건: 활성 계정 usage GET 성공, 그 읽기가 degraded 아님.
1. 라이브에 access+refresh가 둘 다 있어야 한다. 아니면 중단(백업을 반쪽 쌍으로 seed하지 않는다).
2. 백업 fp와 같으면 끝.
3. `_probe_verdicts[(num,email,stored email,org,uuid,fp)]`를 본다(2964-2988). False면 중단. 값이 없으면 락 밖에서 `GET https://api.anthropic.com/api/oauth/profile`(Bearer, 5s)(oauth.py:232-291) → `_resolved_matches_slot_identity`(uuid 우선. uuid가 없으면 email+org가 정확히 일치할 때만 True이고, 이때 uuid를 backfill한다)(2909-2962). None이면 중단, 결과는 메모해 둔다.
4. `.lock` + Claude credentials 락 아래에서 신원을 다시 확인하고, verdict를 다시 확인하고, 라이브를 다시 읽어 fp가 같고 토큰 쌍이 완비됐는지 본다. 통과하면 `_write_account_credentials(num,email,live)`, 로그 **"Resynced…"**.
5. LockError면 조용히 넘어가고, 기타 예외는 경고. 절대 raise하지 않는다.
메모(`_probe_verdicts`)는 프로세스 메모리에만 있다(switcher.py:376-378). CLI는 매번 새 프로세스라 회전이 일어날 때마다 오라클을 한 번 호출한다.

### 3.6 세션 프로필: 판정, bootstrap, 무효화
- **라이브 판정**(process_detection.py:89-145): `<profile>/sessions/*.json`을 읽어 `pid`를 꺼내고 `kill(pid,0)`로 확인한다(EPERM도 살아 있음으로 본다, 50-70). 파싱 불가 파일은 unreadable로 센다. 파괴적 작업은 `profile_is_quiescent`(라이브 0 **그리고** unreadable 0)를 요구한다(session.py:460-469). 반면 `_post_backup_write`와 gate는 `_live_session_pids`(unreadable 무시)를 쓴다(switcher.py:2715-2725).
- **reuse 검사** `_session_validity`(session.py:915-987): env는 auth override 5종을 빼고 `CLAUDE_CONFIG_DIR=session_dir`로 설정한 뒤(192-198, 487-491) `claude auth status --json`을 10s 타임아웃으로 실행한다.
  - rc≠0이면 invalid. JSON 파싱 불가면 unknown. 실행 자체가 안 되면(OSError) unreachable. 타임아웃이면 unknown.
  - `loggedIn is True`, `authMethod=="claude.ai"`, `email==slot email`, `orgId`(둘 다 값이 있을 때만 비교)를 모두 만족하면 valid.
  - unknown이면 로컬 artifact로 판단한다. 평문 seed가 있거나 Keychain에 rc0 항목이 있거나 Keychain을 읽을 수 없으면 "있음"으로 본다. 여기에 신원 drift가 없어야 한다(989-1014, 288-330).
- **setup_session**(session.py:641-800, cc-baton `baton`이 `cswap run N`으로 진입):
  1. `stale` = 마커가 있고 quiescent.
  2. stale이 아니고 valid면 sharing을 동기화하고 반환(락·POST 없음). 대부분의 실행이 이 경로다.
  3. 아니면 **pre-lock refresh**: 백업에 rt가 있으면 consume gate를 부른다. 결과가 `error≠None` + 크리덴셜 있음이면 실행을 거부한다(stashed 여부에 따라 메시지가 다르다). `error≠None`만이면 경고하고 계속(671-728).
  4. `.lock`을 30s 아래에서: stale+quiescent면 `_invalidate_session_credentials` + 마커 제거. valid면 백업과 fp가 다르고 quiescent일 때만 `_bootstrap`. 그 외에는 `_bootstrap`(730-764).
  5. `_bootstrap`(825-896):
     - hashed Keychain 항목을 **먼저 삭제**한다.
     - 백업을 `_ex`로 읽는다. 비었으면 unreadable/없음에 따라 다른 에러.
     - config 백업의 `oauthAccount`가 없으면 에러.
     - dir mkdir + 0700, `.credentials.json` ← 백업 원문(0600), `.claude.json`을 merge(0600). 로그는 **"Bootstrapped…"**.
  6. sharing 동기화 → `_session_validity`를 다시 본다. unknown이면 artifact 판단. unknown/unreachable이면 프로필을 남겨 두고 에러. invalid면 `_cleanup_failed_session`(Keychain 삭제 + rmtree + 마커 제거) 후 에러(766-797).
  7. 락을 풀고 `execvpe(claude, env에서 override 제거 + CLAUDE_CONFIG_DIR)`(585-589, 607-622).
  - same-account fast path: env에 CLAUDE_CONFIG_DIR가 없고, 대상이 `~/.claude.json`의 현재 계정이면, 프로필 없이 그냥 `claude`를 exec한다(546-568). cc-baton `state.profile_dir`가 이 규칙에 의존한다(state.py:160-171).
- **무효화** `_invalidate_session_credentials`(switcher.py:2755-2776): 디렉토리가 없으면 no-op(로그 없음). 있으면 hashed Keychain 삭제 → `.credentials.json` unlink → stale 마커 제거 → **"Invalidated…"** 로그. 히스토리와 `.claude.json`은 남는다.
- **지연 무효화**: 라이브 중 백업이 바뀌면 형제 마커만 남긴다(783-805). 다음 비라이브 실행 때 소비한다(session.py:655, 733-735). 또 gate의 프로필 우선 규칙은 마커가 있는 프로필을 무시한다(switcher.py:2193).
- **sharing**(session.py:1018-1135): `~/.claude`의 SHARED_ITEMS를 resolve한 타깃으로 symlink하고, 사용자 파일은 건드리지 않는다. manifest에 있는 것만 제거한다. MCP mirror는 `~/.claude.json`의 `mcpServers`를 프로필 `.claude.json`에 반영하는데, 채택 마커와 `.claude.json.lock` 아래에서만 한다(1137-1248). `--share-history`는 cc-baton이 쓰지 않는다.

### 3.7 remove (switcher.py:3854-3940)
`_refuse_session_shell` → 식별자 해석(숫자 → alias → email, email이 모호하면 대화형 선택. 3279-3315) → `_ensure_no_live_session`(라이브나 unreadable 레코드가 있으면 거부, 2727-2753) → y/N 확인 → `_delete_account_files`(877-894) → `accounts`/`sequence`에서 제거 → 저장 → 로그 → `_prune_mappings`.
`_delete_account_files`가 지우는 것:
- `.enc`, Keychain `account-N-email`, `account-None-email`, `.prev` 두 종(credentials.py:1347-1368)
- `configs/.claude-config-N-email.json`
- `_delete_session_profile`: hashed Keychain → rmtree → 형제 마커 제거(2778-2819)
`usage.json` 행은 지우지 않는다. 신원 가드 때문에 재사용 슬롯에는 보이지 않는다(usage_store.py:854-860). 라이브 `Claude Code-credentials`도 건드리지 않는다.

### 3.8 alias / map / unmap
- alias(cli.py:501-573, switcher.py:973-1048): `_refuse_session_shell`, `normalize_alias`, 중복 alias 거부(대소문자 무시), sequence 기록 + lastUpdated. unset은 멱등. 목록은 슬롯 번호 순.
- map(cli.py:240-310): 인자 없으면 목록 출력(switcher.py:1664-1687). 있으면 `resolve_account`(모호하면 에러, 946-971). 디렉토리가 없으면 경고 후 계속, `MappingStore.set`.
- unmap(cli.py:313-346): `remove(normalize_path(path or cwd))`.
- map/unmap에는 session-shell 거부가 없다. 셋 다 root면 거부(cli.py:232-237).

---

## 4. usage 수집

### 4.1 엔드포인트
- `GET https://api.anthropic.com/api/oauth/usage`. 헤더 `Authorization: Bearer <access>`, `anthropic-beta: oauth-2025-04-20`, `User-Agent: claude-swap/1.0`. 타임아웃 5s(oauth.py:16, 364-374).
- 오류 kind: `http-<code>`(+Retry-After 초. HTTP-date 형식은 무시), `timeout`, `network`, `bad-response`, 예외 타입명(377-403). 사전 오류: `no-access-token`, `refresh-failed`, 그리고 gate가 주는 `store-unmirrored`/`invalid_client`/`consume-busy`/`stash-unreadable`(601-603).

### 4.2 보존 필드 `build_usage_result`(oauth.py:431-502)
- `five_hour`/`seven_day`: `{pct: utilization, resets_at?, countdown?, clock?}`. countdown/clock은 표시용 캐시 문자열(314-341).
- `spend`: extra_usage.is_enabled이고 used_credits/monthly_limit/utilization이 모두 있을 때 `{used:/100, limit:/100, pct, currency, resets_at?, countdown?, clock?}`.
- `scoped`: `limits[]` 중 `scope.model.display_name`과 숫자 `percent`가 있는 항목 → `{name, pct, resets_at?, countdown?, clock?}`.
- 모두 비면 `None`(성공으로 기록되며 lastGood=null).

### 4.3 수집 패스 `_collect_usage_entries`(switcher.py:4815-4943). list/status 공통.
1. `_build_accounts_info`: 활성 슬롯 = `~/.claude.json`의 (email, org)와 일치하는 슬롯. 활성은 라이브 저장소를, 나머지는 백업을 읽는다(3942-3980).
2. 정적 센티널(4679-4716): api key / 크리덴셜이나 액세스 토큰 없음 → `keychain unavailable` 또는 `no credentials`.
3. `store.entries` → 사망 토큰 격리 검사. 해당하면 `re-login needed`. strike는 있는데 fp가 이미 바뀌었으면 `clear_dead_token`(4853-4873, 5002-5047).
4. `store.reserve(requested, respect_plans=True, repair_overslept=True)`: 락 아래에서 자격을 판정하고 claim을 기록한다. claim = `lastAttemptAt=now, claimId=uuid4hex, claimUntil=now+90`(usage_store.py:984-1039).
5. 활성인데 claim을 못 받았고 만료된 경우 `token expired` 센티널(4898-4913).
6. 병렬 fetch. i번째 요청은 시작 전에 i×0.25s 지연(switcher.py:110, 4797-4813). 비활성 계정은 `_fetch_account_usage`를 탄다(4718-4795):
   - 프로필 크리덴셜이 있고 신원 drift가 없으면 그것으로 **읽기 전용** GET(만료 아닐 때만). 만료됐고 라이브 세션이 있으면 `token expired`. 만료됐고 라이브가 없으면 백업 경로로 내려간다.
   - 백업 경로: `try_fetch_usage_for_account(is_active=has_live_session, refresh_via=None if live else consume_backup_grant)`.
7. `_plans_after_fetch`(성공 건만) → `store.record(records, identities, claims, plans)`. claimId fencing이 맞는 행만 반영한다(usage_store.py:1041-1137).

### 4.4 스케줄·백오프·예산 규칙
- `SERVE_TTL_S=180`: 이보다 신선하면 fetch하지 않는다(poll_policy.py:75).
- `_row_eligible`(usage_store.py:1185-1214). on-demand(list/status) 기준:
  - authDeadStrikes ≥ 1이면 금지, backoffUntil 전이면 금지, 라이브 claim이 있으면 금지.
  - 그다음 `stale(>180s) AND (nextPollAt 도래 OR nextPollAt 없음 OR overslept)`.
  - overslept = `nextPollAt > now + max(interval,600)×1.1 + 60`(395-405).
- 성공 후 `plan_after_fetch`(poll_policy.py:195-270):
  - default는 active 180 / candidate 300. ceiling은 active 300 / candidate 600.
  - binding pct 변화 ≥ 1%p면 간격을 반으로(최소 180). 아니면 ×1.5(ceiling, 최소 180).
  - active가 움직이는 중이고 `pct ≥ threshold-15`이며 최근 429가 없으면 60s. threshold 기본 90.
  - 최근 429(백오프 종료 시점 + 3600s 이내, usage_store.py:312-342)가 있으면 `max(interval, base×1.5, 360)`, 상한 1800.
  - headroom ≤ 0이면 최소 600.
  - jitter ±10%. 다음 poll은 (소진 시) limiting reset+60s 또는 earliest future reset+60s를 넘지 않는다.
- 실패 시 `_failure_backoff_s`(usage_store.py:524-814):
  - computed = `min(30·2^(min(n-1,32)), 600)`.
  - Retry-After 없음 → computed.
  - `Retry-After: 0` + 429 → `max(computed, 300)`(≤600). 429가 아니면 computed.
  - Retry-After > 600 + 429 → `+900` margin.
  - 상한(park bound)은 429면 4500, 그 외 3600. 최종값은 `max(asked, computed)`.
  - 429면 `last429At=now`. `invalid_grant`/`no_refresh_token`이면 `authDeadStrikes += 1`, `struckFingerprint = struck_fp`(1100-1106). `AUTH_DEAD_STRIKES = 1`이라 한 번이면 격리된다(227).
- 성공 기록: `lastGood`, `fetchedAt`, plan, `consecutiveFailures=0`, `lastError=null`, `backoffUntil=null`, `authDeadStrikes=0`(1072-1083). `last429At`은 성공해도 지우지 않는다.
- 신뢰(decision) 판정(usage_store.py:376-392, 867-943):
  - 기본은 age ≤ 300s.
  - 실패 중이거나 nextPollAt 전이거나 claim이 살아 있으면 연장한다. 429면 `min(earliest reset, fetchedAt+7200)`까지, 그 외는 3600s까지.

### 4.5 `cache/usage.json` 스키마(usage_store.py:51, 839-863, 관측)
```
{"schemaVersion": 2,                       // 다르면 빈 테이블로 본다(844-845)
 "accounts": {"<num>": {
   "email": str, "organizationUuid": str,   // 신원 가드. 다르면 그 행은 보이지 않고, 쓸 때 새 행으로 교체된다
   "lastGood": usage dict|null, "fetchedAt": float(epoch s),
   "lastAttemptAt": float, "claimId": str|null, "claimUntil": float,
   "consecutiveFailures": int, "lastError": str|null, "backoffUntil": float|null,
   "nextPollAt": float, "pollIntervalS": float, "last429At"?: float,
   "authDeadStrikes": int, "struckFingerprint": str|null }}}
```
usage dict는 §4.2의 snake_case 형태다. cc-baton이 이 파일을 직접 읽는다(§5.3).

---

## 5. 출력 스키마와 cc-baton 의존성

### 5.1 `list --json` (switcher.py:5329-5407, json_output.py)
```
{"schemaVersion": 1,
 "activeAccountNumber": int|null,          // 라이브 신원 기준. sequence의 activeAccountNumber가 아니다
 "accounts": [ {
   "number": int, "email": str, "organizationName": str, "organizationUuid": str,
   "isOrganization": bool, "active": bool,
   "usageStatus": "ok"|"token_expired"|"api_key"|"keychain_unavailable"|"relogin_required"|"foreign_credential"|"no_credentials"|"unavailable",
   "usage": null | {"fiveHour":W, "sevenDay":W+pace, "spend"?:{used,limit,pct,currency,resetsAt?,countdown?,clock?}, "scoped"?:[W+pace+{"name"}]},
   "alias"?: str, "disabled"?: true,
   // usage가 있으면: "usageFetchedAt": "…Z", "usageAgeSeconds": float(1dp)
   // usage가 null이면: "lastGoodUsage": {...}, "lastGoodFetchedAt", "lastGoodAgeSeconds"
 } ],
 "duplicateAccountWarnings"?: [str], "lockstepUsageWarnings"?: [str], "unclaimedCredentials"?: [id] }
W = {"pct": num, "resetsAt"?: str, "countdown"?: str, "clock"?: str}   // countdown/clock은 직렬화 시점에 다시 계산(json_output.py:45-58)
pace = {"expectedPct", "aheadOfPace", "projectedExhaustionAt"?, "willLastToReset"?}  (json_output.py:61-86, pace.py:65)
```
- `usage`는 decision-grade 값이다. 300s를 넘었고 신뢰가 연장되지 않으면 null이 된다(5341-5355).
- sequence.json이 없으면 `{"schemaVersion":1,"activeAccountNumber":null,"accounts":[]}`(5390-5398).
- 에러 시 stdout에 `{"schemaVersion":1,"error":{"type","message"}}`를 내고 exit 1(json_output.py:254-259, cli.py:1448-1455). 출력은 `json.dumps(indent=2)`(cli.py:1465-1466).

### 5.2 `status --json` (switcher.py:5491-5544)
- 신원이 없으면 `{"schemaVersion":1,"active":null}`.
- 관리되지 않는 계정이면 `{"schemaVersion":1,"active":{"email","managed":false}}`.
- 관리되는 계정이면 `{"schemaVersion":1,"active":{number,email,organizationName,organizationUuid,isOrganization,"managed":true,usageStatus,usage,alias?,+freshness 또는 lastGood 필드},"totalManagedAccounts":int}`. usage 수집은 활성 1건만 한다(5475-5489).

### 5.3 cc-baton이 실제로 의존하는 것
- **list JSON은 파싱하지 않는다.** `cswap list --json`은 캐시를 갱신시키는 트리거로만 쓴다.
  - statusline은 detached로 띄우고 stdout을 버린다. **CLAUDE_CONFIG_DIR를 제거**하고, usageRefresh 기능이 켜져 있고 `poll_due`인 계정이 있을 때만 실행한다(statusline.py:75-98).
  - pick은 동기 호출, 6s 타임아웃이며 **CLAUDE_CONFIG_DIR를 제거하지 않는다**(pick.py:75-93).
- 직접 읽는 파일(state.py):
  - `sequence.json`: `accounts.<n>.{email,alias}`, `sequence`, `activeAccountNumber`(100-130). activeAccountNumber가 "기본 프로필 계정"을 정하므로 add가 이 값을 덮어쓰는 동작을 유지해야 한다.
  - `mappings.json`: `mappings.<path>.email`(208-226).
  - `cache/usage.json`: `accounts.<n>.lastGood.{five_hour,seven_day}.{pct,resets_at}`, `lastGood.scoped[].{name,pct,resets_at}`, `fetchedAt`, `lastError`, `backoffUntil`, `nextPollAt`(193-205, 392-409).
  - 프로필 디렉토리 규칙 `sessions/<n>-<slug>`: slugify를 복제해 쓴다(154-171). 디렉토리명 앞의 숫자로 현재 계정을 판단한다(183-190, 435-449). `<profile>/sessions/*.json`, `history.jsonl`의 mtime(324-340). hib.py:86은 `sessions/*` 디렉토리 목록을 읽는다.
  - `~/.claude.json` `oauthAccount.emailAddress`(412-421).
- 호출하는 명령:
  - `cswap run <target> [-- args]`: baton.zsh:47-49. `env -u CLAUDE_CONFIG_DIR` 붙여서, `CC_SWAP_LOOP=1 CC_BATON_TAB=$$` 설정. 공유는 기본값(share=True).
  - `cswap add`(대화형), `cswap alias N name`: install.py:461-469, 507, 522. CLAUDE_CONFIG_DIR 제거.
  - `cc-baton cswap …` 패스스루(cli.py:78-86).
  - install.py:708은 `uv tool install claude-swap==0.26.0`, 453-458은 `uv tool list` 검사.
- cc-baton swap.py는 대상 프로필 `projects/`에 트랜스크립트를 복사한다(swap.py:126, 237). 아직 bootstrap되지 않은 프로필 디렉토리가 먼저 생길 수 있으므로, Go bootstrap은 "디렉토리는 있는데 크리덴셜과 `.claude.json`이 없는" 상태를 정상 입력으로 받아야 한다.

---

## 6. 위험 순위 (나이브 포트가 로그아웃이나 데이터 손상을 내는 지점)

**R1 [치명] 소비된 refresh token을 POST → `invalid_grant` → 즉시 격리(strike 1) + 실제 재로그인 필요**
- refresh token은 1회용이다. 계정 1 lineage는 백업과 프로필 hashed 항목, 두 곳에 있다.
- 지킬 것:
  - (a) 라이브 세션이 있는 슬롯의 백업 rt는 절대 POST하지 않는다(switcher.py:4735, 4772-4788, 2184).
  - (b) POST 전에 `.consume-N.lock`과 `.lock` 아래에서 다시 읽는다. 프로필 쪽이 더 새 세대면 그것을 채택한다(2107-2216).
  - (c) POST 후에는 fp CAS를 하고, 실패하면 stash한다. successor를 절대 버리지 않는다(2304-2390).
  - (d) degraded 읽기의 rt는 소비하지 않는다(4115).
  - (e) 활성 계정은 백업과 lineage가 맞을 때만 소비한다(4140-4167).
- Python cswap과 Go가 동시에 돌 수 있다(cc-baton 번들 cswap). 그래서 **같은 flock 파일**(`.lock`, `credentials/.consume-N.lock`)을 써야 한다. Go `syscall.Flock`은 macOS에서 Python `fcntl.flock`과 호환된다.

**R2 [치명] 라이브 세션 프로필의 크리덴셜 삭제나 재seed → 지금 이 대화가 로그아웃**
- `_invalidate_session_credentials`와 `_bootstrap`은 hashed Keychain 항목부터 지운다(switcher.py:2771, session.py:831). 라이브 판정이 Python과 다르면 사용자 세션이 즉시 죽는다. Python 판정은 `<profile>/sessions/*.json`의 `pid` + `kill(pid,0)`이다.
- 백업이 바뀔 때마다 무효화하는 동작(R1 방지와 짝)은 라이브가 아닐 때만, 라이브면 마커만 남긴다(783-805).
- 주의: setup_session의 기본 bootstrap 분기(session.py:764)에는 quiescent 검사가 없다. `claude auth status`가 invalid를 줄 때만 들어가므로, Go가 validity를 다르게 판정하면(예: env 실수로 다른 프로필을 probe) 라이브 프로필을 지운다. Go 포트는 bootstrap 직전에 `profile_is_quiescent`를 반드시 확인한다(Python보다 강하게).

**R3 [높음] hashed 서비스명 불일치**
- `sha256(NFC(정확히 export한 문자열))[:8]`(session.py:232-243). 현재 프로필은 `Claude Code-credentials-8a0172f8`이다.
- Go가 경로를 `EvalSymlinks`로 정규화하거나 trailing `/`를 붙이거나 `$HOME` 대신 다른 홈 해석을 쓰면 다음 문제가 생긴다.
  - (a) 새로 실행한 claude가 빈 항목을 보고 로그아웃 상태가 된다.
  - (b) 무효화가 엉뚱한 항목을 지우고 진짜 항목은 남는다(stale 세대가 살아남는다).
  - (c) usage가 프로필 크리덴셜을 못 읽어 백업 경로로 POST하고 R1로 이어진다.
- export 문자열과 해시 입력은 한 변수에서 나와야 한다.

**R4 [높음] Keychain 접근 방식**
- `/usr/bin/security`로 shell-out해야 한다. Security.framework(go-keychain 등)로 직접 읽으면 항목 ACL이 `security`/Claude를 creator로 들고 있어서 GUI 프롬프트가 뜬다. 백그라운드(statusline이 띄운 detached 프로세스)에서는 5s 타임아웃 → 파일 모드 fallback → degraded/unreadable 판정 → defer가 연쇄된다(macos_keychain.py:1-34).
- 파일 fallback으로 `.enc`를 썼다면 읽을 때 **.enc-wins** 규칙을 지켜야 한다. 아니면 회복된 Keychain의 옛 값을 읽어 R1으로 이어진다(credentials.py:1052-1059, 1162-1237).
- rc 44(없음)와 그 외 실패(잠김)를 구분해야 한다. 잠김을 "없음"으로 읽으면 "re-add 하세요"라는 잘못된 안내가 나가거나, 빈 백업으로 판단해 gate가 snapshot을 POST한다(credentials.py:1239-1302).

**R5 [높음] 기본 프로필 계정의 resync 누락**
- Claude Code가 약 8h마다 회전하는데 백업을 resync하지 않으면 백업 rt가 계속 소비된 세대로 남는다. 나중에 그 계정이 비활성이 되거나(/login 전환) 활성 복구 분기가 백업을 POST하면 `invalid_grant`가 난다.
- 최소 구현: 활성 계정 usage가 성공하면 fp를 비교하고, 오라클로 uuid를 검증한 뒤 락 아래에서 라이브를 다시 읽어 백업에 쓴다(§3.5). 오라클 없이 쓰면 다른 계정의 크리덴셜로 백업을 덮을 위험이 있다(4555-4561).
- 권고: v1에서는 활성 계정에 refresh POST를 아예 하지 않는다(만료 시 `token expired`로 defer하고 Claude Code가 스스로 refresh). 이렇게 하면 §3.4.2 전체가 사라지고 위험도 줄어든다.

**R6 [높음] CLAUDE_CONFIG_DIR가 세션을 가리킨 채로 list/status를 실행할 때 생기는 교차 쓰기(Python 0.26.0 잠재 버그)**
- 이 상태에서는 활성 계정이 프로필 계정이 되고 읽기는 hashed 항목에서 한다. 그런데 복구 분기의 라이브 쓰기 `_write_credentials`는 **접미사 없는 `Claude Code-credentials`**(=계정 2의 기본 로그인)에 계정 1 크리덴셜을 쓴다(credentials.py:974-981, switcher.py:4492-4493). Claude credentials 락도 프로필 쪽 경로를 잡는다(claude_locks.py:66-76).
- list와 status에는 `_refuse_session_shell`이 없다(switcher.py:985 등 9곳, list/status 제외).
- cc-baton pick.py:89는 env를 제거하지 않는다.
- Go 포트: list/status 시작 시 CLAUDE_CONFIG_DIR를 무시(기본 프로필로 고정)하거나, 세션 경로면 활성 refresh와 라이브 쓰기를 금지한다.

**R7 [중간] 격리 판정 오분류**
- `AUTH_DEAD_STRIKES=1`이다. `invalid_grant`는 400/401/403 **그리고** JSON 최상위 `error` 필드로만 판정해야 한다(oauth.py:174-192). 문자열 검색이나 상태코드만 보면 살아 있는 계정을 격리한다.
- strike는 `struckFingerprint`에 묶인다. 저장된 fp가 달라지면 자동으로 해제된다(usage_store.py:348-374). fp 포맷이 다르면 해제되지 않거나 반대로 잘못 해제된다.

**R8 [중간] sequence.json 손상 시 재초기화**
- 존재하는데 파싱이 안 되면 반드시 중단한다(switcher.py:2832-2851). "없음"으로 보면 add가 슬롯 1을 다시 쓰면서 백업을 덮는다.
- configs와 bootstrap 파일은 Python이 비원자적으로 쓴다. Go는 원자적으로 써도 호환된다(권장).

**R9 [중간] usage.json 동시성과 예산**
- Python cswap(번들)과 Go가 같은 파일을 쓰려면 `cache/.usage.lock` flock, claim/claimId fencing, SERVE_TTL 180s, plan 존중이 같아야 한다. 아니면 요청이 두 배가 되어 429(1시간 블록)를 맞는다.
- UA를 `claude-swap/1.0`에서 바꾸면 예산 class가 달라질 수 있다(poll_policy.py:4-5. 미검증).
- 쓰기는 `{"schemaVersion":2,...}`를 유지한다. 다르면 Python이 전체를 빈 것으로 본다(usage_store.py:844-845).

**R10 [중간] stash 무시**
- `.unclaimed-manifest.json`에 `consumedFp==fp(백업)`인 행이 있는데 adopt하지 않으면, 이미 쓴 grant를 POST하게 되어 R1이 된다(switcher.py:2452-2609). 현재 stash는 없지만, Go가 persist 실패 처리를 단순화한다면 최소한 읽기와 adopt는 구현해야 한다.

**R11 [낮음] JSON 직렬화 차이**
- Go `encoding/json`은 기본으로 `<>&`를 HTML 이스케이프한다(SetEscapeHTML(false) 권장). Python은 non-ASCII를 `\uXXXX`로 쓴다. 둘 다 읽기 호환은 된다.
- Python이 `Infinity`/`NaN`을 쓴 usage.json은 Go 파서가 거부한다. 그 경우 "빈 테이블"로 처리하고 **덮어쓰기 전에 보존**한다(usage_store.py:633-640 참조).
- 파일 권한(0600, 디렉토리 0700)을 유지한다.

**R12 [낮음] 불필요한 grant 소비**
- setup_session은 reuse하지 않는 모든 실행에서 POST 1회를 한다(session.py:662-675). 생략해도 claude가 seed rt로 스스로 refresh하므로 안전하다. 유지한다면 gate 규칙 전체를 따라야 한다. 생략하는 편이 R1 노출면을 줄인다.

---

## 7. 코드로 해결하지 못한 질문

1. Claude Code가 `CLAUDE_CONFIG_DIR` 해시를 정말 `sha256(NFC(raw))[:8]`로 만드는가? claude-swap 주석은 claude 2.1.x 소스 기준이라고 주장한다(session.py:232-243). 이 머신의 claude 버전에서 `security find-generic-password -s "Claude Code-credentials-8a0172f8" -a ljh0327`(-w 없음, 속성만)로 확인이 필요하다. 지시에 따라 실행하지 않았다.
2. Claude Code가 평문 seed를 Keychain으로 옮긴 뒤 `.credentials.json`을 지우는가? 관측상 현재 프로필에 파일이 없어서 그렇게 보인다. 그 시점과 조건은 코드로 확인할 수 없다.
3. 토큰 응답의 `expires_in` 값(관측 회전 주기 ≈8.1h)과 `refresh_token`이 매번 회전하는지(코드는 optional로 처리, oauth.py:158-159).
4. usage 예산이 UA class별로 따로 잡히는가? Go 포트의 UA를 바꿔도 되는가?
5. `claude auth status --json` 출력 키(`loggedIn`, `authMethod`, `email`, `orgId`)가 현재 claude 버전에서도 그대로인가(session.py:976-986, "verified against 2.1.175").
6. 전환 기간에 Python cswap(번들, menubar/TUI 포함)을 계속 돌릴 것인가? 돌린다면 R1·R9의 락과 스키마를 완전히 호환해야 하고, 아니라면 cc-baton install이 번들 cswap을 제거해야 한다.
7. 계정 2에 세션 프로필이 생기는 경로(계정 1을 기본 로그인으로 /login 전환)를 v1에서 지원할 것인가? 그 경우 활성 판정이 바뀌는 순간 §3.4.2/§3.5 경계 사례(stale config 아래의 다른 계정 크리덴셜)가 생긴다.
8. `CLAUDE_SECURESTORAGE_CONFIG_DIR`를 쓸 일이 있는가? 없다면 "정의돼 있으면 거부"만 구현하면 된다.
9. Python `.prev` 보관이 Keychain에 `account-N-email.prev` 항목을 남기는데, 현재 존재 여부는 확인하지 않았다(Keychain 조회 금지). remove 시 함께 지워야 한다.
