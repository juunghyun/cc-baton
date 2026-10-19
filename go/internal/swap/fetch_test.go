package swap

import (
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const apiUsage = `{"five_hour":{"utilization":30,"resets_at":"2099-01-01T05:00:00+00:00"},
 "seven_day":{"utilization":55,"resets_at":"2099-01-07T00:00:00+00:00"},
 "extra_usage":{"is_enabled":true,"used_credits":1234,"monthly_limit":5000,"utilization":24.7},
 "limits":[{"scope":{"model":{"display_name":"Fable"}},"percent":12,"resets_at":"2099-01-07T00:00:00+00:00"},{"percent":5}]}`

// fakeAPI 는 받은 요청의 토큰을 기록하고, 토큰별로 응답을 고른다.
type fakeAPI struct {
	mu     sync.Mutex
	tokens []string
	reply  func(token string, w http.ResponseWriter)
}

func (h *fakeHome) api(reply func(token string, w http.ResponseWriter)) *fakeAPI {
	a := &fakeAPI{reply: reply}
	h.serve(func(w http.ResponseWriter, r *http.Request) {
		tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if r.Header.Get("User-Agent") != "claude-swap/1.0" || r.Header.Get("anthropic-beta") != "oauth-2025-04-20" {
			w.WriteHeader(400)
			return
		}
		a.mu.Lock()
		a.tokens = append(a.tokens, tok)
		a.mu.Unlock()
		a.reply(tok, w)
	})
	return a
}

func ok200(_ string, w http.ResponseWriter) { w.Write([]byte(apiUsage)) }

func (h *fakeHome) usageRow(n string) map[string]any {
	row, _ := readJSONMap(h.env.usagePath())["accounts"].(map[string]any)[n].(map[string]any)
	return row
}

func TestFetchRecordsUsageAndPlan(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	api := h.api(ok200)
	p, err := h.env.ListJSON()
	must(t, err)
	if len(api.tokens) != 2 || api.tokens[0] != "bk" || api.tokens[1] != "live" {
		t.Fatalf("요청 = %v (1번은 백업 토큰, 2번은 기본 로그인 토큰, 순서대로)", api.tokens)
	}
	acc := accountsByNum(t, p)
	if acc[1]["usageStatus"] != "ok" || acc[2]["usageStatus"] != "ok" {
		t.Fatalf("상태 = %v / %v", acc[1]["usageStatus"], acc[2]["usageStatus"])
	}
	row := h.usageRow("2")
	lg := row["lastGood"].(map[string]any)
	fh := lg["five_hour"].(map[string]any)
	sp := lg["spend"].(map[string]any)
	sc := lg["scoped"].([]any)
	if fh["pct"] != 30.0 || fh["resets_at"] != "2099-01-01T05:00:00+00:00" || sp["used"] != 12.34 || sp["limit"] != 50.0 || sp["currency"] != "USD" ||
		len(sc) != 1 || sc[0].(map[string]any)["name"] != "Fable" {
		t.Fatalf("lastGood = %v", lg)
	}
	if row["claimId"] != nil || row["consecutiveFailures"] != 0.0 || row["email"] != "b@corp.com" || row["organizationUuid"] != "org2" {
		t.Fatalf("행 = %v", row)
	}
	// 처음 받은 계정: 기본 간격(활성 180, 나머지 300) ±10%
	now := float64(time.Now().Unix())
	for n, iv := range map[string]float64{"2": 180, "1": 300} {
		r := h.usageRow(n)
		next := r["nextPollAt"].(float64)
		if r["pollIntervalS"] != iv || next < now+iv*0.89 || next > now+iv*1.11 {
			t.Fatalf("계정 %s plan = %v %v", n, r["pollIntervalS"], next-now)
		}
	}
	if st, _ := os.Stat(h.env.usagePath()); st.Mode().Perm() != 0o600 {
		t.Fatal("권한")
	}
	// 바로 다시 부르면 받을 차례가 아니다
	h.env.ListJSON()
	if len(api.tokens) != 2 {
		t.Fatalf("180초 안에 다시 받음: %v", api.tokens)
	}
}

func TestLiveClaimOfAnotherCollectorIsRespected(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	api := h.api(ok200)
	soon := float64(time.Now().Unix() + 60)
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{
		"1": map[string]any{"email": "a@gmail.com", "organizationUuid": "", "claimId": "python", "claimUntil": soon},
		"2": map[string]any{"email": "b@corp.com", "organizationUuid": "org2", "claimId": "python", "claimUntil": soon},
	}})
	h.env.ListJSON()
	if len(api.tokens) != 0 || h.usageRow("1")["claimId"] != "python" {
		t.Fatalf("다른 수집기가 받는 중인데 또 받음: %v", api.tokens)
	}
}

// 받는 도중 임대가 만료돼 다른 수집기(Python)가 행을 가져갔으면, 늦게 끝난 쪽 결과는 버린다.
func TestLateResultAfterLostClaimIsDropped(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.api(func(tok string, w http.ResponseWriter) {
		rows := readJSONMap(h.env.usagePath())
		row := rows["accounts"].(map[string]any)["2"].(map[string]any)
		row["claimId"] = "python"
		writeJSONAtomic(h.env.usagePath(), rows)
		ok200(tok, w)
	})
	h.env.ListJSON()
	if row := h.usageRow("2"); row["claimId"] != "python" || row["lastGood"] != nil {
		t.Fatalf("남의 임대 위에 덮어씀: %v", row)
	}
}

func TestRateLimitBacksOffAndLogs(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.api(func(_ string, w http.ResponseWriter) { w.Header().Set("Retry-After", "3600"); w.WriteHeader(429) })
	h.env.ListJSON()
	row := h.usageRow("2")
	now := float64(time.Now().Unix())
	if row["lastError"] != "http-429" || row["consecutiveFailures"] != 1.0 || row["last429At"] == nil {
		t.Fatalf("행 = %v", row)
	}
	if b := row["backoffUntil"].(float64) - now; math.Abs(b-4500) > 5 { // 3600 + 900, 상한 4500
		t.Fatalf("backoff = %v", b)
	}
	log, _ := os.ReadFile(filepath.Join(h.env.Backup(), "claude-swap.log"))
	if !strings.Contains(string(log), "account 2: http-429, retry-after 3600s (usage-endpoint budget reached; backing off)") || strings.Contains(string(log), "@") {
		t.Fatalf("로그 = %s", log)
	}
}

func TestIdleAccountPrefersItsSessionProfileToken(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	api := h.api(ok200)
	dir := h.env.sessionDir("1", "a@gmail.com")
	must(t, os.MkdirAll(dir, 0o700))
	h.keychain(sessionService(dir), h.user, creds("prof", "rt-p", later))
	h.env.ListJSON()
	if !contains(api.tokens, "prof") || contains(api.tokens, "bk") {
		t.Fatalf("프로필 토큰을 안 씀: %v", api.tokens)
	}

	// 프로필 안에서 다른 계정으로 /login 했으면 그 토큰은 이 계정 것이 아니다
	h2 := newHome(t)
	h2.twoAccounts()
	api2 := h2.api(ok200)
	dir2 := h2.env.sessionDir("1", "a@gmail.com")
	must(t, os.MkdirAll(dir2, 0o700))
	h2.keychain(sessionService(dir2), h2.user, creds("prof", "rt-p", later))
	h2.json(".claude-swap-backup/sessions/1-a_gmail.com/.claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": "other@x.com"}})
	h2.env.ListJSON()
	if contains(api2.tokens, "prof") || !contains(api2.tokens, "bk") {
		t.Fatalf("다른 계정 프로필 토큰을 씀: %v", api2.tokens)
	}
}

func TestExpiredIdleBackupIsNotSentAndClaimIsReleased(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.keychain(backupService, "account-1-a@gmail.com", creds("bk", "rt-1", time.Now().UnixMilli()))
	api := h.api(ok200)
	p, _ := h.env.ListJSON()
	if contains(api.tokens, "bk") {
		t.Fatal("만료된 백업 토큰을 보냄")
	}
	if s := accountsByNum(t, p)[1]["usageStatus"]; s != "token_expired" {
		t.Fatalf("= %v", s)
	}
	if row := h.usageRow("1"); row["claimId"] != nil || row["lastError"] != nil {
		t.Fatalf("임대가 안 풀렸거나 실패로 기록됨: %v", row)
	}
}

func TestActive401IsRecordedNotRefreshed(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	api := h.api(func(tok string, w http.ResponseWriter) {
		if tok == "live" {
			w.WriteHeader(401)
			return
		}
		ok200(tok, w)
	})
	h.env.ListJSON()
	if row := h.usageRow("2"); row["lastError"] != "http-401" || len(api.tokens) != 2 {
		t.Fatalf("행 = %v, 요청 = %v", row, api.tokens)
	}
}

func TestReserveReplacesAnotherAccountsRow(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.api(ok200)
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{
		"1": usageRow("old@gmail.com", "", 10, map[string]any{"authDeadStrikes": 1}),
		"7": map[string]any{"email": "keep@x.com"}, // 이번 수집과 무관한 슬롯은 그대로
	}})
	h.env.ListJSON()
	rows := readJSONMap(h.env.usagePath())["accounts"].(map[string]any)
	if r := h.usageRow("1"); r["email"] != "a@gmail.com" || r["authDeadStrikes"] != 0.0 || rows["7"] == nil {
		t.Fatalf("rows = %v", rows)
	}
}

func TestUnparseableCacheIsLeftAlone(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	api := h.api(ok200)
	must(t, os.WriteFile(h.env.usagePath(), []byte(`{"schemaVersion": 2, "accounts": {"1": {"pct": NaN}}}`), 0o600))
	h.env.ListJSON()
	if b, _ := os.ReadFile(h.env.usagePath()); !strings.Contains(string(b), "NaN") || len(api.tokens) != 0 {
		t.Fatalf("Python 이 쓴 파일을 덮어씀 / 요청 %v", api.tokens)
	}
}

func TestFailureBackoff(t *testing.T) {
	f := func(v float64) *float64 { return &v }
	for _, c := range []struct {
		fails int
		ra    *float64
		rl    bool
		want  float64
	}{
		{1, nil, false, 30}, {3, nil, false, 120}, {9, nil, false, 600}, {2000, nil, false, 600},
		{1, f(0), true, 300}, {1, f(0), false, 30},
		{1, f(120), true, 120}, {6, f(120), true, 600},
		{1, f(3600), true, 4500}, {1, f(7200), false, 3600},
	} {
		if got := failureBackoff(c.fails, c.ra, c.rl); got != c.want {
			t.Errorf("failureBackoff(%d, %v, %v) = %v, want %v", c.fails, c.ra, c.rl, got, c.want)
		}
	}
}

func TestPlanAfterFetch(t *testing.T) {
	now := 1_000_000.0
	mid := func() float64 { return 0.5 } // 지터 0
	u := func(p5, p7 float64) map[string]any {
		return map[string]any{"five_hour": map[string]any{"pct": p5}, "seven_day": map[string]any{"pct": p7}}
	}
	iv := func(v float64) *float64 { return &v }
	cases := []struct {
		name   string
		prev   entry
		usage  map[string]any
		active bool
		want   float64
	}{
		{"첫 측정", entry{}, u(10, 20), false, 300},
		{"움직임 → 절반", entry{lastGood: u(10, 20), pollIntervalS: iv(600)}, u(10, 25), false, 300},
		{"정지 → 1.5배(상한)", entry{lastGood: u(10, 20), pollIntervalS: iv(500)}, u(10, 20), false, 600},
		{"활성이 한도 근처에서 움직임 → 60초", entry{lastGood: u(70, 20), pollIntervalS: iv(180)}, u(80, 20), true, 60},
		{"429 직후 → 360초 이상", entry{lastGood: u(70, 20), pollIntervalS: iv(180), last429At: iv(now - 10)}, u(80, 20), true, 360},
		{"한도 소진 → 600초 이상", entry{lastGood: u(100, 20), pollIntervalS: iv(180)}, u(100, 20), true, 600},
	}
	for _, c := range cases {
		got := planAfterFetch(c.prev, c.usage, c.active, now, mid)
		if got[1] != c.want || got[0] != now+c.want {
			t.Errorf("%s: %v, want interval %v", c.name, got, c.want)
		}
	}
	// 다음 호출은 가장 가까운 리셋 + 60초를 넘지 않는다
	soon := time.Unix(int64(now)+100, 0).UTC().Format(time.RFC3339)
	got := planAfterFetch(entry{}, map[string]any{"five_hour": map[string]any{"pct": 10.0, "resets_at": soon}}, false, now, mid)
	if got[0] != now+160 {
		t.Errorf("리셋 상한 = %v", got[0]-now)
	}
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}
