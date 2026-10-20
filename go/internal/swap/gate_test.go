package swap

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// tokenAPI 는 받은 refresh token 을 기록하고, 정해진 응답을 준다. 진짜 서버처럼 같은 refresh token 을 두 번 받으면 invalid_grant.
type tokenAPI struct {
	mu   sync.Mutex
	seen []string
	used map[string]bool
	mode string // "" = 정상, "dead", "flaky", "slow"
	hook func() // 응답 직전에 부른다(그사이 다른 쪽이 백업을 바꾸는 상황 등)
}

func (h *fakeHome) tokenServer() *tokenAPI {
	api := &tokenAPI{used: map[string]bool{}}
	h.token(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		json.NewDecoder(r.Body).Decode(&body)
		if body["grant_type"] != "refresh_token" || body["client_id"] != oauthClientID || r.Header.Get("User-Agent") != "claude-swap/1.0" {
			w.WriteHeader(400)
			return
		}
		api.mu.Lock()
		rt := body["refresh_token"]
		api.seen = append(api.seen, rt)
		reused := api.used[rt]
		api.used[rt] = true
		mode, hook := api.mode, api.hook
		api.mu.Unlock()
		if hook != nil {
			hook()
		}
		switch {
		case mode == "flaky":
			w.WriteHeader(500)
		case mode == "dead" || reused:
			w.WriteHeader(400)
			w.Write([]byte(`{"error":"invalid_grant"}`))
		default:
			json.NewEncoder(w).Encode(map[string]any{"access_token": "fresh-" + rt, "refresh_token": "next-" + rt, "expires_in": 28800, "scope": "user:inference user:profile"})
		}
	})
	return api
}

// expiredIdle 은 1번(쉬는 계정) 백업이 만료된 상태. 사용량 API 는 새 토큰만 받아 준다.
func (h *fakeHome) expiredIdle() (tok *tokenAPI, usage *fakeAPI) {
	h.twoAccounts()
	h.keychain(backupService, "account-1-a@gmail.com",
		`{"claudeAiOauth":{"accessToken":"bk","refreshToken":"rt-1","expiresAt":`+jsonNum(time.Now().UnixMilli())+`,"subscriptionType":"max"},"mcpOAuth":{"keep":"me"}}`)
	tok = h.tokenServer()
	usage = h.api(func(t string, w http.ResponseWriter) {
		if t == "bk" {
			w.WriteHeader(401)
			return
		}
		ok200(t, w)
	})
	return
}

func TestIdleExpiredBackupIsRefreshedOnceAndPersisted(t *testing.T) {
	h := newHome(t)
	tok, usage := h.expiredIdle()
	p, _ := h.env.ListJSON()
	if len(tok.seen) != 1 || tok.seen[0] != "rt-1" {
		t.Fatalf("갱신 요청 = %v", tok.seen)
	}
	b := h.kcValue(backupService, "account-1-a@gmail.com")
	o := oauthData(b)
	if o["accessToken"] != "fresh-rt-1" || o["refreshToken"] != "next-rt-1" || o["subscriptionType"] != "max" || !strings.Contains(b, `"mcpOAuth":{"keep":"me"}`) {
		t.Fatalf("새 백업 = %s (모르는 필드도 남아야 한다)", b)
	}
	if exp, _ := o["expiresAt"].(float64); exp < float64(time.Now().Add(7*time.Hour).UnixMilli()) {
		t.Fatalf("expiresAt = %v", o["expiresAt"])
	}
	if !contains(usage.tokens, "fresh-rt-1") || contains(usage.tokens, "bk") || accountsByNum(t, p)[1]["usageStatus"] != "ok" {
		t.Fatalf("사용량 요청 = %v", usage.tokens)
	}
	if h.kcValue(backupService, "account-1-a@gmail.com.prev") == "" {
		t.Fatal(".prev 없음")
	}
}

func TestDeadGrantQuarantinesTheGenerationItConsumed(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	tok.mode = "dead"
	before := h.kcValue(backupService, "account-1-a@gmail.com")
	p, _ := h.env.ListJSON()
	row := h.usageRow("1")
	if row["authDeadStrikes"] != 1.0 || row["struckFingerprint"] != fingerprint(before) || row["lastError"] != "invalid_grant" {
		t.Fatalf("행 = %v", row)
	}
	if s := accountsByNum(t, p)[1]["usageStatus"]; s != "relogin_required" || h.kcValue(backupService, "account-1-a@gmail.com") != before {
		t.Fatalf("상태 = %v (같은 수집 안에서 바로 보여야 한다), 백업이 바뀜", s)
	}
	// 격리된 계정은 다시 보내지 않는다
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{"1": func() map[string]any { row["backoffUntil"] = nil; return row }()}})
	h.env.ListJSON()
	if len(tok.seen) != 1 {
		t.Fatalf("격리된 토큰을 또 보냄: %v", tok.seen)
	}
}

func TestTransientRefreshFailureIsNotAStrike(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	tok.mode = "flaky"
	h.env.ListJSON()
	if row := h.usageRow("1"); row["authDeadStrikes"] == 1.0 || row["lastError"] != "refresh-failed" {
		t.Fatalf("행 = %v", row)
	}
}

func TestGateAdoptsANewerIdleProfileTokenFirst(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	dir := h.env.sessionDir("1", "a@gmail.com")
	must(t, os.MkdirAll(filepath.Join(dir, "sessions"), 0o700))
	// 프로필 안에서 claude 가 이미 갱신해 둔 더 새(아직 유효한) 세대: 백업으로 가져오고, 보낼 필요가 없다
	newer := `{"claudeAiOauth":{"accessToken":"pa","refreshToken":"rt-prof","expiresAt":` + jsonNum(time.Now().Add(time.Hour).UnixMilli()) + `}}`
	h.keychain(sessionService(dir), h.user, newer)
	r := h.env.consumeBackupGrant("1", "a@gmail.com", h.kcValue(backupService, "account-1-a@gmail.com"))
	if len(tok.seen) != 0 || r.creds != newer || h.kcValue(backupService, "account-1-a@gmail.com") != newer {
		t.Fatalf("보낸 토큰 = %v, 결과 %+v (프로필의 더 새 세대를 백업으로 가져와야 한다)", tok.seen, r)
	}
	// 프로필에서 claude 가 돌고 있으면 프로필 토큰은 그쪽 것이다. 건드리지 않고 백업을 보낸다
	h2 := newHome(t)
	tok2, _ := h2.expiredIdle()
	d2 := h2.env.sessionDir("1", "a@gmail.com")
	must(t, os.MkdirAll(filepath.Join(d2, "sessions"), 0o700))
	h2.keychain(sessionService(d2), h2.user, newer)
	must(t, os.WriteFile(filepath.Join(d2, "sessions", "1.json"), []byte(`{"pid":`+jsonNum(int64(os.Getpid()))+`}`), 0o600))
	h2.env.consumeBackupGrant("1", "a@gmail.com", h2.kcValue(backupService, "account-1-a@gmail.com"))
	if len(tok2.seen) != 1 || tok2.seen[0] != "rt-1" || h2.kcValue(sessionService(d2), h2.user) != newer {
		t.Fatalf("돌고 있는 프로필의 토큰을 가져감: %v", tok2.seen)
	}
}

// 그 계정 프로필에서 claude 가 돌고 있으면 토큰 주인은 그 claude 다. 수집이 대신 갱신하면 claude 가 쥔 세대가 무효가 된다.
func TestIdleRefreshNeverRunsUnderALiveSession(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	dir := h.env.sessionDir("1", "a@gmail.com")
	must(t, os.MkdirAll(filepath.Join(dir, "sessions"), 0o700))
	must(t, os.WriteFile(filepath.Join(dir, "sessions", "1.json"), []byte(`{"pid":`+jsonNum(int64(os.Getpid()))+`}`), 0o600))
	h.env.ListJSON()
	if len(tok.seen) != 0 {
		t.Fatalf("돌고 있는 세션의 토큰을 갱신함: %v", tok.seen)
	}
}

func TestGateSkipsThePOSTWhenTheBackupAlreadyRotated(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	snapshot := h.kcValue(backupService, "account-1-a@gmail.com")
	h.keychain(backupService, "account-1-a@gmail.com", creds("someone-refreshed", "rt-2", later)) // 그사이 다른 쪽이 갱신
	r := h.env.consumeBackupGrant("1", "a@gmail.com", snapshot)
	if len(tok.seen) != 0 || accessToken(r.creds) != "someone-refreshed" {
		t.Fatalf("이미 새 토큰이 있는데 보냄: %v", tok.seen)
	}
}

func TestCASConflictStashesTheSuccessorInsteadOfOverwriting(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	winner := creds("winner", "rt-winner", later)
	tok.hook = func() { h.keychain(backupService, "account-1-a@gmail.com", winner) } // 보내는 사이 다른 쪽이 백업을 바꿈
	snapshot := h.kcValue(backupService, "account-1-a@gmail.com")
	r := h.env.consumeBackupGrant("1", "a@gmail.com", snapshot)
	if h.kcValue(backupService, "account-1-a@gmail.com") != winner || r.creds != winner {
		t.Fatal("경쟁에서 이긴 값을 덮어씀")
	}
	en, verdict := h.env.readManifestEx()
	if verdict != "ok" || len(en) != 1 {
		t.Fatalf("보관본 = %v %s", en, verdict)
	}
	for id, v := range en {
		meta := v.(map[string]any)
		b, _ := h.env.readStashEntry(id)
		if meta["reason"] != "consume-gate-cas-conflict" || meta["consumedFp"] != fingerprint(snapshot) || oauthData(b)["refreshToken"] != "next-rt-1" || meta["configSlot"] != "1" {
			t.Fatalf("보관본 내용 = %v", meta)
		}
	}
}

func TestStashedSuccessorIsAdoptedBeforeAnyPOST(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	spent := h.kcValue(backupService, "account-1-a@gmail.com")
	succ := creds("succ", "rt-succ", later)
	must(t, h.env.stashSuccessor("1", succ, fingerprint(spent), "consume-gate-persist-failed"))
	r := h.env.consumeBackupGrant("1", "a@gmail.com", spent)
	if len(tok.seen) != 0 || r.creds != succ || h.kcValue(backupService, "account-1-a@gmail.com") != succ {
		t.Fatalf("이미 쓴 토큰을 다시 보냄 / 보관본을 안 씀: %v", tok.seen)
	}
	if en, _ := h.env.readManifestEx(); len(en) != 0 {
		t.Fatalf("채택한 보관본이 남음: %v", en)
	}
}

func TestUnreadableStashBlocksThePOST(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	spent := h.kcValue(backupService, "account-1-a@gmail.com")
	must(t, h.env.stashSuccessor("1", creds("s", "rt-s", later), fingerprint(spent), "x"))
	must(t, os.WriteFile(h.env.manifestPath(), []byte("{broken"), 0o600)) // 보관본 파일은 있는데 목록이 깨짐
	if r := h.env.consumeBackupGrant("1", "a@gmail.com", spent); r.err != "stash-unreadable" || len(tok.seen) != 0 {
		t.Fatalf("= %+v, 보냄 %v", r, tok.seen)
	}
}

func TestConsumeIsSerializedPerAccount(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	old := consumeLockTimeout
	consumeLockTimeout = 200 * time.Millisecond
	t.Cleanup(func() { consumeLockTimeout = old })
	must(t, os.MkdirAll(filepath.Join(h.env.Backup(), "credentials"), 0o700))
	unlock, err := flock(filepath.Join(h.env.Backup(), "credentials", ".consume-1.lock"), time.Second)
	must(t, err)
	defer unlock()
	if r := h.env.consumeBackupGrant("1", "a@gmail.com", h.kcValue(backupService, "account-1-a@gmail.com")); r.err != "consume-busy" || len(tok.seen) != 0 {
		t.Fatalf("다른 쪽이 보내는 중인데 또 보냄: %+v", r)
	}
}

func TestSlotLockLostAfterPOSTStashesAndDemotes(t *testing.T) {
	h := newHome(t)
	tok, _ := h.expiredIdle()
	old := slotLockTimeout
	slotLockTimeout = 200 * time.Millisecond
	t.Cleanup(func() { slotLockTimeout = old })
	var release func()
	tok.hook = func() { release, _ = flock(filepath.Join(h.env.Backup(), ".lock"), time.Second) } // 보내는 사이 누가 슬롯 잠금을 잡음
	before := h.kcValue(backupService, "account-1-a@gmail.com")
	r := h.env.consumeBackupGrant("1", "a@gmail.com", before)
	if release != nil {
		release()
	}
	if r.err != "transient" || !r.stashed || oauthData(r.creds)["refreshToken"] != "next-rt-1" || h.kcValue(backupService, "account-1-a@gmail.com") != before {
		t.Fatalf("= %+v", r)
	}
	// 다음 수집은 보관본을 채택하고 쓴 토큰을 다시 보내지 않는다
	r2 := h.env.consumeBackupGrant("1", "a@gmail.com", before)
	if len(tok.seen) != 1 || oauthData(r2.creds)["refreshToken"] != "next-rt-1" {
		t.Fatalf("보낸 기록 = %v", tok.seen)
	}
}

func TestRefreshErrorClassification(t *testing.T) {
	h := newHome(t)
	for _, c := range []struct {
		status int
		body   string
		want   string
	}{
		{400, `{"error":"invalid_grant"}`, "invalid_grant"},
		{401, `{"error":"invalid_client"}`, "invalid_client"},
		{500, `{"error":"invalid_grant"}`, "transient"}, // 상태 코드가 아니면 영구 실패로 보지 않는다
		{400, `invalid_grant`, "transient"},             // 본문 문자열 검색은 하지 않는다
		{200, `{"access_token":"x"}`, "transient"},      // expires_in 이 없으면 쓸 수 없는 응답
	} {
		h.token(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(c.status); w.Write([]byte(c.body)) })
		if got := refreshToken(creds("a", "rt", later)); got.err != c.want {
			t.Errorf("%d %s → %q, want %q", c.status, c.body, got.err, c.want)
		}
	}
	if got := refreshToken(`{"claudeAiOauth":{"accessToken":"a"}}`); got.err != "no_refresh_token" {
		t.Errorf("refresh token 없음 → %q", got.err)
	}
}
