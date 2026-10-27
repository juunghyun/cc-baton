package swap

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

// claude-swap list/status --json 의 schema v1. cc-baton 은 이 출력을 파싱하지 않지만 형식은 맞춰 둔다.
const jsonSchema = 1

// ConfigError 는 sequence.json 이 있는데 읽을 수 없을 때. 이때 "계정 없음"으로 보면 add 가 슬롯을 덮어쓴다(spec R8).
type ConfigError struct{ msg string }

func (e *ConfigError) Error() string { return e.msg }

type sequence struct {
	Sequence []int                     `json:"sequence"`
	Accounts map[string]map[string]any `json:"accounts"`
}

// loadSequence: 없으면 (nil, nil). 있는데 깨졌으면 ConfigError.
func (e Env) loadSequence() (*sequence, error) {
	b, err := os.ReadFile(filepath.Join(e.Backup(), "sequence.json"))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, &ConfigError{fmt.Sprintf("cannot read sequence.json: %v", err)}
	}
	var s sequence
	if err := json.Unmarshal(b, &s); err != nil {
		return nil, &ConfigError{fmt.Sprintf("sequence.json is corrupt: %v", err)}
	}
	return &s, nil
}

// identity 는 기본 프로필 설정의 (email, org). 로그인 안 돼 있으면 ok=false.
func (e Env) identity() (email, org string, ok bool) {
	cfg := readJSONMap(e.GlobalConfig())
	oa, _ := cfg["oauthAccount"].(map[string]any)
	email, _ = oa["emailAddress"].(string)
	org, _ = oa["organizationUuid"].(string)
	return email, org, email != ""
}

func str(m map[string]any, k string) string { s, _ := m[k].(string); return s }

func (s *sequence) slotOf(email, org string) string {
	for n, a := range s.Accounts {
		if a["email"] == email && orEmpty(a, "organizationUuid") == org {
			return n
		}
	}
	return ""
}

// orEmpty 는 Python account.get(k, "") 처럼 키가 없을 때만 "" 다.
func orEmpty(m map[string]any, k string) any {
	if v, ok := m[k]; ok {
		return v
	}
	return ""
}

type accountInfo struct {
	num                     int
	email, orgName, orgUUID string
	alias                   string
	active                  bool
	creds                   string
	unreadable, degraded    bool
	disabled                bool
	uuid                    string // sequence.json 에 저장된 계정 uuid. 소유권 확인에 쓴다
}

func (e Env) accountsInfo(s *sequence) []accountInfo {
	email, org, logged := e.identity()
	active := ""
	if logged {
		active = s.slotOf(email, org)
	}
	var out []accountInfo
	for _, n := range s.Sequence {
		k := strconv.Itoa(n)
		a := s.Accounts[k]
		ai := accountInfo{num: n, email: "unknown", orgName: str(a, "organizationName"), orgUUID: str(a, "organizationUuid"),
			alias: str(a, "alias"), active: k == active, disabled: a["disabled"] == true, uuid: str(a, "uuid")}
		if v, ok := a["email"].(string); ok {
			ai.email = v
		}
		if ai.active {
			ai.creds, ai.unreadable, ai.degraded = e.readActive()
		} else {
			ai.creds, ai.unreadable = e.readBackup(k, ai.email)
		}
		out = append(out, ai)
	}
	return out
}

// statuses 는 네트워크 없이 정할 수 있는 상태만 정한다(api_key, no_credentials, keychain_unavailable,
// relogin_required, token_expired). 나머지는 캐시 판정을 따른다.
func (e Env) statuses(infos []accountInfo) (map[string]string, map[string]entry) {
	ids := map[string][2]string{}
	for _, i := range infos {
		ids[strconv.Itoa(i.num)] = [2]string{i.email, i.orgUUID}
	}
	entries := e.usageEntries(ids)
	st := map[string]string{}
	for _, i := range infos {
		k := strconv.Itoa(i.num)
		switch {
		case looksLikeAPIKey(i.creds):
			st[k] = "api_key"
		case i.creds == "" || accessToken(i.creds) == "":
			if i.unreadable {
				st[k] = "keychain_unavailable"
			} else {
				st[k] = "no_credentials"
			}
		case e.tokenDead(entries[k], i):
			st[k] = "relogin_required"
		case i.active && tokenExpired(oauthData(i.creds)):
			st[k] = "token_expired"
		}
	}
	return st, entries
}

// collectAll 은 claude-swap 의 수집 한 번과 같다: 상태 판정 → 풀린 격리 정리 → 받을 차례인 계정 받기 → 다시 읽기.
func (e Env) collectAll(infos []accountInfo) (map[string]string, map[string]entry) {
	st, entries := e.statuses(infos)
	ids := map[string][2]string{}
	for _, i := range infos {
		ids[strconv.Itoa(i.num)] = [2]string{i.email, i.orgUUID}
	}
	healed := false
	for n, en := range entries {
		// strike 는 있는데 지금 저장된 세대를 가리키지 않는다(relogin_required 나 정적 센티널이 아니면 그 경우다)
		if (st[n] == "" || st[n] == "token_expired") && en.strikes >= authDeadStrikes {
			e.clearDeadToken(n, ids[n])
			healed = true
		}
	}
	if healed {
		entries = e.usageEntries(ids)
	}
	e.collect(infos, st, ids, entries)
	entries = e.usageEntries(ids)
	// 이번에 받은 결과가 invalid_grant 였으면 다음 수집까지 기다리지 않고 바로 "다시 로그인 필요"로 보인다
	for _, i := range infos {
		n := strconv.Itoa(i.num)
		if st[n] == "" && e.tokenDead(entries[n], i) {
			st[n] = "relogin_required"
		}
	}
	return st, entries
}

// tokenDead: 활성 계정은 라이브와 백업 두 곳 중 하나라도 죽은 세대를 들고 있으면 죽은 것으로 본다.
func (e Env) tokenDead(en entry, i accountInfo) bool {
	if en.tokenDead(fingerprint(i.creds)) {
		return i.active || !e.profileAlive(en, i)
	}
	if !i.active {
		return false
	}
	backup, unreadable := e.readBackup(strconv.Itoa(i.num), i.email)
	if unreadable {
		return en.tokenDead("")
	}
	return backup != "" && en.tokenDead(fingerprint(backup))
}

// profileAlive: 쉬는 계정의 세션 프로필이 격리된 세대가 아닌 살아 있는 토큰을 들고 있는가. 그러면 fetchOne 은 그 토큰으로
// 읽기만 하고 죽은 백업은 보내지 않는다. 0.26.0 은 백업만 봐서 세션으로만 쓰는 계정이 격리된 채 굳었다(2026-10-21).
func (e Env) profileAlive(en entry, i accountInfo) bool {
	dir := e.sessionDir(strconv.Itoa(i.num), i.email)
	prof := sessionCreds(dir)
	o := oauthData(prof)
	return o != nil && accessToken(prof) != "" && !tokenExpired(o) && fingerprint(prof) != en.struckFP &&
		!sessionDrifted(dir, i.email, i.orgUUID)
}

func isoZ(ts float64) string {
	return time.Unix(int64(math.Floor(ts)), 0).UTC().Format("2006-01-02T15:04:05Z")
}

// shortcut: countdown/clock/pace 같은 표시용 필드는 내지 않는다. cc-baton 은 쓰지 않는다. 사람이 읽는 list 를 만들 때 추가.
func usageJSON(u map[string]any) map[string]any {
	win := func(w map[string]any) map[string]any {
		out := map[string]any{"pct": w["pct"]}
		if v, ok := w["resets_at"]; ok {
			out["resetsAt"] = v
		}
		return out
	}
	out := map[string]any{}
	if w, ok := u["five_hour"].(map[string]any); ok {
		out["fiveHour"] = win(w)
	}
	if w, ok := u["seven_day"].(map[string]any); ok {
		out["sevenDay"] = win(w)
	}
	if sp, ok := u["spend"].(map[string]any); ok {
		o := map[string]any{"used": sp["used"], "limit": sp["limit"], "pct": sp["pct"], "currency": sp["currency"]}
		if v, ok := sp["resets_at"]; ok {
			o["resetsAt"] = v
		}
		out["spend"] = o
	}
	if sc, ok := u["scoped"].([]any); ok {
		ws := []any{}
		for _, x := range sc {
			w, _ := x.(map[string]any)
			o := win(w)
			o["name"] = w["name"]
			ws = append(ws, o)
		}
		out["scoped"] = ws
	}
	return out
}

// usageFields 는 행에 usageStatus/usage 와 신선도 필드를 붙인다.
func usageFields(row map[string]any, status string, en entry) {
	var usage map[string]any
	if status == "" {
		if v := en.decisionValue(); v != nil {
			status, usage = "ok", usageJSON(v)
		} else {
			status = "unavailable"
		}
	}
	row["usageStatus"] = status
	if usage == nil {
		row["usage"] = nil
		if en.lastGood != nil && en.fetchedAt != nil {
			row["lastGoodUsage"] = usageJSON(en.lastGood)
			row["lastGoodFetchedAt"] = isoZ(*en.fetchedAt)
			row["lastGoodAgeSeconds"] = math.Round(*en.ageS*10) / 10
		}
		return
	}
	row["usage"] = usage
	row["usageFetchedAt"] = isoZ(*en.fetchedAt)
	row["usageAgeSeconds"] = math.Round(*en.ageS*10) / 10
}

// shortcut: duplicateAccountWarnings / lockstepUsageWarnings / unclaimedCredentials 는 아직 없다. 쓰기 단계에서 stash 를 다룰 때 추가.
func (e Env) ListJSON() (map[string]any, error) {
	s, err := e.loadSequence()
	if err != nil {
		return nil, err
	}
	payload := map[string]any{"schemaVersion": jsonSchema, "activeAccountNumber": nil, "accounts": []any{}}
	if s == nil {
		return payload, nil
	}
	infos := e.accountsInfo(s)
	st, entries := e.collectAll(infos)
	accounts := []any{}
	for _, i := range infos {
		k := strconv.Itoa(i.num)
		if i.active {
			payload["activeAccountNumber"] = i.num
		}
		row := map[string]any{"number": i.num, "email": i.email, "organizationName": i.orgName,
			"organizationUuid": i.orgUUID, "isOrganization": i.orgUUID != "", "active": i.active}
		usageFields(row, st[k], entries[k])
		if i.alias != "" {
			row["alias"] = i.alias
		}
		if i.disabled {
			row["disabled"] = true
		}
		accounts = append(accounts, row)
	}
	payload["accounts"] = accounts
	return payload, nil
}

func (e Env) StatusJSON() (map[string]any, error) {
	email, org, ok := e.identity()
	if !ok {
		return map[string]any{"schemaVersion": jsonSchema, "active": nil}, nil
	}
	s, err := e.loadSequence()
	if err != nil {
		return nil, err
	}
	unmanaged := map[string]any{"schemaVersion": jsonSchema, "active": map[string]any{"email": email, "managed": false}}
	if s == nil || len(s.Accounts) == 0 {
		return unmanaged, nil
	}
	k := s.slotOf(email, org)
	if k == "" {
		return unmanaged, nil
	}
	a := s.Accounts[k]
	n, _ := strconv.Atoi(k)
	i := accountInfo{num: n, email: email, orgUUID: str(a, "organizationUuid"), active: true, uuid: str(a, "uuid")}
	i.creds, i.unreadable, i.degraded = e.readActive()
	st, entries := e.collectAll([]accountInfo{i})
	active := map[string]any{"number": n, "email": email, "organizationName": str(a, "organizationName"),
		"organizationUuid": i.orgUUID, "isOrganization": i.orgUUID != "", "managed": true}
	usageFields(active, st[k], entries[k])
	if al := str(a, "alias"); al != "" {
		active["alias"] = al
	}
	return map[string]any{"schemaVersion": jsonSchema, "active": active, "totalManagedAccounts": len(s.Accounts)}, nil
}
