package swap

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

var emailRE = regexp.MustCompile(`^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`)

// ensureStore 는 처음 쓰는 기계에서 백업 폴더와 빈 sequence.json 을 만든다. 이미 있으면 건드리지 않는다.
func (e Env) ensureStore() error {
	for _, d := range []string{e.Backup(), filepath.Join(e.Backup(), "configs"), filepath.Join(e.Backup(), "credentials")} {
		if err := os.MkdirAll(d, 0o700); err != nil {
			return err
		}
		os.Chmod(d, 0o700)
	}
	if exists(filepath.Join(e.Backup(), "sequence.json")) {
		return nil
	}
	return writeJSONAtomic(filepath.Join(e.Backup(), "sequence.json"),
		map[string]any{"activeAccountNumber": nil, "lastUpdated": timestamp(), "sequence": []any{}, "accounts": map[string]any{}})
}

func (e Env) configPath(num, email string) string {
	return filepath.Join(e.Backup(), "configs", ".claude-config-"+num+"-"+email+".json")
}

func nextSlot(m map[string]any) string {
	max := 0
	for k := range accountsOf(m) {
		if n, err := strconv.Atoi(k); err == nil && n > max {
			max = n
		}
	}
	return strconv.Itoa(max + 1)
}

func addToSequence(m map[string]any, num string) {
	n, _ := strconv.Atoi(num)
	seq, _ := m["sequence"].([]any)
	for _, v := range seq {
		if f, ok := v.(float64); ok && int(f) == n {
			return
		}
	}
	seq = append(seq, float64(n))
	sort.Slice(seq, func(i, j int) bool { a, _ := seq[i].(float64); b, _ := seq[j].(float64); return a < b })
	m["sequence"] = seq
}

func slotByIdentity(m map[string]any, email, org string) string {
	for n, a := range accountsOf(m) {
		if a["email"] == email && orEmpty(a, "organizationUuid") == org {
			return n
		}
	}
	return ""
}

// Add 는 지금 Claude Code 에 로그인된 계정을 등록한다(이미 있으면 그 슬롯의 토큰·설정을 갱신). cc-baton 설치 마법사가 부른다.
// shortcut: --slot(지정 슬롯에 넣기·옮기기·덮어쓰기)은 cc-baton 이 안 써서 없다. 필요해지면 switcher.add_account 의 displace/migrate 를 옮긴다.
func (e Env) Add(alias string) (num string, updated bool, err error) {
	if alias != "" {
		if alias, err = normalizeAlias(alias); err != nil {
			return
		}
	}
	if err = e.ensureStore(); err != nil {
		return
	}
	cfg := readJSONMap(e.GlobalConfig())
	oa, _ := cfg["oauthAccount"].(map[string]any)
	email, org, uuid := str(oa, "emailAddress"), str(oa, "organizationUuid"), str(oa, "accountUuid")
	if email == "" {
		return "", false, &ConfigError{"No active Claude account found. Please log in first."}
	}
	creds, _, degraded := e.readActive()
	switch {
	case degraded:
		return "", false, &UserError{"The macOS Keychain is unreadable right now (locked or no GUI session), so the only readable credential is a plaintext fallback that may be a superseded generation. Retry from a GUI terminal."}
	case creds == "":
		return "", false, &UserError{"No credentials found for current account"}
	case looksLikeAPIKey(creds):
		return "", false, &UserError{"Active login is an API-key account. Add it with 'add-token sk-ant-api...' instead."}
	}
	if err = e.rejectForeignCapture(creds, email, org, uuid); err != nil {
		return
	}
	// 확인하는 사이 토큰이 회전했거나 다른 계정으로 /login 했으면, 옛 세대나 남의 토큰을 등록하게 된다
	if now, _, _ := e.readActive(); now != "" && fingerprint(now) != fingerprint(creds) {
		return "", false, &ConfigError{"The stored credential rotated while it was being verified. Nothing was changed. Re-run when no refresh is in flight."}
	}
	raw, rerr := os.ReadFile(e.GlobalConfig())
	if rerr != nil {
		return "", false, &ConfigError{"Claude config file not found"}
	}
	cfg2 := readJSONMap(e.GlobalConfig())
	oa2, _ := cfg2["oauthAccount"].(map[string]any)
	if str(oa2, "emailAddress") != email || str(oa2, "organizationUuid") != org || str(oa2, "accountUuid") != uuid {
		return "", false, &ConfigError{fmt.Sprintf("The active account changed while %s was being verified. Nothing was changed. Re-run when no other login is in flight.", email)}
	}
	m, err := e.loadSequenceRaw()
	if err != nil {
		return
	}
	num = slotByIdentity(m, email, org)
	updated = num != ""
	if !updated {
		num = nextSlot(m)
	}
	if owner := aliasOwner(accountsOf(m), alias); alias != "" && owner != "" && owner != num {
		return "", false, &UserError{fmt.Sprintf("Alias '%s' is already used by account %s", alias, owner)}
	}
	if err = e.storeAccount(num, email, org, creds, raw); err != nil {
		return
	}
	accs, _ := m["accounts"].(map[string]any)
	if accs == nil {
		accs = map[string]any{}
		m["accounts"] = accs
	}
	if updated {
		a := accountsOf(m)[num]
		if alias != "" {
			a["alias"] = alias
		}
	} else {
		rec := map[string]any{"email": email, "uuid": uuid, "organizationUuid": org, "organizationName": str(oa, "organizationName"), "added": timestamp()}
		if alias != "" {
			rec["alias"] = alias
		}
		accs[num] = rec
		addToSequence(m, num)
	}
	n, _ := strconv.Atoi(num)
	m["activeAccountNumber"] = n // cc-baton 이 "기본 프로필 계정"을 이 값으로 판단한다
	if err = e.saveSequence(m); err != nil {
		return
	}
	if updated {
		e.log("INFO", fmt.Sprintf("Updated credentials for account %s: %s", num, email))
	} else {
		orgLabel := org
		if orgLabel == "" {
			orgLabel = "personal"
		}
		e.log("INFO", fmt.Sprintf("Added account %s: %s (org: %s)", num, email, orgLabel))
	}
	return
}

// storeAccount 는 토큰 백업 → 설정 사본 → 격리 해제 순으로 쓴다(새 토큰이니 이전 격리는 의미가 없다).
func (e Env) storeAccount(num, email, org, creds string, config []byte) error {
	if err := e.writeAccountCredentials(num, email, creds); err != nil {
		return err
	}
	if err := writeFileAtomic(e.configPath(num, email), config); err != nil {
		return err
	}
	e.clearDeadToken(num, [2]string{email, org})
	return nil
}

// rejectForeignCapture: 설정은 A 계정인데 키체인엔 B 토큰이 남아 있는 경우를 막는다. 확인을 못 하면 안내만 하고 진행한다.
func (e Env) rejectForeignCapture(creds, email, org, uuid string) error {
	unverified := func(why string) error {
		fmt.Printf("Notice: could not verify that the stored credential belongs to %s (%s). Registering anyway; re-run where the check can complete to confirm.\n", email, why)
		return nil
	}
	tok := accessToken(creds)
	if tok == "" {
		return unverified("no access token to resolve")
	}
	if tokenExpired(oauthData(creds)) {
		return unverified("the access token is expired")
	}
	p := e.fetchProfile(tok)
	if p == nil {
		return unverified("the identity lookup did not resolve")
	}
	if uuid != "" {
		if *p.uuid != uuid {
			return &ConfigError{fmt.Sprintf("The stored credential does not belong to %s: the token resolves to account %s, not %s. Nothing was changed. Log in as %s in THIS environment, then re-run.", email, *p.uuid, uuid, email)}
		}
	} else {
		if p.email == nil || strings.TrimSpace(*p.email) == "" {
			return unverified("the resolved identity carries no address")
		}
		if !strings.EqualFold(strings.TrimSpace(*p.email), email) {
			return &ConfigError{fmt.Sprintf("The stored credential does not belong to %s: the token resolves to %s. Nothing was changed. Log in as %s in THIS environment, then re-run.", email, *p.email, email)}
		}
	}
	if p.org == nil || strings.TrimSpace(*p.org) == org {
		return nil
	}
	return &ConfigError{fmt.Sprintf("The stored credential for %s belongs to another organization (%s, not %s). Nothing was changed.", email, strings.TrimSpace(*p.org), orDefault(org, "personal"))}
}

func orDefault(s, d string) string {
	if s == "" {
		return d
	}
	return s
}

// setupTokenCreds 는 Python json.dumps 와 같은 바이트로 만든다. refresh token 이 없어 fingerprint 가 전체 해시라서다.
func setupTokenCreds(tok string) string {
	q, _ := json.Marshal(tok)
	return `{"claudeAiOauth": {"accessToken": ` + string(q) + `, "scopes": ["user:inference"]}}`
}

// AddToken 은 setup-token 이나 API 키를 계정으로 등록한다. 네트워크를 쓰지 않고, 기본 로그인 계정(activeAccountNumber)도 바꾸지 않는다.
func (e Env) AddToken(tok, email string) (num string, updated bool, err error) {
	tok = strings.TrimSpace(tok)
	if tok == "" {
		return "", false, &UserError{"Token cannot be empty"}
	}
	isKey := looksLikeAPIKey(tok)
	if email != "" && !emailRE.MatchString(email) {
		return "", false, &UserError{"Invalid email format: " + email}
	}
	if err = e.ensureStore(); err != nil {
		return
	}
	m, err := e.loadSequenceRaw()
	if err != nil {
		return
	}
	if email == "" {
		label := "setup-token"
		if isKey {
			label = "api-key"
		}
		email = label + "-" + nextSlot(m) + "@token.local"
	}
	num = slotByIdentity(m, email, "")
	if num != "" && (accountsOf(m)[num]["kind"] == "api_key") != isKey {
		return "", false, &UserError{fmt.Sprintf("'%s' already exists as a different kind of account (slot %s). Pass a distinct --email.", email, num)}
	}
	updated = num != ""
	if !updated {
		num = nextSlot(m)
	}
	creds := tok
	if !isKey {
		creds = setupTokenCreds(tok)
	}
	q, _ := json.Marshal(email)
	config := `{"oauthAccount": {"emailAddress": ` + string(q) + `, "accountUuid": "", "organizationUuid": null, "organizationName": null}}`
	if err = e.storeAccount(num, email, "", creds, []byte(config)); err != nil {
		return
	}
	if !updated {
		rec := map[string]any{"email": email, "uuid": "", "organizationUuid": "", "organizationName": "", "added": timestamp()}
		if isKey {
			rec["kind"] = "api_key"
		}
		accs, _ := m["accounts"].(map[string]any)
		accs[num] = rec
		addToSequence(m, num)
	}
	if err = e.saveSequence(m); err != nil {
		return
	}
	src := "token"
	if isKey {
		src = "API key"
	}
	if updated {
		e.log("INFO", fmt.Sprintf("Updated %s for account %s: %s", src, num, email))
	} else {
		e.log("INFO", fmt.Sprintf("Added account %s from %s: %s", num, src, email))
	}
	return
}

// RemoveTarget 은 지울 계정을 찾고, 그 프로필에서 claude 가 돌고 있으면(또는 확실히 모르면) 거부한다. 확인 질문 전에 부른다.
// shortcut: 같은 이메일이 여러 조직 계정에 있으면 Python 은 번호를 묻지만 여기선 "번호로 지정하라"는 에러를 낸다. 그런 계정을 쓰게 되면 선택 질문 추가.
func (e Env) RemoveTarget(id string) (num, email string, err error) {
	m, err := e.loadSequenceRaw()
	if err != nil {
		return
	}
	if !isDigits(id) && aliasOwner(accountsOf(m), id) == "" && !emailRE.MatchString(id) {
		return "", "", &UserError{"Invalid account identifier: " + id}
	}
	num, a, err := resolve(m, id)
	if err != nil {
		return
	}
	email = str(a, "email")
	if live, unreadable := liveSessions(e.sessionDir(num, email)); live > 0 || unreadable > 0 {
		return "", "", &UserError{fmt.Sprintf("Account-%s (%s) has a running session-mode claude (or an unreadable session record). Exit it first, then retry.", num, email)}
	}
	return
}

// Remove 는 계정의 토큰 백업(.prev 포함)·설정 사본·세션 프로필·폴더 매핑을 지운다. 사용량 캐시 행은 남긴다(신원이 달라지면 안 보인다).
func (e Env) Remove(num string) error {
	m, err := e.loadSequenceRaw()
	if err != nil {
		return err
	}
	a, ok := accountsOf(m)[num]
	if !ok {
		return &UserError{fmt.Sprintf("Account-%s does not exist", num)}
	}
	email, org := str(a, "email"), str(a, "organizationUuid")
	dir := e.sessionDir(num, email)
	if live, unreadable := liveSessions(dir); live > 0 || unreadable > 0 { // 확인 질문 사이에 새로 떴을 수 있다
		return &UserError{fmt.Sprintf("Account-%s (%s) has a running session-mode claude. Exit it first, then retry.", num, email)}
	}
	for _, n := range []string{num, "None"} { // "None" 은 옛 버전이 남긴 이름
		acct := "account-" + n + "-" + email
		os.Remove(e.encPath(n, email))
		os.Remove(e.encPath(n, email) + ".prev")
		kcDelete(backupService, acct) // 잠겨서 남아도 sequence 에서 빠지면 아무도 안 읽는다
		kcDelete(backupService, acct+".prev")
	}
	os.Remove(e.configPath(num, email))
	if exists(dir) {
		kcDelete(sessionService(dir), kcAccount())
		if err := os.RemoveAll(dir); err != nil {
			e.log("WARNING", fmt.Sprintf("Could not fully remove account %s's session profile at %s", num, dir))
		} else {
			e.log("INFO", fmt.Sprintf("Removed session profile for account %s at %s", num, dir))
		}
	}
	clearStale(dir)
	accs, _ := m["accounts"].(map[string]any)
	delete(accs, num)
	n, _ := strconv.Atoi(num)
	var seq []any
	old, _ := m["sequence"].([]any)
	for _, v := range old {
		if f, ok := v.(float64); !ok || int(f) != n {
			seq = append(seq, v)
		}
	}
	if seq == nil {
		seq = []any{}
	}
	m["sequence"] = seq
	if err := e.saveSequence(m); err != nil {
		return err
	}
	e.log("INFO", fmt.Sprintf("Removed account %s: %s", num, email))
	mp := e.loadMappings()
	pruned := 0
	for k, v := range mp {
		en, _ := v.(map[string]any)
		if en["email"] == email && str(en, "organizationUuid") == org {
			delete(mp, k)
			pruned++
		}
	}
	if pruned > 0 {
		return e.saveMappings(mp)
	}
	return nil
}
