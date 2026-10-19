package swap

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"time"
)

// 세션에서 계정을 덮어쓸 수 있는 환경변수. 세션 모드에선 빼야 고른 계정으로 돈다(session.py AUTH_OVERRIDE_ENV_VARS).
var authOverrides = []string{"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
	"CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR"}

// 기본 프로필(~/.claude)에서 세션 프로필로 링크해 같이 쓰는 것들. 대화 기록(projects, history.jsonl)은 cc-baton 이 쓰지 않아 뺐다.
var sharedItems = []string{"settings.json", "keybindings.json", "CLAUDE.md", "skills", "commands", "agents"}

const (
	shareManifest = ".cswap-shared.json"
	mcpMarker     = ".cswap-mcp-mirror-v1"
	mcpStash      = ".cswap-mcp-displaced.json"
)

func sessionEnv(dir string) []string {
	var env []string
	for _, kv := range os.Environ() {
		k, _, _ := strings.Cut(kv, "=")
		if k == "CLAUDE_CONFIG_DIR" || slices.Contains(authOverrides, k) {
			continue
		}
		env = append(env, kv)
	}
	return append(env, "CLAUDE_CONFIG_DIR="+dir)
}

// validity 는 `claude auth status --json` 으로 프로필 로그인을 확인한다(네트워크 없이 로컬만 본다).
// "unknown" = 실행은 됐는데 답이 없음, "unreachable" = claude 를 못 띄움. 둘 다 프로필이 나쁘다는 증거가 아니다.
func validity(dir, email, org string) string {
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return "invalid"
	}
	bin, err := exec.LookPath("claude")
	if err != nil {
		bin = "claude"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, "auth", "status", "--json")
	cmd.Env = sessionEnv(dir)
	out, err := cmd.Output()
	if ctx.Err() != nil {
		return "unknown"
	}
	var ee *exec.ExitError
	if err != nil && !errors.As(err, &ee) {
		return "unreachable"
	}
	if err != nil {
		return "invalid"
	}
	var st map[string]any
	if json.Unmarshal(out, &st) != nil || st == nil {
		return "unknown"
	}
	if st["loggedIn"] != true || st["authMethod"] != "claude.ai" || st["email"] != email {
		return "invalid"
	}
	if o, _ := st["orgId"].(string); o != "" && org != "" && o != org {
		return "invalid"
	}
	return "valid"
}

// artifactsUsable: 확인이 답을 못 줄 때, 토큰이 확실히 없지 않고 다른 계정으로 바뀌지 않았으면 쓸 수 있다고 본다.
func artifactsUsable(dir, email, org string) bool {
	material := false
	if b, err := os.ReadFile(filepath.Join(dir, ".credentials.json")); err == nil && len(b) > 0 {
		material = true
	} else if v, err := kcGet(sessionService(dir), kcAccount()); err != nil || v != "" {
		material = true // 키체인을 못 읽는 건 "없음"이 아니다
	}
	return material && !sessionDrifted(dir, email, org)
}

func usable(dir, email, org string) bool {
	if v := validity(dir, email, org); v != "unknown" {
		return v == "valid"
	}
	return artifactsUsable(dir, email, org)
}

func quiescent(dir string) bool {
	live, unreadable := liveSessions(dir)
	return live == 0 && unreadable == 0
}

// invalidate 는 프로필의 토큰만 비운다. 대화 기록과 .claude.json 은 남는다.
func (e Env) invalidate(num, dir string) error {
	if err := kcDelete(sessionService(dir), kcAccount()); err != nil {
		return err
	}
	os.Remove(filepath.Join(dir, ".credentials.json"))
	clearStale(dir)
	e.log("INFO", "Invalidated session credentials for account "+num)
	return nil
}

func (e Env) configBackup(num, email string) map[string]any {
	return readJSONMap(filepath.Join(e.Backup(), "configs", ".claude-config-"+num+"-"+email+".json"))
}

// bootstrap 은 백업 토큰과 설정으로 프로필을 다시 채운다. claude 가 처음 쓸 때 평문 seed 를 키체인으로 옮긴다.
// Python 과 달리 프로필이 조용할 때만 한다: 돌고 있는 세션의 키체인 항목을 지우면 그 대화가 로그아웃된다(spec R2).
func (e Env) bootstrap(num, email, dir string) error {
	if st, err := os.Stat(dir); err == nil && st.IsDir() && !quiescent(dir) {
		return &UserError{fmt.Sprintf("Account-%s's session profile is in use by a running claude (or a session record is unreadable); close it and retry", num)}
	}
	if !isASCII(dir) {
		return &UserError{"session profile path has non-ASCII characters; not supported yet"} // sessionService 의 NFC 생략 때문
	}
	if err := kcDelete(sessionService(dir), kcAccount()); err != nil {
		return &UserError{"keychain is unreadable right now (locked?); retry from a GUI terminal"}
	}
	creds, unreadable := e.readBackup(num, email)
	if creds == "" {
		if unreadable {
			return &UserError{fmt.Sprintf("Account-%s's backup is in the macOS Keychain but it is unreadable right now (locked or no GUI session). Retry from a GUI terminal; do not re-add.", num)}
		}
		return &UserError{fmt.Sprintf("Account-%s has no stored credentials. Log in with it and re-add it.", num)}
	}
	cfg := e.configBackup(num, email)
	oa, _ := cfg["oauthAccount"].(map[string]any)
	if len(oa) == 0 {
		return &UserError{fmt.Sprintf("Account-%s has no stored config backup. Log in with it and re-add it.", num)}
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	os.Chmod(dir, 0o700)
	if err := writeFileAtomic(filepath.Join(dir, ".credentials.json"), []byte(creds)); err != nil {
		return err
	}
	cur := readJSONMap(filepath.Join(dir, ".claude.json"))
	if cur == nil {
		cur = map[string]any{}
	}
	cur["oauthAccount"], cur["hasCompletedOnboarding"] = oa, true
	if _, ok := cur["theme"]; !ok {
		theme, _ := cfg["theme"].(string)
		if theme == "" {
			theme = "dark"
		}
		cur["theme"] = theme
	}
	if err := writeJSONAtomic(filepath.Join(dir, ".claude.json"), cur); err != nil {
		return err
	}
	e.log("INFO", fmt.Sprintf("Bootstrapped session profile for account %s at %s", num, dir))
	return nil
}

func writeFileAtomic(path string, b []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+"-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	f.Chmod(0o600)
	_, werr := f.Write(b)
	if cerr := f.Close(); werr != nil || cerr != nil {
		return errors.Join(werr, cerr)
	}
	return os.Rename(f.Name(), path)
}

// setupSession 은 프로필을 쓸 수 있게 만든다(session.py setup_session).
// shortcut: Python 은 다시 채우기 전에 백업 토큰을 한 번 갱신한다. 넘겨받은 토큰으로 claude 가 스스로 갱신하므로 생략했다(spec R12).
func (e Env) setupSession(num, email, org string, share bool) (string, error) {
	dir := e.sessionDir(num, email)
	if !(isStale(dir) && quiescent(dir)) && usable(dir, email, org) {
		e.syncSharing(dir, share)
		return dir, nil
	}
	unlock, err := flock(filepath.Join(e.Backup(), ".lock"), 30*time.Second)
	if err != nil {
		return "", &UserError{"another cswap is busy with the account store; retry in a moment"}
	}
	defer unlock()
	if isStale(dir) && quiescent(dir) {
		if err := e.invalidate(num, dir); err != nil {
			return "", err
		}
	}
	if usable(dir, email, org) {
		if !e.profileMatchesBackup(num, email, dir) && quiescent(dir) {
			if err := e.bootstrap(num, email, dir); err != nil {
				return "", err
			}
		}
		e.syncSharing(dir, share)
		return dir, nil
	}
	if err := e.bootstrap(num, email, dir); err != nil {
		return "", err
	}
	e.syncSharing(dir, share)
	v := validity(dir, email, org)
	if v == "unknown" && artifactsUsable(dir, email, org) {
		v = "valid"
	}
	switch v {
	case "valid":
		return dir, nil
	case "invalid":
		// Python 은 여기서 프로필 폴더를 통째로 지운다(대화 기록까지). 여기서는 토큰만 비우고 폴더는 남긴다.
		e.invalidate(num, dir)
		return "", &UserError{fmt.Sprintf("Session profile for Account-%s (%s) failed validation. Log in with that account and re-add it.", num, email)}
	}
	return "", &UserError{fmt.Sprintf("Session profile for Account-%s (%s) could not be verified: `claude auth status` did not run or did not answer. The profile is left in place — check that `claude` is on PATH, then retry.", num, email)}
}

// profileMatchesBackup: 같은 세대인지. 한쪽을 못 읽으면 "맞다"고 본다(읽기 실패로 멀쩡한 프로필을 갈아엎지 않게).
func (e Env) profileMatchesBackup(num, email, dir string) bool {
	prof := sessionCreds(dir)
	backup, _ := e.readBackup(num, email)
	return prof == "" || backup == "" || fingerprint(prof) == fingerprint(backup)
}

// syncSharing 은 매 실행마다 맞춘다. 우리가 만든 링크(목록에 있는 것)만 지우고, 사용자 파일은 건드리지 않는다.
func (e Env) syncSharing(dir string, share bool) {
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return
	}
	e.syncMCP(dir, share)
	manifest := filepath.Join(dir, shareManifest)
	managed := readManifest(manifest)
	active := sharedItems
	if !share {
		active = nil
	}
	for _, name := range managed {
		if !slices.Contains(active, name) {
			removeManaged(filepath.Join(dir, name))
		}
	}
	if len(active) == 0 {
		os.Remove(manifest)
		return
	}
	src := filepath.Join(e.Home, ".claude")
	var now []string
	for _, name := range active {
		from, to := filepath.Join(src, name), filepath.Join(dir, name)
		if _, err := os.Stat(from); err != nil {
			if slices.Contains(managed, name) {
				removeManaged(to)
			}
			continue
		}
		target := from
		if r, err := filepath.EvalSymlinks(from); err == nil {
			if li, _ := os.Lstat(from); li != nil && li.Mode()&os.ModeSymlink != 0 {
				target = r
			}
		}
		if li, err := os.Lstat(to); err == nil {
			if li.Mode()&os.ModeSymlink != 0 {
				if cur, _ := os.Readlink(to); cur != target {
					os.Remove(to)
					if os.Symlink(target, to) != nil {
						continue
					}
				}
				now = append(now, name)
				continue
			}
			if !slices.Contains(managed, name) {
				fmt.Fprintf(os.Stderr, "Not sharing %s: the session profile already has its own copy.\n", name)
				continue
			}
			removeManaged(to)
		}
		if err := os.Symlink(target, to); err != nil {
			e.log("WARNING", fmt.Sprintf("Failed to share %s into session: %v", name, err))
			continue
		}
		now = append(now, name)
	}
	b, _ := json.MarshalIndent(map[string]any{"items": now, "mode": "symlink"}, "", "  ")
	writeFileAtomic(manifest, b)
}

func readManifest(p string) []string {
	m := readJSONMap(p)
	items, _ := m["items"].([]any)
	var out []string
	for _, it := range items {
		if s, ok := it.(string); ok && slices.Contains(sharedItems, s) {
			out = append(out, s)
		}
	}
	return out
}

// removeManaged 는 링크와 파일만 지운다. Python 은 목록에 있는 실제 폴더도 통째로 지우지만, 맥에선 우리가 만든 건 항상 링크라 폴더는 남긴다.
func removeManaged(p string) {
	if li, err := os.Lstat(p); err == nil && !li.IsDir() {
		os.Remove(p)
	}
}

// syncMCP 는 기본 프로필의 사용자 MCP 서버(~/.claude.json mcpServers)를 프로필 .claude.json 에 그대로 반영한다.
// 처음 반영할 때 프로필에만 있던 정의는 한 번만 따로 보관하고 덮는다. 무엇이든 이상하면 손대지 않는다(session.py _sync_mcp_servers).
func (e Env) syncMCP(dir string, share bool) {
	cfg, marker := filepath.Join(dir, ".claude.json"), filepath.Join(dir, mcpMarker)
	var source map[string]any
	if share {
		if source = e.mcpSource(); source == nil {
			return
		}
	} else if exists(marker) {
		source = map[string]any{}
	} else {
		return
	}
	read := func() (map[string]any, map[string]any, bool) {
		if li, err := os.Lstat(cfg); err != nil || !li.Mode().IsRegular() {
			return nil, nil, false
		}
		cur := readJSONMap(cfg)
		if cur == nil {
			return nil, nil, false
		}
		target, ok := cur["mcpServers"].(map[string]any)
		if _, has := cur["mcpServers"]; has && !ok {
			return nil, nil, false
		}
		if target == nil {
			target = map[string]any{}
		}
		return cur, target, true
	}
	_, target, ok := read()
	if !ok || (reflect.DeepEqual(target, source) && (!share || exists(marker))) {
		return
	}
	unlock, err := properLock(cfg+".lock", 10*time.Second)
	if err != nil {
		e.log("WARNING", "Could not sync MCP servers (lock busy) — skipping this launch.")
		return
	}
	defer unlock()
	if share {
		if source = e.mcpSource(); source == nil {
			return
		}
	}
	cur, target, ok := read()
	if !ok {
		return
	}
	if reflect.DeepEqual(target, source) {
		if share {
			touch(marker)
		}
		return
	}
	if share && !exists(marker) {
		displaced := map[string]any{}
		for k, v := range target {
			if sv, ok := source[k]; !ok || !reflect.DeepEqual(sv, v) {
				displaced[k] = v
			}
		}
		if len(displaced) > 0 && !e.stashMCP(dir, displaced) {
			return // 유일한 사본을 지우지 않는다
		}
	}
	if len(source) > 0 {
		cur["mcpServers"] = source
	} else {
		delete(cur, "mcpServers")
	}
	if err := writeJSONAtomic(cfg, cur); err != nil {
		e.log("WARNING", "Could not sync MCP servers: "+err.Error())
		return
	}
	if share {
		touch(marker)
	}
}

func (e Env) mcpSource() map[string]any {
	cfg := readJSONMap(e.GlobalConfig())
	if cfg == nil {
		return nil
	}
	v, has := cfg["mcpServers"]
	if !has {
		return map[string]any{}
	}
	m, _ := v.(map[string]any)
	return m
}

func (e Env) stashMCP(dir string, displaced map[string]any) bool {
	p := filepath.Join(dir, mcpStash)
	if li, err := os.Lstat(p); err == nil {
		m := readJSONMap(p)
		_, ok := m["mcpServers"].(map[string]any)
		return li.Mode().IsRegular() && ok // 이미 있는 올바른 보관본이 원래 데이터다. 덮지 않는다
	}
	if err := writeJSONAtomic(p, map[string]any{"schemaVersion": 1, "mcpServers": displaced}); err != nil {
		return false
	}
	fmt.Fprintf(os.Stderr, "Session MCP servers now mirror your default profile; the profile's previous definitions were saved to %s.\n", mcpStash)
	return true
}

func touch(p string) {
	if f, err := os.OpenFile(p, os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
		f.Close()
	}
}

// Launch 는 exec 할 claude 와 인자·환경을 돌려준다. exec 는 호출자(main)가 한다: 테스트가 자기 프로세스를 바꾸지 않게.
type Launch struct {
	Bin  string
	Argv []string
	Env  []string
	Note string // exec 전에 보여 줄 한 줄
}

// PrepareRun 은 `cswap run <계정> [-- 인자]` 다(session.py run).
// shortcut: 계정 없이 부르기(폴더 매핑으로 고르기), --share-history, --require-session 은 cc-baton 이 안 써서 없다.
func (e Env) PrepareRun(id string, args []string, share bool) (*Launch, error) {
	bin, err := exec.LookPath("claude")
	if err != nil {
		return nil, &UserError{"'claude' was not found on PATH. Install Claude Code first."}
	}
	m, err := e.loadSequenceRaw()
	if err != nil {
		return nil, err
	}
	num, a, err := resolve(m, id)
	if err != nil {
		return nil, err
	}
	email, org := str(a, "email"), str(a, "organizationUuid")
	if a["kind"] == "api_key" {
		return nil, &UserError{fmt.Sprintf("Account-%s (%s) is an API-key account; session mode does not support API-key accounts yet.", num, email)}
	}
	argv := append([]string{bin}, args...)
	if os.Getenv("CLAUDE_CONFIG_DIR") == "" {
		if ce, co, ok := e.identity(); ok && ce == email && co == org {
			// 기본 로그인 계정이면 프로필 없이 그냥 claude. 같은 토큰의 사본을 둘 만들면 어긋난다
			return &Launch{bin, argv, os.Environ(), fmt.Sprintf("Account-%s (%s) is already the active default login — launching claude directly.", num, email)}, nil
		}
	} else {
		fmt.Fprintf(os.Stderr, "Warning: CLAUDE_CONFIG_DIR is already set (%s); overriding it for this launch.\n", os.Getenv("CLAUDE_CONFIG_DIR"))
	}
	var dropped []string
	for _, k := range authOverrides {
		if os.Getenv(k) != "" {
			dropped = append(dropped, k)
		}
	}
	if len(dropped) > 0 {
		fmt.Fprintf(os.Stderr, "Warning: ignoring %s for this session — it would override the selected account inside Claude Code.\n", strings.Join(dropped, ", "))
	}
	dir, err := e.setupSession(num, email, org, share)
	if err != nil {
		return nil, err
	}
	return &Launch{bin, argv, sessionEnv(dir), fmt.Sprintf("Launching Account-%s (%s) [session mode]", num, email)}, nil
}
