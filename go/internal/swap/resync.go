package swap

import (
	"encoding/json"
	"fmt"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

var profileURL = "https://api.anthropic.com/api/oauth/profile" // 테스트가 가짜 서버로 바꾼다

type identity struct{ uuid, email, org *string }

// fetchProfile 은 토큰 주인이 누구인지 묻는다. account.uuid 가 없으면 모르는 것으로 본다(nil).
// 잠금을 잡은 채 부르면 안 된다(잠금 안에서 네트워크 금지).
func (e Env) fetchProfile(token string) *identity {
	req, _ := http.NewRequest("GET", profileURL, nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", userAgent)
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode == 401 {
		e.log("WARNING", "OAuth profile returned 401 while resolving credential ownership; proceeding without identity")
	}
	if resp.StatusCode != 200 {
		return nil
	}
	var d struct {
		Account      map[string]any `json:"account"`
		Organization map[string]any `json:"organization"`
	}
	if json.NewDecoder(resp.Body).Decode(&d) != nil {
		return nil
	}
	u, _ := d.Account["uuid"].(string)
	if strings.TrimSpace(u) == "" {
		return nil
	}
	u = strings.TrimSpace(u)
	id := identity{uuid: &u}
	if v, ok := d.Account["email"].(string); ok {
		id.email = &v
	}
	if v, ok := d.Organization["uuid"].(string); ok {
		id.org = &v
	}
	return &id
}

// ownedBy 는 확인된 신원이 이 슬롯 계정인지: 1 = 맞음, 0 = 다른 계정, -1 = 판단 불가(기억하지 않는다).
// uuid 가 먼저다. 이메일은 다른 조직 계정과 겹칠 수 있다(switcher._resolved_matches_slot_identity).
// shortcut: 슬롯에 uuid 가 없을 때 Python 은 확인된 uuid 를 sequence.json 에 채워 넣는다. 지금 계정은 다 uuid 가 있어 생략. add-token 계정에 쓰게 되면 추가.
func ownedBy(r *identity, i accountInfo) int {
	if *r.uuid != "" && i.uuid != "" {
		if *r.uuid == i.uuid && (r.org == nil || *r.org == "" || i.orgUUID == "" || *r.org == i.orgUUID) {
			return 1
		}
		return 0
	}
	if r.email != nil && *r.email != "" && *r.email == i.email {
		if r.org == nil {
			return -1
		}
		if *r.org == i.orgUUID {
			return 1
		}
		return 0
	}
	if r.email != nil && *r.email != "" && r.org != nil {
		return 0
	}
	return -1
}

// 소유권 판정은 같은 세대(fp)·같은 슬롯 신원에만 유효하다. CLI 는 매번 새 프로세스라 사실상 실행 한 번 동안만 기억한다.
var verdicts sync.Map

func lineageKey(i accountInfo, fp string) string {
	return strings.Join([]string{strconv.Itoa(i.num), i.email, i.orgUUID, i.uuid, fp}, "\x00")
}

func verdictOf(i accountInfo, fp string) (bool, bool) {
	v, ok := verdicts.Load(lineageKey(i, fp))
	if !ok {
		return false, false
	}
	return v.(bool), true
}

// resyncRotatedBackup: 기본 로그인 계정 토큰을 Claude Code 가 회전시켰는데 백업이 옛 세대에 머물러 있으면 맞춘다.
// 안 맞추면 나중에 백업의 이미 쓴 refresh token 을 보내 invalid_grant 로 계정이 격리된다(spec R5, switcher._resync_rotated_backup).
// 사용량 요청이 방금 이 토큰을 받아들였을 때만 부른다. 어떤 실패도 그냥 넘어간다(다음 수집에서 다시 본다).
func (e Env) resyncRotatedBackup(i accountInfo) {
	o := oauthData(i.creds)
	if str(o, "accessToken") == "" || str(o, "refreshToken") == "" {
		return // 토큰 쌍이 온전하지 않으면 백업을 덮지 않는다
	}
	n := strconv.Itoa(i.num)
	backup, _ := e.readBackup(n, i.email)
	fp := fingerprint(i.creds)
	if backup != "" && fingerprint(backup) == fp {
		return
	}
	v, known := verdictOf(i, fp)
	if known && !v {
		return
	}
	if !known {
		r := e.fetchProfile(str(o, "accessToken"))
		if r == nil {
			return
		}
		m := ownedBy(r, i)
		if m < 0 {
			return
		}
		verdicts.Store(lineageKey(i, fp), m == 1)
		if m == 0 {
			e.log("WARNING", fmt.Sprintf("Live credential resolves to a different account than Account-%s's identity; backup left untouched (foreign credential under a stale config).", n))
			return
		}
	}
	unlock, err := flock(filepath.Join(e.Backup(), ".lock"), 10*time.Second)
	if err != nil {
		return
	}
	defer unlock()
	unCC, err := e.claudeCredentialsLock()
	if err != nil {
		return
	}
	defer unCC()
	// 잠금 안에서 다시 확인: 그사이 /login 이나 다른 수집기가 상태를 바꿨을 수 있다
	if email, org, ok := e.identity(); !ok || email != i.email || org != i.orgUUID {
		return
	}
	live, _, degraded := e.readActive()
	lo := oauthData(live)
	if degraded || str(lo, "accessToken") == "" || str(lo, "refreshToken") == "" || fingerprint(live) != fp {
		return
	}
	if err := e.writeAccountCredentials(n, i.email, live); err != nil {
		e.log("WARNING", fmt.Sprintf("Backup resync for account %s failed: %v", n, err))
		return
	}
	e.log("INFO", fmt.Sprintf("Resynced account %s's backup to the rotated live credential (rotation completed outside a collect pass).", n))
}
