package swap

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// 가짜 claude: `auth status --json` 에 진짜처럼 답한다. 프로필에 토큰(평문 seed 또는 해시 이름 키체인 항목)이 있으면
// .claude.json 의 이메일로 로그인된 것으로 본다. $FAKE_CLAUDE_MODE 가 garbage/invalid 면 그렇게 답한다.
const fakeClaude = `#!/bin/sh
[ "$1" = auth ] || exit 0
mode=$(cat "$FAKE_CLAUDE_MODE" 2>/dev/null)
[ "$mode" = garbage ] && { echo nope; exit 0; }
[ "$mode" = invalid ] && exit 1
svc="Claude Code-credentials-$(printf '%s' "$CLAUDE_CONFIG_DIR" | shasum -a 256 | cut -c1-8)"
[ -s "$CLAUDE_CONFIG_DIR/.credentials.json" ] || [ -f "$FAKE_KC/$svc@$USER" ] || exit 1
email=$(grep -o '"emailAddress": *"[^"]*"' "$CLAUDE_CONFIG_DIR/.claude.json" | head -1 | sed 's/.*: *"//; s/"$//')
echo "{\"loggedIn\":true,\"authMethod\":\"claude.ai\",\"email\":\"$email\"}"
`

type runHome struct {
	*fakeHome
	mode string
}

func newRunHome(t *testing.T) *runHome {
	h := &runHome{fakeHome: newHome(t)}
	h.twoAccounts()
	bin := filepath.Join(filepath.Dir(h.kc), "bin")
	must(t, os.MkdirAll(bin, 0o755))
	must(t, os.WriteFile(filepath.Join(bin, "claude"), []byte(fakeClaude), 0o755))
	t.Setenv("PATH", bin+":"+os.Getenv("PATH"))
	h.mode = filepath.Join(filepath.Dir(h.kc), "claude-mode")
	t.Setenv("FAKE_CLAUDE_MODE", h.mode)
	t.Setenv("CLAUDE_CONFIG_DIR", "") // 끝나면 원래대로 돌려놓게 등록만 하고
	os.Unsetenv("CLAUDE_CONFIG_DIR")  // baton.zsh 처럼 아예 지운다(env -u)
	t.Setenv("ANTHROPIC_API_KEY", "sk-should-be-dropped")
	h.json(".claude-swap-backup/configs/.claude-config-1-a@gmail.com.json", map[string]any{
		"oauthAccount": map[string]any{"emailAddress": "a@gmail.com", "organizationUuid": ""}, "theme": "light", "projects": map[string]any{"x": 1}})
	return h
}

func (h *runHome) setMode(m string) { must(h.t, os.WriteFile(h.mode, []byte(m), 0o600)) }

func envOf(l *Launch, k string) (string, bool) {
	for _, kv := range l.Env {
		if key, v, _ := strings.Cut(kv, "="); key == k {
			return v, true
		}
	}
	return "", false
}

func TestRunDefaultLoginLaunchesClaudeDirectly(t *testing.T) {
	h := newRunHome(t)
	l, err := h.env.PrepareRun("work", []string{"--resume"}, true)
	must(t, err)
	if _, set := envOf(l, "CLAUDE_CONFIG_DIR"); set || !strings.Contains(l.Note, "already the active default login") || l.Argv[1] != "--resume" {
		t.Fatalf("launch = %+v", l)
	}
	if exists(h.env.sessionDir("2", "b@corp.com")) {
		t.Fatal("기본 로그인 계정에 프로필을 만듦")
	}
}

func TestRunBootstrapsAFreshProfile(t *testing.T) {
	h := newRunHome(t)
	l, err := h.env.PrepareRun("1", []string{"-p", "hi"}, true)
	must(t, err)
	dir := h.env.sessionDir("1", "a@gmail.com")
	if v, _ := envOf(l, "CLAUDE_CONFIG_DIR"); v != dir {
		t.Fatalf("CLAUDE_CONFIG_DIR = %q", v)
	}
	if _, set := envOf(l, "ANTHROPIC_API_KEY"); set || !slices.Equal(l.Argv[1:], []string{"-p", "hi"}) {
		t.Fatalf("env/argv = %v %v", l.Env, l.Argv)
	}
	seed, _ := os.ReadFile(filepath.Join(dir, ".credentials.json"))
	if string(seed) != creds("bk", "rt-1", later) || mode(t, dir) != 0o700 || mode(t, filepath.Join(dir, ".credentials.json")) != 0o600 {
		t.Fatal("seed 가 백업과 다르거나 권한이 다름")
	}
	cfg := readJSONMap(filepath.Join(dir, ".claude.json"))
	if cfg["oauthAccount"].(map[string]any)["emailAddress"] != "a@gmail.com" || cfg["hasCompletedOnboarding"] != true || cfg["theme"] != "light" || cfg["projects"] != nil {
		t.Fatalf(".claude.json = %v (설정 사본에서 계정·테마만 가져와야 함)", cfg)
	}
	if !strings.Contains(h.logText(), "[go] Bootstrapped session profile for account 1") {
		t.Fatal("로그 없음")
	}
}

func TestRunReusesAValidProfile(t *testing.T) {
	h := newRunHome(t)
	_, err := h.env.PrepareRun("1", nil, true)
	must(t, err)
	dir := h.env.sessionDir("1", "a@gmail.com")
	must(t, os.WriteFile(filepath.Join(dir, "history.jsonl"), []byte("mine"), 0o600))
	before, _ := os.Stat(filepath.Join(dir, ".credentials.json"))
	_, err = h.env.PrepareRun("1", nil, true)
	must(t, err)
	after, _ := os.Stat(filepath.Join(dir, ".credentials.json"))
	if !os.SameFile(before, after) || strings.Count(h.logText(), "Bootstrapped") != 1 {
		t.Fatal("유효한 프로필을 다시 채움")
	}
}

// 유효한 프로필은 백업과 세대가 달라도 그대로 쓴다. 프로필 안에서 claude 가 갱신하므로 대개 프로필 쪽이 더 새 세대다.
func TestRunKeepsAValidProfileEvenIfItsGenerationDiffers(t *testing.T) {
	h := newRunHome(t)
	dir := h.profileDir("1", "a@gmail.com") // 프로필은 rt-p, 백업은 rt-1
	h.json(".claude-swap-backup/sessions/1-a_gmail.com/.claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": "a@gmail.com"}})
	_, err := h.env.PrepareRun("1", nil, true)
	must(t, err)
	if h.kcValue(sessionService(dir), h.user) == "" || strings.Contains(h.logText(), "Bootstrapped") {
		t.Fatal("프로필의 (더 새) 토큰을 백업으로 덮음")
	}
}

func TestRunRefusesToReseedALiveInvalidProfile(t *testing.T) {
	h := newRunHome(t)
	dir := h.profileDir("1", "a@gmail.com")
	must(t, os.WriteFile(filepath.Join(dir, "sessions", "9.json"), []byte(`{"pid":`+jsonNum(int64(os.Getpid()))+`}`), 0o600))
	h.setMode("invalid")
	var ue *UserError
	if _, err := h.env.PrepareRun("1", nil, true); !errors.As(err, &ue) || !strings.Contains(err.Error(), "in use") {
		t.Fatalf("= %v", err)
	}
	if h.kcValue(sessionService(dir), h.user) == "" {
		t.Fatal("돌고 있는 프로필의 토큰을 지움")
	}
}

func TestRunConsumesAStaleMarker(t *testing.T) {
	h := newRunHome(t)
	dir := h.profileDir("1", "a@gmail.com")
	h.json(".claude-swap-backup/sessions/1-a_gmail.com/.claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": "a@gmail.com"}})
	must(t, markStale(dir))
	_, err := h.env.PrepareRun("1", nil, true)
	must(t, err)
	seed, _ := os.ReadFile(filepath.Join(dir, ".credentials.json"))
	if isStale(dir) || string(seed) != creds("bk", "rt-1", later) || h.kcValue(sessionService(dir), h.user) != "" {
		t.Fatal("표시를 보고 새 백업으로 다시 채우지 않음")
	}
}

func TestRunFailedValidationKeepsTheProfileFolder(t *testing.T) {
	h := newRunHome(t)
	dir := h.env.sessionDir("1", "a@gmail.com")
	must(t, os.MkdirAll(filepath.Join(dir, "projects"), 0o700))
	must(t, os.WriteFile(filepath.Join(dir, "projects", "t.jsonl"), []byte("conversation"), 0o600))
	h.setMode("invalid")
	if _, err := h.env.PrepareRun("1", nil, true); err == nil || !strings.Contains(err.Error(), "failed validation") {
		t.Fatalf("= %v", err)
	}
	if b, _ := os.ReadFile(filepath.Join(dir, "projects", "t.jsonl")); string(b) != "conversation" || exists(filepath.Join(dir, ".credentials.json")) {
		t.Fatal("대화 기록을 지웠거나 실패한 토큰을 남김")
	}
}

func TestRunTrustsArtifactsWhenTheProbeDoesNotAnswer(t *testing.T) {
	h := newRunHome(t)
	_, err := h.env.PrepareRun("1", nil, true)
	must(t, err)
	h.setMode("garbage")
	_, err = h.env.PrepareRun("1", nil, true)
	must(t, err)
	if strings.Count(h.logText(), "Bootstrapped") != 1 {
		t.Fatal("확인이 답을 못 줬다고 다시 채움")
	}
}

func TestRunRefusals(t *testing.T) {
	h := newRunHome(t)
	m := h.seqRaw()
	m["accounts"].(map[string]any)["1"].(map[string]any)["kind"] = "api_key"
	h.json(".claude-swap-backup/sequence.json", m)
	if _, err := h.env.PrepareRun("1", nil, true); err == nil || !strings.Contains(err.Error(), "API-key") {
		t.Fatalf("api key = %v", err)
	}
	t.Setenv("PATH", "/nonexistent")
	if _, err := h.env.PrepareRun("2", nil, true); err == nil || !strings.Contains(err.Error(), "not found on PATH") {
		t.Fatalf("no claude = %v", err)
	}
}

func TestSharingLinksOnlyWhatItOwns(t *testing.T) {
	h := newRunHome(t)
	src := filepath.Join(h.env.Home, ".claude")
	must(t, os.MkdirAll(filepath.Join(src, "skills", "s1"), 0o755))
	real := filepath.Join(h.env.Home, "dotfiles", "settings.json")
	must(t, os.MkdirAll(filepath.Dir(real), 0o755))
	must(t, os.WriteFile(real, []byte("{}"), 0o644))
	must(t, os.Symlink(real, filepath.Join(src, "settings.json"))) // 이미 링크면 원본으로 링크한다
	must(t, os.WriteFile(filepath.Join(src, "keybindings.json"), []byte("{}"), 0o644))
	dir := h.env.sessionDir("1", "a@gmail.com")
	must(t, os.MkdirAll(dir, 0o700))
	must(t, os.WriteFile(filepath.Join(dir, "keybindings.json"), []byte("own"), 0o600)) // 사용자 것

	_, err := h.env.PrepareRun("1", nil, true)
	must(t, err)
	realResolved, _ := filepath.EvalSymlinks(real) // Python resolve() 처럼 끝까지 푼 경로(/var → /private/var)
	if l, _ := os.Readlink(filepath.Join(dir, "settings.json")); l != realResolved {
		t.Fatalf("settings.json → %q", l)
	}
	if l, _ := os.Readlink(filepath.Join(dir, "skills")); l != filepath.Join(src, "skills") {
		t.Fatalf("skills → %q", l)
	}
	if b, _ := os.ReadFile(filepath.Join(dir, "keybindings.json")); string(b) != "own" {
		t.Fatal("사용자 파일을 덮음")
	}
	if items := readManifest(filepath.Join(dir, shareManifest)); !slices.Equal(items, []string{"settings.json", "skills"}) {
		t.Fatalf("manifest = %v", items)
	}
	_, err = h.env.PrepareRun("1", nil, false) // --no-share: 우리가 만든 링크만 걷는다
	must(t, err)
	if exists(filepath.Join(dir, "settings.json")) || exists(filepath.Join(dir, "skills")) || !exists(filepath.Join(dir, "keybindings.json")) || !exists(filepath.Join(src, "skills", "s1")) {
		t.Fatal("--no-share 정리 결과가 다름")
	}
	// 목록엔 남아 있는데 사용자가 링크를 자기 폴더로 바꿔 놓았으면, 그 폴더는 지우지 않는다
	_, err = h.env.PrepareRun("1", nil, true)
	must(t, err)
	must(t, os.Remove(filepath.Join(dir, "skills")))
	must(t, os.MkdirAll(filepath.Join(dir, "skills", "mine"), 0o700))
	_, err = h.env.PrepareRun("1", nil, false)
	must(t, err)
	if !exists(filepath.Join(dir, "skills", "mine")) {
		t.Fatal("사용자 폴더를 지움")
	}
}

func TestMCPServersMirrorTheDefaultProfile(t *testing.T) {
	h := newRunHome(t)
	h.json(".claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": "b@corp.com", "organizationUuid": "org2"},
		"mcpServers": map[string]any{"github": map[string]any{"command": "gh"}}})
	dir := h.env.sessionDir("1", "a@gmail.com")
	_, err := h.env.PrepareRun("1", nil, true) // 새 프로필
	must(t, err)
	cfg := readJSONMap(filepath.Join(dir, ".claude.json"))
	if cfg["mcpServers"].(map[string]any)["github"] == nil || !exists(filepath.Join(dir, mcpMarker)) {
		t.Fatalf("반영 안 됨: %v", cfg["mcpServers"])
	}

	// 기능이 생기기 전부터 프로필에만 있던 정의는 한 번 보관한 뒤 덮는다
	h2 := newRunHome(t)
	h2.json(".claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": "b@corp.com", "organizationUuid": "org2"},
		"mcpServers": map[string]any{"github": map[string]any{"command": "gh"}}})
	_, err = h2.env.PrepareRun("1", nil, true)
	must(t, err)
	d2 := h2.env.sessionDir("1", "a@gmail.com")
	must(t, os.Remove(filepath.Join(d2, mcpMarker)))
	c2 := readJSONMap(filepath.Join(d2, ".claude.json"))
	c2["mcpServers"] = map[string]any{"local-only": map[string]any{"command": "x"}}
	must(t, writeJSONAtomic(filepath.Join(d2, ".claude.json"), c2))
	_, err = h2.env.PrepareRun("1", nil, true)
	must(t, err)
	stash := readJSONMap(filepath.Join(d2, mcpStash))
	if stash["mcpServers"].(map[string]any)["local-only"] == nil {
		t.Fatalf("보관본 = %v", stash)
	}
	if got := readJSONMap(filepath.Join(d2, ".claude.json"))["mcpServers"].(map[string]any); got["local-only"] != nil || got["github"] == nil {
		t.Fatalf("반영 = %v", got)
	}
	// 기본 프로필에서 지우면 세션에서도 지워진다
	h2.json(".claude.json", map[string]any{"oauthAccount": map[string]any{"emailAddress": "b@corp.com", "organizationUuid": "org2"}})
	_, err = h2.env.PrepareRun("1", nil, true)
	must(t, err)
	if _, has := readJSONMap(filepath.Join(d2, ".claude.json"))["mcpServers"]; has {
		t.Fatal("삭제가 전파 안 됨")
	}
}
