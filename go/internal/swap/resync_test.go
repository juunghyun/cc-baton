package swap

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// rotated 는 Claude Code 가 2번(기본 로그인) 토큰을 회전시켰는데 백업은 옛 세대인 상태를 만든다.
func (h *fakeHome) rotated() (old, live string) {
	h.twoAccounts()
	old, live = creds("live-old", "rt-old", later), creds("live", "rt-live", later)
	h.keychain(backupService, "account-2-b@corp.com", old)
	h.keychain(liveService, h.user, live)
	h.api(ok200)
	verdicts.Range(func(k, _ any) bool { verdicts.Delete(k); return true }) // 프로세스 메모는 테스트마다 비운다
	return
}

func (h *fakeHome) owner(email, org string) *atomic.Int32 {
	var calls atomic.Int32
	h.profile(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		json.NewEncoder(w).Encode(map[string]any{"account": map[string]any{"uuid": "u-" + email, "email": email}, "organization": map[string]any{"uuid": org}})
	})
	return &calls
}

func (h *fakeHome) kcValue(service, account string) string {
	b, err := os.ReadFile(filepath.Join(h.kc, service+"@"+account))
	if err != nil {
		return ""
	}
	return string(b)
}

func (h *fakeHome) logText() string {
	b, _ := os.ReadFile(filepath.Join(h.env.Backup(), "claude-swap.log"))
	return string(b)
}

func TestResyncAdoptsRotatedLiveToken(t *testing.T) {
	h := newHome(t)
	old, live := h.rotated()
	calls := h.owner("b@corp.com", "org2")
	h.env.ListJSON()
	if got := h.kcValue(backupService, "account-2-b@corp.com"); got != live {
		t.Fatalf("백업 = %s", got)
	}
	if got := h.kcValue(backupService, "account-2-b@corp.com.prev"); got != old {
		t.Fatalf(".prev = %s (옛 세대가 남아야 한다)", got)
	}
	if calls.Load() != 1 || !strings.Contains(h.logText(), "[go] Resynced account 2's backup to the rotated live credential") {
		t.Fatalf("소유권 확인 %d회, 로그 %s", calls.Load(), h.logText())
	}
	// 맞춘 뒤엔 같은 세대라 다시 확인하지 않는다
	h.env.StatusJSON()
	if calls.Load() != 1 {
		t.Fatal("같은 세대인데 다시 확인함")
	}
}

func TestResyncRefusesAnotherAccountsToken(t *testing.T) {
	h := newHome(t)
	old, _ := h.rotated()
	h.owner("someone@else.com", "org9")
	p, _ := h.env.ListJSON()
	if h.kcValue(backupService, "account-2-b@corp.com") != old {
		t.Fatal("다른 계정 토큰으로 백업을 덮음")
	}
	if s := accountsByNum(t, p)[2]["usageStatus"]; s != "foreign_credential" {
		t.Fatalf("= %v", s)
	}
	if !strings.Contains(h.logText(), "resolves to a different account than Account-2") {
		t.Fatal("경고 없음")
	}
}

func TestResyncSkipsWhenOwnershipUnknown(t *testing.T) {
	h := newHome(t)
	old, _ := h.rotated() // 소유권 확인 API 는 기본값 503
	p, _ := h.env.ListJSON()
	if h.kcValue(backupService, "account-2-b@corp.com") != old || accountsByNum(t, p)[2]["usageStatus"] != "ok" {
		t.Fatal("확인 못 했는데 백업을 바꿈")
	}
	// 같은 이메일인데 조직 정보가 없으면 판단 불가 → 그대로
	h.profile(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"account":{"uuid":"u2","email":"b@corp.com"}}`))
	})
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{}}) // 다시 받을 차례로
	h.env.ListJSON()
	if h.kcValue(backupService, "account-2-b@corp.com") != old {
		t.Fatal("조직 없이 소유권을 인정함")
	}
}

func TestResyncSkipsSameLineageAndPartialPairs(t *testing.T) {
	h := newHome(t)
	h.rotated()
	calls := h.owner("b@corp.com", "org2")
	h.keychain(backupService, "account-2-b@corp.com", creds("older-access", "rt-live", later)) // refresh token 이 같으면 같은 세대
	h.env.ListJSON()
	h.keychain(liveService, h.user, `{"claudeAiOauth":{"accessToken":"live","expiresAt":`+jsonNum(later)+`}}`) // refresh token 없음
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{}})
	h.env.ListJSON()
	if calls.Load() != 0 {
		t.Fatalf("확인할 필요 없는데 %d회 확인", calls.Load())
	}
}

func TestResyncRespectsClaudeCodesLock(t *testing.T) {
	h := newHome(t)
	old, live := h.rotated()
	h.owner("b@corp.com", "org2")
	old2 := claudeLockTimeout
	claudeLockTimeout = 300 * time.Millisecond
	t.Cleanup(func() { claudeLockTimeout = old2 })
	lock := filepath.Join(h.env.Home, ".claude", ".oauth_refresh.lock")
	must(t, os.MkdirAll(lock, 0o755)) // Claude Code 가 지금 갱신 중
	h.env.ListJSON()
	if h.kcValue(backupService, "account-2-b@corp.com") != old {
		t.Fatal("Claude Code 잠금을 무시함")
	}
	// 주인이 죽고 60초 넘게 방치된 잠금은 넘겨받는다
	stale := time.Now().Add(-2 * time.Minute)
	must(t, os.Chtimes(lock, stale, stale))
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{}})
	h.env.ListJSON()
	if h.kcValue(backupService, "account-2-b@corp.com") != live || exists(lock) || exists(filepath.Join(h.env.Home, ".claude.lock")) {
		t.Fatal("죽은 잠금을 못 넘겨받았거나 잠금을 안 풀었음")
	}
}

func TestResyncNeverUsesPlaintextFallback(t *testing.T) {
	h := newHome(t)
	h.rotated()
	calls := h.owner("b@corp.com", "org2")
	// 키체인을 못 읽어 ~/.claude/.credentials.json(옛 세대일 수 있음)으로 메운 경우
	must(t, os.MkdirAll(filepath.Join(h.env.Home, ".claude"), 0o700))
	must(t, os.WriteFile(filepath.Join(h.env.Home, ".claude", ".credentials.json"), []byte(creds("file", "rt-file", later)), 0o600))
	must(t, os.WriteFile(filepath.Join(h.kc, "LOCKED"), nil, 0o600))
	h.env.ListJSON()
	if calls.Load() != 0 {
		t.Fatal("평문 대체 값으로 백업 맞추기를 시도함")
	}
}

func (h *fakeHome) profileDir(n, email string) string {
	dir := h.env.sessionDir(n, email)
	must(h.t, os.MkdirAll(filepath.Join(dir, "sessions"), 0o700))
	h.keychain(sessionService(dir), h.user, creds("prof", "rt-p", later))
	must(h.t, os.WriteFile(filepath.Join(dir, ".credentials.json"), []byte("seed"), 0o600))
	return dir
}

func TestBackupWriteInvalidatesOnlyQuiescentProfiles(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	dir := h.profileDir("1", "a@gmail.com")
	marker := filepath.Join(filepath.Dir(dir), "."+filepath.Base(dir)+staleMarker)

	// 돌고 있는 claude 가 있으면 토큰은 그대로, 표시만
	rec := filepath.Join(dir, "sessions", "1.json")
	must(t, os.WriteFile(rec, []byte(`{"pid":`+jsonNum(int64(os.Getpid()))+`}`), 0o600))
	must(t, h.env.writeAccountCredentials("1", "a@gmail.com", creds("n1", "rt-n1", later)))
	if h.kcValue(sessionService(dir), h.user) == "" || !exists(marker) {
		t.Fatal("살아 있는 세션의 토큰을 지웠거나 표시를 안 남김")
	}

	// 읽을 수 없는 세션 기록도 "돌고 있을 수 있음"으로 본다(Python 보다 엄격)
	must(t, os.Remove(marker))
	must(t, os.WriteFile(rec, []byte("{garbage"), 0o600))
	must(t, h.env.writeAccountCredentials("1", "a@gmail.com", creds("n2", "rt-n2", later)))
	if h.kcValue(sessionService(dir), h.user) == "" || !exists(marker) {
		t.Fatal("모르는 상태에서 토큰을 지움")
	}

	// 아무것도 안 돌면 토큰을 비워 다음 실행 때 새 백업으로 채우게 한다. 대화 기록은 남는다
	must(t, os.Remove(rec))
	must(t, os.WriteFile(filepath.Join(dir, "history.jsonl"), []byte("keep"), 0o600))
	must(t, h.env.writeAccountCredentials("1", "a@gmail.com", creds("n3", "rt-n3", later)))
	if h.kcValue(sessionService(dir), h.user) != "" || exists(filepath.Join(dir, ".credentials.json")) || exists(marker) || !exists(filepath.Join(dir, "history.jsonl")) {
		t.Fatal("비우기 결과가 다름")
	}
	if !strings.Contains(h.logText(), "[go] Invalidated session credentials for account 1") {
		t.Fatal("로그 없음")
	}
}

func TestBackupFallsBackToFileWhenKeychainWriteFails(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	must(t, os.WriteFile(filepath.Join(h.kc, "READONLY"), nil, 0o600))
	want := creds("n", "rt-n", later)
	must(t, h.env.writeBackup("1", "a@gmail.com", want))
	b, err := os.ReadFile(h.env.encPath("1", "a@gmail.com"))
	must(t, err)
	if d, _ := base64.StdEncoding.DecodeString(string(b)); string(d) != want || mode(t, h.env.encPath("1", "a@gmail.com")) != 0o600 {
		t.Fatalf(".enc = %s", d)
	}
	if h.kcValue(backupService, "account-1-a@gmail.com") != "" {
		t.Fatal("옛 키체인 사본이 남음(키체인이 돌아오면 새 파일을 가린다)")
	}
	// 키체인이 돌아온 뒤 쓰면 .enc 는 치운다(.enc 가 먼저 읽히므로 남으면 새 값을 가린다)
	must(t, os.Remove(filepath.Join(h.kc, "READONLY")))
	must(t, h.env.writeBackup("1", "a@gmail.com", creds("n2", "rt-n2", later)))
	if exists(h.env.encPath("1", "a@gmail.com")) {
		t.Fatal(".enc 가 남음")
	}
	if v, _ := h.env.readBackup("1", "a@gmail.com"); accessToken(v) != "n2" {
		t.Fatalf("읽은 값 = %s", v)
	}
}

func jsonNum(n int64) string { b, _ := json.Marshal(n); return string(b) }
