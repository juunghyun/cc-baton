package swap

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// loginAs 는 Claude Code 에 새 계정으로 로그인한 상태를 만든다(설정 + 기본 키체인 항목).
func (h *fakeHome) loginAs(email, org, uuid, token string) {
	h.json(".claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": email, "organizationUuid": org, "accountUuid": uuid, "organizationName": "Org"}, "theme": "dark"})
	h.keychain(liveService, h.user, token)
}

func (h *fakeHome) ownerUUID(uuid string) {
	h.profile(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"account": map[string]any{"uuid": uuid}})
	})
}

func TestAddRegistersTheLoggedInAccount(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	tok := creds("new", "rt-new", later)
	h.loginAs("c@new.com", "", "u-c", tok)
	h.ownerUUID("u-c")
	num, updated, err := h.env.Add("New")
	must(t, err)
	if num != "3" || updated {
		t.Fatalf("= %s %v", num, updated)
	}
	m := h.seqRaw()
	rec := m["accounts"].(map[string]any)["3"].(map[string]any)
	if rec["email"] != "c@new.com" || rec["uuid"] != "u-c" || rec["organizationUuid"] != "" || rec["alias"] != "new" || rec["added"] == nil {
		t.Fatalf("rec = %v", rec)
	}
	if seq := m["sequence"].([]any); len(seq) != 3 || seq[2] != 3.0 || m["activeAccountNumber"] != 3.0 {
		t.Fatalf("sequence = %v active = %v", seq, m["activeAccountNumber"])
	}
	if h.kcValue(backupService, "account-3-c@new.com") != tok {
		t.Fatal("토큰 백업 없음")
	}
	cfg, _ := os.ReadFile(h.env.configPath("3", "c@new.com"))
	raw, _ := os.ReadFile(filepath.Join(h.env.Home, ".claude.json"))
	if string(cfg) != string(raw) || mode(t, h.env.configPath("3", "c@new.com")) != 0o600 {
		t.Fatal("설정 사본이 ~/.claude.json 원문과 다름")
	}
	if !strings.Contains(h.logText(), "[go] Added account 3: c@new.com (org: personal)") {
		t.Fatal(h.logText())
	}
}

func TestAddRefreshesAnExistingAccountInPlace(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.keychain(backupService, "account-2-b@corp.com", creds("old", "rt-old", later))
	tok := creds("relogin", "rt-re", later)
	h.loginAs("b@corp.com", "org2", "", tok)
	h.profile(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"account": map[string]any{"uuid": "u2", "email": "b@corp.com"}, "organization": map[string]any{"uuid": "org2"}})
	})
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{
		"2": map[string]any{"email": "b@corp.com", "organizationUuid": "org2", "authDeadStrikes": 1, "struckFingerprint": "x"}}})
	num, updated, err := h.env.Add("")
	must(t, err)
	if num != "2" || !updated || len(h.seqRaw()["sequence"].([]any)) != 2 {
		t.Fatalf("= %s %v", num, updated)
	}
	if h.kcValue(backupService, "account-2-b@corp.com") != tok || h.kcValue(backupService, "account-2-b@corp.com.prev") == "" {
		t.Fatal("갱신 안 됨 / .prev 없음")
	}
	if h.seqRaw()["accounts"].(map[string]any)["2"].(map[string]any)["alias"] != "work" {
		t.Fatal("기존 별칭이 사라짐")
	}
	if h.usageRow("2")["authDeadStrikes"] != 0.0 {
		t.Fatal("다시 로그인했는데 격리가 안 풀림")
	}
}

func TestAddRefusals(t *testing.T) {
	cases := map[string]func(h *fakeHome){
		"로그인 안 됨":   func(h *fakeHome) { h.json(".claude.json", map[string]any{}) },
		"API 키 로그인": func(h *fakeHome) { h.loginAs("c@new.com", "", "u-c", "sk-ant-api03-xyz") },
		"다른 계정 토큰": func(h *fakeHome) {
			h.loginAs("c@new.com", "", "u-c", creds("t", "rt", later))
			h.ownerUUID("u-someone-else")
		},
		"평문 대체 값만 있음": func(h *fakeHome) {
			h.loginAs("c@new.com", "", "u-c", creds("t", "rt", later))
			must(h.t, os.MkdirAll(filepath.Join(h.env.Home, ".claude"), 0o700))
			must(h.t, os.WriteFile(filepath.Join(h.env.Home, ".claude", ".credentials.json"), []byte(creds("old", "rt-old", later)), 0o600))
			must(h.t, os.WriteFile(filepath.Join(h.kc, "LOCKED"), nil, 0o600))
		},
		"별칭 겹침": func(h *fakeHome) {
			h.loginAs("c@new.com", "", "u-c", creds("t", "rt", later))
			h.ownerUUID("u-c")
		},
	}
	for name, setup := range cases {
		h := newHome(t)
		h.twoAccounts()
		setup(h)
		before := h.seqRaw()
		alias := ""
		if name == "별칭 겹침" {
			alias = "work"
		}
		if _, _, err := h.env.Add(alias); err == nil {
			t.Fatalf("%s: 거부 안 함", name)
		}
		os.Remove(filepath.Join(h.kc, "LOCKED"))
		if len(h.seqRaw()["accounts"].(map[string]any)) != len(before["accounts"].(map[string]any)) || h.kcValue(backupService, "account-3-c@new.com") != "" {
			t.Fatalf("%s: 거부했는데 뭔가 썼다", name)
		}
	}
}

func TestAddProceedsWithANoticeWhenOwnershipIsUnknown(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.loginAs("c@new.com", "", "u-c", creds("new", "rt-new", later)) // 소유권 API 는 기본 503
	if num, _, err := h.env.Add(""); err != nil || num != "3" {
		t.Fatalf("= %s %v", num, err)
	}
}

func TestAddTokenStoresSetupTokensLikePython(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	num, updated, err := h.env.AddToken(" sk-ant-oat01-abc \n", "")
	must(t, err)
	if num != "3" || updated {
		t.Fatalf("= %s %v", num, updated)
	}
	want := `{"claudeAiOauth": {"accessToken": "sk-ant-oat01-abc", "scopes": ["user:inference"]}}`
	if got := h.kcValue(backupService, "account-3-setup-token-3@token.local"); got != want {
		t.Fatalf("토큰 형식 = %s", got)
	}
	cfg := readJSONMap(h.env.configPath("3", "setup-token-3@token.local"))
	oa := cfg["oauthAccount"].(map[string]any)
	if oa["emailAddress"] != "setup-token-3@token.local" || oa["organizationUuid"] != nil {
		t.Fatalf("config = %v", cfg)
	}
	m := h.seqRaw()
	if m["activeAccountNumber"] != 2.0 || m["accounts"].(map[string]any)["3"].(map[string]any)["kind"] != nil {
		t.Fatalf("기본 로그인 계정이 바뀌었거나 kind 가 붙음: %v", m)
	}
	// 같은 이메일로 다시 넣으면 갱신
	if num, updated, err = h.env.AddToken("sk-ant-oat01-def", "setup-token-3@token.local"); err != nil || num != "3" || !updated {
		t.Fatalf("갱신 = %s %v %v", num, updated, err)
	}
	// API 키는 kind 가 붙고 원문 그대로 저장
	num, _, err = h.env.AddToken("sk-ant-api03-key", "")
	must(t, err)
	if num != "4" || h.kcValue(backupService, "account-4-api-key-4@token.local") != "sk-ant-api03-key" || h.seqRaw()["accounts"].(map[string]any)["4"].(map[string]any)["kind"] != "api_key" {
		t.Fatal("API 키 저장이 다름")
	}
	var ue *UserError
	for _, c := range [][2]string{{"", ""}, {"t", "not-an-email"}, {"sk-ant-api03-other", "setup-token-3@token.local"}} {
		if _, _, err := h.env.AddToken(c[0], c[1]); !errors.As(err, &ue) {
			t.Fatalf("%v 허용됨: %v", c, err)
		}
	}
}

func TestRemoveDeletesEverythingOfTheAccount(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	h.keychain(backupService, "account-1-a@gmail.com.prev", "old")
	h.json(".claude-swap-backup/configs/.claude-config-1-a@gmail.com.json", map[string]any{"oauthAccount": map[string]any{}})
	dir := h.profileDir("1", "a@gmail.com")
	must(t, markStale(dir))
	h.json(".claude-swap-backup/mappings.json", map[string]any{"schemaVersion": 1, "mappings": map[string]any{
		"/p": map[string]any{"email": "a@gmail.com", "organizationUuid": ""}, "/w": map[string]any{"email": "b@corp.com", "organizationUuid": "org2"}}})
	h.json(".claude-swap-backup/cache/usage.json", map[string]any{"schemaVersion": 2, "accounts": map[string]any{"1": map[string]any{"email": "a@gmail.com"}}})

	num, email, err := h.env.RemoveTarget("a@gmail.com")
	must(t, err)
	must(t, h.env.Remove(num))
	if email != "a@gmail.com" || h.kcValue(backupService, "account-1-a@gmail.com") != "" || h.kcValue(backupService, "account-1-a@gmail.com.prev") != "" ||
		h.kcValue(sessionService(dir), h.user) != "" || exists(dir) || isStale(dir) || exists(h.env.configPath("1", "a@gmail.com")) {
		t.Fatal("남은 것이 있다")
	}
	m := h.seqRaw()
	if _, ok := m["accounts"].(map[string]any)["1"]; ok || len(m["sequence"].([]any)) != 1 {
		t.Fatalf("sequence = %v", m)
	}
	mp := h.env.loadMappings()
	if mp["/p"] != nil || mp["/w"] == nil || h.usageRow("1") == nil {
		t.Fatalf("매핑 정리 = %v", mp)
	}
}

func TestRemoveRefusesWhileTheProfileMayBeInUse(t *testing.T) {
	for _, rec := range []string{`{"pid":` + jsonNum(int64(os.Getpid())) + `}`, "{garbage"} {
		h := newHome(t)
		h.twoAccounts()
		dir := h.profileDir("1", "a@gmail.com")
		must(t, os.WriteFile(filepath.Join(dir, "sessions", "1.json"), []byte(rec), 0o600))
		if _, _, err := h.env.RemoveTarget("1"); err == nil {
			t.Fatalf("%s: 거부 안 함", rec)
		}
		if err := h.env.Remove("1"); err == nil || !exists(dir) || h.kcValue(backupService, "account-1-a@gmail.com") == "" {
			t.Fatalf("%s: 확인 뒤 떠오른 세션인데 지움", rec)
		}
	}
	h := newHome(t)
	h.twoAccounts()
	if _, _, err := h.env.RemoveTarget("not an id"); err == nil {
		t.Fatal("잘못된 식별자를 허용함")
	}
}

func TestFirstAddOnAFreshMachine(t *testing.T) {
	h := newHome(t)
	h.loginAs("c@new.com", "", "u-c", creds("new", "rt-new", later))
	h.ownerUUID("u-c")
	num, _, err := h.env.Add("")
	must(t, err)
	if num != "1" || mode(t, h.env.Backup()) != 0o700 || mode(t, filepath.Join(h.env.Backup(), "credentials")) != 0o700 {
		t.Fatalf("= %s", num)
	}
}
