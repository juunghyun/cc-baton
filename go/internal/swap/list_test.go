package swap

import (
	"encoding/base64"
	"encoding/json"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// 가짜 security: $FAKE_KC/<service>@<account> 파일이 비밀값. 없으면 44, LOCKED 가 있으면 잠김(51), READONLY 면 쓰기 실패.
// `security -i` 는 표준입력 한 줄을 셸처럼 다시 나눠 같은 명령으로 처리한다.
const fakeSecurity = `#!/bin/sh
[ -e "$FAKE_KC/LOCKED" ] && exit 51
if [ "$1" = "-i" ]; then read -r line; eval "set -- $line"; fi
cmd=$1; shift
while [ $# -gt 0 ]; do case "$1" in -a) a="$2"; shift;; -s) s="$2"; shift;; -X) x="$2"; shift;; esac; shift; done
f="$FAKE_KC/$s@$a"
case "$cmd" in
  find-generic-password) [ -f "$f" ] || exit 44; cat "$f"; echo;;
  add-generic-password) [ -e "$FAKE_KC/READONLY" ] && exit 1; printf '%s' "$x" | xxd -r -p > "$f";;
  delete-generic-password) [ -f "$f" ] || exit 44; rm "$f";;
esac
`

type fakeHome struct {
	t    *testing.T
	env  Env
	kc   string
	user string
}

func newHome(t *testing.T) *fakeHome {
	root := t.TempDir()
	h := &fakeHome{t: t, env: Env{Home: filepath.Join(root, "home")}, kc: filepath.Join(root, "kc"), user: "tester"}
	bin := filepath.Join(root, "security")
	must(t, os.WriteFile(bin, []byte(fakeSecurity), 0o755))
	must(t, os.MkdirAll(h.kc, 0o700))
	must(t, os.MkdirAll(filepath.Join(h.env.Backup(), "cache"), 0o700))
	old := securityBin
	securityBin = bin
	t.Cleanup(func() { securityBin = old })
	t.Setenv("FAKE_KC", h.kc)
	t.Setenv("USER", h.user)
	// 진짜 API 로 나가지 않게 가짜 서버를 둔다. 기본은 503(받기 실패), 테스트가 h.serve 로 바꾼다.
	h.serve(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) })
	h.profile(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) })
	return h
}

// profile 은 소유권 확인 API(/api/oauth/profile) 자리에 가짜 서버를 둔다.
func (h *fakeHome) profile(fn http.HandlerFunc) {
	srv := httptest.NewServer(fn)
	h.t.Cleanup(srv.Close)
	old := profileURL
	profileURL = srv.URL
	h.t.Cleanup(func() { profileURL = old })
}

func (h *fakeHome) serve(fn http.HandlerFunc) {
	srv := httptest.NewServer(fn)
	h.t.Cleanup(srv.Close)
	old := usageURL
	usageURL = srv.URL
	h.t.Cleanup(func() { usageURL = old })
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func (h *fakeHome) json(rel string, v any) {
	b, _ := json.Marshal(v)
	p := filepath.Join(h.env.Home, rel)
	must(h.t, os.MkdirAll(filepath.Dir(p), 0o700))
	must(h.t, os.WriteFile(p, b, 0o600))
}

func (h *fakeHome) keychain(service, account, value string) {
	must(h.t, os.WriteFile(filepath.Join(h.kc, service+"@"+account), []byte(value), 0o600))
}

func creds(access, refresh string, expiresAt int64) string {
	b, _ := json.Marshal(map[string]any{"claudeAiOauth": map[string]any{"accessToken": access, "refreshToken": refresh, "expiresAt": expiresAt}})
	return string(b)
}

var later = time.Now().Add(time.Hour).UnixMilli()

// 계정 1(gmail, 개인) 은 백업만, 계정 2(회사) 는 기본 로그인.
func (h *fakeHome) twoAccounts() {
	h.json(".claude-swap-backup/sequence.json", map[string]any{
		"activeAccountNumber": 2, "sequence": []int{1, 2},
		"accounts": map[string]any{
			"1": map[string]any{"email": "a@gmail.com", "organizationUuid": "", "organizationName": ""},
			"2": map[string]any{"email": "b@corp.com", "organizationUuid": "org2", "organizationName": "Corp", "alias": "work"},
		}})
	h.json(".claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": "b@corp.com", "organizationUuid": "org2"}})
	h.keychain(liveService, h.user, creds("live", "rt-live", later))
	h.keychain(backupService, "account-1-a@gmail.com", creds("bk", "rt-1", later))
}

func usageRow(email, org string, fetchedAgo float64, extra map[string]any) map[string]any {
	now := float64(time.Now().UnixNano()) / 1e9
	r := map[string]any{"email": email, "organizationUuid": org, "fetchedAt": now - fetchedAgo,
		"lastGood": map[string]any{"five_hour": map[string]any{"pct": 12.0, "resets_at": "2099-01-01T00:00:00+00:00"},
			"seven_day": map[string]any{"pct": 40.0}}}
	for k, v := range extra {
		r[k] = v
	}
	return r
}

func accountsByNum(t *testing.T, p map[string]any) map[float64]map[string]any {
	t.Helper()
	b, _ := json.Marshal(p) // 실제 출력처럼 JSON 을 거쳐 비교한다
	var back struct{ Accounts []map[string]any }
	must(t, json.Unmarshal(b, &back))
	out := map[float64]map[string]any{}
	for _, a := range back.Accounts {
		out[a["number"].(float64)] = a
	}
	return out
}

func TestListMarksActiveAndServesOnlyTrustedUsage(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{
		"2": usageRow("b@corp.com", "org2", 10, nil),
		// 300초 넘었고 신뢰 연장 사유 없음. 백오프 중이라 이번엔 받지도 않는다
		"1": usageRow("a@gmail.com", "", 1000, map[string]any{"nextPollAt": 0.0, "backoffUntil": float64(time.Now().Unix() + 600)}),
	}})
	p, err := h.env.ListJSON()
	must(t, err)
	if p["activeAccountNumber"] != 2 {
		t.Fatalf("active = %v", p["activeAccountNumber"])
	}
	acc := accountsByNum(t, p)
	a2, a1 := acc[2], acc[1]
	if a2["usageStatus"] != "ok" || a2["usage"].(map[string]any)["fiveHour"].(map[string]any)["pct"] != 12.0 || a2["alias"] != "work" {
		t.Fatalf("account 2 = %v", a2)
	}
	if a1["usageStatus"] != "unavailable" || a1["usage"] != nil || a1["lastGoodUsage"] == nil || a1["active"] != false {
		t.Fatalf("account 1 = %v", a1)
	}
}

func TestUsageRowOfAnotherAccountIsIgnored(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{
		"1": usageRow("someone-else@gmail.com", "", 10, nil)}})
	p, err := h.env.ListJSON()
	must(t, err)
	if a1 := accountsByNum(t, p)[1]; a1["usage"] != nil || a1["lastGoodUsage"] != nil {
		t.Fatalf("슬롯 재사용 전 계정의 사용량이 보임: %v", a1)
	}
}

func TestBackupReadStatuses(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	must(t, os.Remove(filepath.Join(h.kc, backupService+"@account-1-a@gmail.com")))
	p, _ := h.env.ListJSON()
	if s := accountsByNum(t, p)[1]["usageStatus"]; s != "no_credentials" {
		t.Fatalf("백업 없음 = %v", s)
	}

	// .enc 가 있으면 키체인이 비어도 그걸 쓴다
	enc := base64.StdEncoding.EncodeToString([]byte(creds("enc", "rt-enc", later)))
	must(t, os.MkdirAll(filepath.Join(h.env.Backup(), "credentials"), 0o700))
	must(t, os.WriteFile(filepath.Join(h.env.Backup(), "credentials", ".creds-1-a@gmail.com.enc"), []byte(enc), 0o600))
	if v, _ := h.env.readBackup("1", "a@gmail.com"); accessToken(v) != "enc" {
		t.Fatalf(".enc 우선 아님: %q", v)
	}
	must(t, os.Remove(filepath.Join(h.env.Backup(), "credentials", ".creds-1-a@gmail.com.enc")))

	// 잠긴 키체인은 "없음"이 아니다
	must(t, os.WriteFile(filepath.Join(h.kc, "LOCKED"), nil, 0o600))
	p, _ = h.env.ListJSON()
	acc := accountsByNum(t, p)
	if acc[1]["usageStatus"] != "keychain_unavailable" || acc[2]["usageStatus"] != "keychain_unavailable" {
		t.Fatalf("잠김 = %v / %v", acc[1]["usageStatus"], acc[2]["usageStatus"])
	}
}

func TestDeadTokenStrikeBindsToItsGeneration(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	for struck, dead := range map[string]bool{
		fingerprint(creds("bk", "rt-1", later)): true,
		"sha256:" + strings.Repeat("0", 64):     false, // 그새 다른 세대로 바뀌었으면 격리가 풀린다
	} {
		h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{
			"1": usageRow("a@gmail.com", "", 1000, map[string]any{"authDeadStrikes": 1, "struckFingerprint": struck})}})
		p, _ := h.env.ListJSON()
		s := accountsByNum(t, p)[1]["usageStatus"]
		row := readJSONMap(h.env.usagePath())["accounts"].(map[string]any)["1"].(map[string]any)
		if dead && (s != "relogin_required" || row["authDeadStrikes"] != 1.0) {
			t.Fatalf("격리가 풀림: %v %v", s, row["authDeadStrikes"])
		}
		if !dead && (s == "relogin_required" || row["authDeadStrikes"] != 0.0 || row["struckFingerprint"] != nil) {
			t.Fatalf("격리가 안 풀림: %v %v", s, row)
		}
	}
}

func TestExpiredActiveTokenIsTokenExpired(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.keychain(liveService, h.user, creds("live", "rt-live", time.Now().UnixMilli()))
	p, _ := h.env.ListJSON()
	if s := accountsByNum(t, p)[2]["usageStatus"]; s != "token_expired" {
		t.Fatalf("= %v", s)
	}
}

func TestCorruptSequenceIsAnErrorNotEmpty(t *testing.T) {
	h := newHome(t)
	must(t, os.WriteFile(filepath.Join(h.env.Backup(), "sequence.json"), []byte("{oops"), 0o600))
	if _, err := h.env.ListJSON(); err == nil {
		t.Fatal("깨진 sequence.json 을 빈 목록으로 읽음")
	}
}

func TestStatus(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	p, err := h.env.StatusJSON()
	must(t, err)
	a := p["active"].(map[string]any)
	if a["number"] != 2 || a["managed"] != true || p["totalManagedAccounts"] != 2 {
		t.Fatalf("status = %v", p)
	}
	h.json(".claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": "new@x.com"}})
	p, _ = h.env.StatusJSON()
	if a := p["active"].(map[string]any); a["managed"] != false || a["email"] != "new@x.com" {
		t.Fatalf("unmanaged = %v", p)
	}
}

// list/status 가 쓰는 건 사용량 캐시와 그 잠금 파일뿐이다. 계정 목록·매핑·키체인은 그대로여야 한다.
func TestListAndStatusWriteOnlyUsageCache(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{
		"2": usageRow("b@corp.com", "org2", 10, map[string]any{"authDeadStrikes": 1, "struckFingerprint": "sha256:x"})}})
	cache := filepath.Join(h.env.Backup(), "cache")
	snap := func() map[string]string {
		m := map[string]string{}
		for _, root := range []string{h.env.Home, h.kc} {
			filepath.WalkDir(root, func(p string, d fs.DirEntry, _ error) error {
				if d.IsDir() || filepath.Dir(p) == cache || filepath.Base(p) == "claude-swap.log" {
					return nil // 폴더 시각은 로그 파일이 생기면 바뀐다
				}
				info, _ := d.Info()
				b, _ := os.ReadFile(p)
				m[p] = info.ModTime().String() + info.Mode().String() + string(b)
				return nil
			})
		}
		return m
	}
	before := snap()
	h.env.ListJSON()
	h.env.StatusJSON()
	after := snap()
	for p := range after {
		if _, ok := before[p]; !ok {
			t.Fatalf("새 파일: %s", p)
		}
	}
	// 받기 실패(가짜 서버 503)는 claude-swap 과 같은 로그 파일에 [go] 표시로 남는다
	if b, _ := os.ReadFile(filepath.Join(h.env.Backup(), "claude-swap.log")); !strings.Contains(string(b), " - WARNING - [go] Usage fetch failed for account 1: http-503") { // 2번은 10초 전에 받아서 아직 차례가 아니다
		t.Fatalf("로그 = %q", b)
	}
	for p, v := range before {
		if after[p] != v {
			t.Fatalf("바뀐 파일: %s", p)
		}
	}
}
