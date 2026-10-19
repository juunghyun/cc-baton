package swap

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/rand"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

var usageURL = "https://api.anthropic.com/api/oauth/usage" // 테스트가 가짜 서버로 바꾼다

// 사용량 예산이 User-Agent 종류별로 잡힐 수 있어서 claude-swap 과 같은 값을 쓴다(spec R9).
const userAgent = "claude-swap/1.0"

// getUsage 는 사용량을 한 번 받는다. err 는 claude-swap 과 같은 분류(http-NNN, timeout, network, bad-response).
func getUsage(token string) (map[string]any, string, *float64) {
	req, _ := http.NewRequest("GET", usageURL, nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("anthropic-beta", "oauth-2025-04-20")
	req.Header.Set("User-Agent", userAgent)
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		var ne net.Error
		if errors.As(err, &ne) && ne.Timeout() {
			return nil, "timeout", nil
		}
		return nil, "network", nil
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		var ra *float64
		if v, err := strconv.ParseFloat(strings.TrimSpace(resp.Header.Get("Retry-After")), 64); err == nil {
			v = math.Max(0, v)
			ra = &v
		}
		return nil, fmt.Sprintf("http-%d", resp.StatusCode), ra
	}
	var data map[string]any
	if json.NewDecoder(resp.Body).Decode(&data) != nil {
		return nil, "bad-response", nil
	}
	return buildUsage(data), "", nil
}

// buildUsage 는 API 응답을 usage.json 의 lastGood 모양으로 줄인다(oauth.build_usage_result).
// shortcut: 표시용 countdown/clock 문자열은 저장하지 않는다. resets_at 이 있으면 Python 도 그걸로 다시 계산한다. 사람이 읽는 list 를 만들 때 추가.
func buildUsage(data map[string]any) map[string]any {
	out := map[string]any{}
	for _, k := range []string{"five_hour", "seven_day"} {
		if w, ok := data[k].(map[string]any); ok && len(w) > 0 {
			en := map[string]any{"pct": w["utilization"]}
			if r, _ := w["resets_at"].(string); r != "" {
				en["resets_at"] = r
			}
			out[k] = en
		}
	}
	if eu, ok := data["extra_usage"].(map[string]any); ok && eu["is_enabled"] == true {
		used, ok1 := eu["used_credits"].(float64)
		limit, ok2 := eu["monthly_limit"].(float64)
		pct, ok3 := eu["utilization"].(float64)
		if ok1 && ok2 && ok3 {
			cur, ok := eu["currency"].(string)
			if !ok {
				cur = "USD"
			}
			sp := map[string]any{"used": used / 100, "limit": limit / 100, "pct": pct, "currency": cur}
			if r, _ := eu["resets_at"].(string); r != "" {
				sp["resets_at"] = r
			}
			out["spend"] = sp
		}
	}
	if lims, ok := data["limits"].([]any); ok {
		var scoped []any
		for _, l := range lims {
			lim, _ := l.(map[string]any)
			scope, _ := lim["scope"].(map[string]any)
			model, _ := scope["model"].(map[string]any)
			name, _ := model["display_name"].(string)
			pct, ok := lim["percent"].(float64)
			if name == "" || !ok {
				continue
			}
			en := map[string]any{"name": name, "pct": pct}
			if r, _ := lim["resets_at"].(string); r != "" {
				en["resets_at"] = r
			}
			scoped = append(scoped, en)
		}
		if len(scoped) > 0 {
			out["scoped"] = scoped
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// windows 는 5시간·7일 창의 (pct, resets_at). 모델별 창은 autoswitch.model 을 안 쓰므로 빠진다.
func windows(u map[string]any) (pcts []float64, resets []string) {
	for _, k := range []string{"five_hour", "seven_day"} {
		w, _ := u[k].(map[string]any)
		if p, ok := w["pct"].(float64); ok {
			pcts = append(pcts, p)
			r, _ := w["resets_at"].(string)
			resets = append(resets, r)
		}
	}
	return
}

func bindingPct(u map[string]any) *float64 {
	pcts, _ := windows(u)
	if len(pcts) == 0 {
		return nil
	}
	m := pcts[0]
	for _, p := range pcts {
		m = math.Max(m, p)
	}
	return &m
}

// recent429 는 429 백오프가 풀린 시점부터 1시간 동안 참이다(usage_store.UsageEntry.recent_429).
func (en entry) recent429(now float64) bool {
	if en.last429At == nil {
		return false
	}
	anchor := *en.last429At
	if en.lastError == "http-429" && en.backoffUntil != nil && *en.backoffUntil > anchor {
		anchor = *en.backoffUntil
	}
	return now < anchor+recent429WindowS
}

// planAfterFetch 는 성공한 뒤 다음 호출 시각과 간격을 정한다(poll_policy.plan_after_fetch).
func planAfterFetch(prev entry, newUsage map[string]any, active bool, now float64, rnd func() float64) [2]float64 {
	def, ceil := candidateDefaultS, candidateMaxS
	if active {
		def, ceil = minIntervalS, activeMaxS
	}
	base := def
	if prev.pollIntervalS != nil && *prev.pollIntervalS != 0 {
		base = *prev.pollIntervalS
	}
	pp, np := bindingPct(prev.lastGood), bindingPct(newUsage)
	moving, interval := false, def
	if pp != nil && np != nil {
		if math.Abs(*np-*pp) >= 1 {
			moving, interval = true, math.Max(minIntervalS, base/2)
		} else {
			interval = math.Min(ceil, math.Max(minIntervalS, base*1.5))
		}
	}
	r429 := prev.recent429(now)
	if active && moving && !r429 && np != nil && *np >= threshold-15 {
		interval = urgentIntervalS
	}
	if r429 {
		interval = math.Min(post429MaxS, math.Max(interval, math.Max(base*1.5, post429MinS)))
	}
	exhausted := np != nil && 100-*np <= 0
	if exhausted {
		interval = math.Max(interval, exhaustedS)
	}
	next := now + interval*(1+jitterFrac*(2*rnd()-1))
	_, resets := windows(newUsage)
	pcts, _ := windows(newUsage)
	var cap *float64
	for i, r := range resets {
		t, ok := parseReset(r)
		if !ok {
			continue
		}
		if exhausted { // 한도에 걸린 창들 중 가장 늦게 풀리는 시각
			if pcts[i] >= 100 && t > now && (cap == nil || t > *cap) {
				cap = &t
			}
		} else if t > now && (cap == nil || t < *cap) { // 다음으로 리셋되는 창
			cap = &t
		}
	}
	if cap != nil {
		next = math.Min(next, *cap+resetSlackS)
	}
	return [2]float64{next, interval}
}

// sessionDir 는 계정별 세션 프로필 폴더. 이 문자열 그대로 CLAUDE_CONFIG_DIR 로 넘어가고 키체인 이름도 이걸로 만든다.
// shortcut: Python 은 NFC 정규화를 먼저 한다. 표준 라이브러리에 없어서 생략했다. ASCII 가 아닌 글자는 어차피 '_' 가 되는데,
// 분해형 한글처럼 글자 수가 달라지는 경우만 다르다. 그런 이메일·홈 경로를 쓰게 되면 x/text/unicode/norm 추가.
func (e Env) sessionDir(num, email string) string {
	var b strings.Builder
	for _, r := range email {
		if r < 128 && (r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || strings.ContainsRune("._-", r)) {
			b.WriteRune(r)
		} else {
			b.WriteByte('_')
		}
	}
	return filepath.Join(e.Backup(), "sessions", num+"-"+b.String())
}

// sessionService 는 Claude Code 가 CLAUDE_CONFIG_DIR 값(가공 없이 NFC 만)으로 만드는 키체인 이름이다(spec R3).
func sessionService(dir string) string {
	h := sha256.Sum256([]byte(dir)) // shortcut: NFC 생략(sessionDir 주석 참고)
	return liveService + "-" + hex.EncodeToString(h[:])[:8]
}

// sessionCreds 는 프로필의 최신 토큰. 키체인이 먼저, 없으면 평문 seed. 키체인이 잠겼으면 seed 로 넘어간다.
func sessionCreds(dir string) string {
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return ""
	}
	if v, err := kcGet(sessionService(dir), kcAccount()); err == nil && v != "" {
		return v
	}
	b, _ := os.ReadFile(filepath.Join(dir, ".credentials.json"))
	return string(b)
}

// sessionDrifted: 프로필 안에서 /login 으로 다른 계정이 됐는지. 신원을 못 읽으면 drift 아님.
func sessionDrifted(dir, email, org string) bool {
	cfg := readJSONMap(filepath.Join(dir, ".claude.json"))
	oa, _ := cfg["oauthAccount"].(map[string]any)
	pe, _ := oa["emailAddress"].(string)
	if pe == "" {
		return false
	}
	if pe != email {
		return true
	}
	po, _ := oa["organizationUuid"].(string)
	return po != "" && org != "" && po != org
}

// liveSessions 는 프로필에서 돌고 있는 claude 수와 읽지 못한 기록 수. 파괴적인 작업은 둘 다 0일 때만 한다.
func liveSessions(dir string) (live, unreadable int) {
	files, _ := filepath.Glob(filepath.Join(dir, "sessions", "*.json"))
	for _, f := range files {
		var rec map[string]any
		b, err := os.ReadFile(f)
		if err != nil || json.Unmarshal(b, &rec) != nil {
			unreadable++
			continue
		}
		pid, ok := rec["pid"].(float64)
		if !ok || pid != math.Trunc(pid) {
			unreadable++
			continue
		}
		if pid > 1 && pidAlive(int(pid)) {
			live++
		}
	}
	return
}

func pidAlive(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

// fetchOne 은 한 계정의 사용량을 받는다. 토큰은 갱신하지 않는다.
// shortcut: 만료·401 이면 갱신 대신 미룬다(기본 로그인은 Claude Code 몫, 쉬는 계정은 3f 에서 consume gate 로 추가).
func (e Env) fetchOne(i accountInfo) fetchRecord {
	use := func(creds string) fetchRecord {
		u, kind, ra := getUsage(accessToken(creds))
		return fetchRecord{usage: u, err: kind, retryAfter: ra}
	}
	if i.active {
		if tokenExpired(oauthData(i.creds)) {
			return fetchRecord{sentinel: "token expired"}
		}
		return use(i.creds)
	}
	n := strconv.Itoa(i.num)
	dir := e.sessionDir(n, i.email)
	live, _ := liveSessions(dir)
	prof := sessionCreds(dir)
	if prof != "" && sessionDrifted(dir, i.email, i.orgUUID) {
		prof, live = "", 0
	}
	if o := oauthData(prof); o != nil && accessToken(prof) != "" {
		if !tokenExpired(o) {
			return use(prof)
		}
		if live > 0 {
			return fetchRecord{sentinel: "token expired"}
		}
	}
	if o := oauthData(i.creds); live == 0 && o["refreshToken"] != nil && o["refreshToken"] != "" && tokenExpired(o) {
		return fetchRecord{sentinel: "token expired"}
	}
	return use(i.creds)
}

// collect 는 받을 차례인 계정만 임대를 걸고, 0.25초씩 띄워 동시에 받고, 결과를 기록한다(_collect_usage_entries).
func (e Env) collect(infos []accountInfo, st map[string]string, ids map[string][2]string, pre map[string]entry) {
	var want []string
	byNum := map[string]accountInfo{}
	for _, i := range infos {
		n := strconv.Itoa(i.num)
		byNum[n] = i
		if st[n] == "" {
			want = append(want, n)
		}
	}
	claims := e.reserve(want, ids)
	if len(claims) == 0 {
		return
	}
	recs := map[string]fetchRecord{}
	var mu sync.Mutex
	var wg sync.WaitGroup
	idx := 0
	for _, n := range want { // 순서를 고정해야 지연 간격이 계정 순서를 따른다
		if _, ok := claims[n]; !ok {
			continue
		}
		wg.Add(1)
		go func(n string, delay time.Duration) {
			defer wg.Done()
			time.Sleep(delay)
			r := e.fetchOne(byNum[n])
			mu.Lock()
			recs[n] = r
			mu.Unlock()
		}(n, time.Duration(idx)*250*time.Millisecond)
		idx++
	}
	wg.Wait()
	plans := map[string][2]float64{}
	for n, r := range recs {
		if r.err != "" {
			e.logUsageFailure(n, r)
		} else if r.sentinel == "" {
			plans[n] = planAfterFetch(pre[n], r.usage, byNum[n].active, nowS(), rand.Float64)
		}
		if r.sentinel != "" {
			st[n] = "token_expired" // fetchOne 이 내는 센티널은 이것 하나다
		}
	}
	e.record(recs, ids, claims, plans)
}

// logUsageFailure 는 claude-swap.log 에 같은 형식으로 남긴다. 이메일은 넣지 않는다(이슈에 붙여 넣는 줄이라).
func (e Env) logUsageFailure(n string, r fetchRecord) {
	cause := r.err
	if r.retryAfter != nil {
		cause += fmt.Sprintf(", retry-after %.0fs", *r.retryAfter)
	}
	if r.err == "http-429" {
		cause += " (usage-endpoint budget reached; backing off)"
	}
	e.log("WARNING", fmt.Sprintf("Usage fetch failed for account %s: %s", n, cause))
}

// log 는 Python logging 형식("2026-10-14 10:03:29,521 - INFO - msg")으로 이어 쓰고 [go] 를 붙인다. 실패해도 무시한다.
func (e Env) log(level, msg string) {
	f, err := os.OpenFile(filepath.Join(e.Backup(), "claude-swap.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	t := time.Now()
	fmt.Fprintf(f, "%s,%03d - %s - [go] %s\n", t.Format("2006-01-02 15:04:05"), t.Nanosecond()/1e6, level, msg)
}
