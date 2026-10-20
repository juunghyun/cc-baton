package swap

import (
	"math"
	"path/filepath"
	"time"
)

// claude-swap usage_store.py 의 판정 상수. 바꾸면 Python cswap 과 같은 캐시를 다르게 읽는다.
const (
	staleOKS         = 300.0
	trustMaxAgeS     = 3600.0
	rateLimitMaxAgeS = 7200.0
	legacyClaimTTLS  = 10.0
	authDeadStrikes  = 1
	usageSchema      = 2
)

// entry 는 usage.json 한 행을 판정용으로 읽은 것. 행이 없거나 다른 계정 것이면 빈 값.
type entry struct {
	lastGood      map[string]any
	fetchedAt     *float64
	ageS          *float64
	strikes       int
	struckFP      string
	trustExtended bool
	claimUntil    *float64
	lastAttemptAt *float64
	// 호출 간격 계획(plan)에 쓰는 값
	pollIntervalS, last429At, backoffUntil *float64
	lastError                              string
}

func num(v any) *float64 {
	f, ok := v.(float64)
	if !ok {
		return nil
	}
	return &f
}

func liveClaim(claimUntil, lastAttempt *float64, now float64) bool {
	if claimUntil != nil {
		return now < *claimUntil
	}
	return lastAttempt != nil && now-*lastAttempt < legacyClaimTTLS
}

// usageEntries 는 계정 번호 → (email, org) 신원으로 행을 고른다. 읽기만 한다.
func (e Env) usageEntries(ids map[string][2]string) map[string]entry {
	now := float64(time.Now().UnixNano()) / 1e9
	raw := readJSONMap(filepath.Join(e.Backup(), "cache", "usage.json"))
	rows := map[string]any{}
	if raw != nil {
		if v, _ := raw["schemaVersion"].(float64); v == usageSchema {
			rows, _ = raw["accounts"].(map[string]any)
		}
	}
	out := map[string]entry{}
	for n, id := range ids {
		row, _ := rows[n].(map[string]any)
		org, ok := row["organizationUuid"]
		if !ok {
			org = "" // Python row.get("organizationUuid", "") 과 같게: null 은 "" 와 다르다
		}
		if row == nil || row["email"] != id[0] || org != id[1] {
			out[n] = entry{}
			continue
		}
		en := entry{fetchedAt: num(row["fetchedAt"]), claimUntil: num(row["claimUntil"]), lastAttemptAt: num(row["lastAttemptAt"])}
		en.lastGood, _ = row["lastGood"].(map[string]any)
		if en.fetchedAt != nil {
			a := now - *en.fetchedAt
			en.ageS = &a
		}
		if s := num(row["authDeadStrikes"]); s != nil {
			en.strikes = int(*s)
		}
		en.struckFP, _ = row["struckFingerprint"].(string)
		en.pollIntervalS, en.last429At, en.backoffUntil = num(row["pollIntervalS"]), num(row["last429At"]), num(row["backoffUntil"])
		en.lastError, _ = row["lastError"].(string)
		var within bool
		if row["lastError"] == "http-429" {
			within = rateLimitedTrustOK(en.lastGood, en.ageS, now)
		} else {
			within = en.ageS != nil && *en.ageS <= trustMaxAgeS
		}
		fails := 0
		if f := num(row["consecutiveFailures"]); f != nil {
			fails = int(*f)
		}
		next := num(row["nextPollAt"])
		en.trustExtended = within && (fails > 0 || (next != nil && now < *next) || liveClaim(en.claimUntil, en.lastAttemptAt, now))
		out[n] = en
	}
	return out
}

// rateLimitedTrustOK: 429 로 멈춘 값은 가장 빠른 창 리셋 전까지만, 그리고 2시간 상한 안에서만 믿는다.
// shortcut: settings.json 의 autoswitch.model(모델별 창)은 보지 않는다. 그 설정을 쓰게 되면 추가.
func rateLimitedTrustOK(lastGood map[string]any, ageS *float64, now float64) bool {
	if ageS == nil {
		return false
	}
	bound := now + (rateLimitMaxAgeS - *ageS)
	for _, k := range []string{"five_hour", "seven_day"} {
		w, _ := lastGood[k].(map[string]any)
		if _, ok := w["pct"].(float64); !ok {
			continue
		}
		s, _ := w["resets_at"].(string)
		if t, ok := parseReset(s); ok {
			bound = math.Min(bound, t)
		}
	}
	return now < bound
}

func parseReset(s string) (float64, bool) {
	if s == "" {
		return 0, false
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return 0, false
	}
	return float64(t.UnixNano()) / 1e9, true
}

// tokenDead: strike 가 남아 있고, 그 strike 가 지금 저장된 세대를 가리킬 때만 죽은 토큰이다.
func (en entry) tokenDead(storedFP string) bool {
	if en.strikes < authDeadStrikes {
		return false
	}
	return storedFP == "" || en.struckFP == "" || storedFP == en.struckFP
}

// decisionValue 는 판단에 써도 될 만큼 신선한 lastGood, 아니면 nil.
func (en entry) decisionValue() map[string]any {
	if en.lastGood != nil && en.ageS != nil && (*en.ageS <= staleOKS || en.trustExtended) {
		return en.lastGood
	}
	return nil
}
