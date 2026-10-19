package swap

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

// claude-swap usage_store.py / poll_policy.py 상수. Python cswap 과 같은 표를 같이 쓰므로 그대로 둔다.
const (
	serveTTLS         = 180.0
	claimTTLS         = 90.0
	backoffBaseS      = 30.0
	backoffCapS       = 600.0
	edgeBackoffS      = 300.0
	retryAfterMarginS = 900.0
	retryAfterFloorS  = 4500.0
	minIntervalS      = 180.0
	urgentIntervalS   = 60.0
	activeMaxS        = 300.0
	candidateDefaultS = 300.0
	candidateMaxS     = 600.0
	exhaustedS        = 600.0
	jitterFrac        = 0.1
	post429MinS       = 360.0
	recent429WindowS  = 3600.0
	post429MaxS       = 1800.0
	resetSlackS       = 60.0
	// shortcut: settings.json 의 autoswitch.threshold 를 읽지 않고 기본값(90)을 쓴다. 설정 파일을 쓰게 되면 읽기 추가.
	threshold = 90.0
)

var errLockTimeout = errors.New("lock timeout")

// flock 은 Python FileLock 과 같은 방식(fcntl.flock LOCK_EX|LOCK_NB 를 0.1초마다, 기본 10초)이라 서로 배타적이다.
func flock(path string, timeout time.Duration) (func(), error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(timeout)
	for {
		if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err == nil {
			return func() { syscall.Flock(int(f.Fd()), syscall.LOCK_UN); f.Close() }, nil
		}
		if time.Now().After(deadline) {
			f.Close()
			return nil, errLockTimeout
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func nowS() float64 { return float64(time.Now().UnixNano()) / 1e9 }

func newClaimID() string {
	b := make([]byte, 16)
	rand.Read(b)
	return hex.EncodeToString(b)
}

func (e Env) usagePath() string { return filepath.Join(e.Backup(), "cache", "usage.json") }

// mutateUsage 는 cache/.usage.lock 아래에서 행을 읽고 고쳐 쓴다. fn 이 false 를 주면 쓰지 않는다.
// Go 가 못 읽는 파일(Python 이 NaN 을 썼다든가)은 빈 표로 덮어쓰지 않고 그대로 둔다(spec R11).
func (e Env) mutateUsage(fn func(rows map[string]any) bool) error {
	dir := filepath.Dir(e.usagePath())
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	unlock, err := flock(filepath.Join(dir, ".usage.lock"), 10*time.Second)
	if err != nil {
		return err
	}
	defer unlock()
	rows := map[string]any{}
	b, err := os.ReadFile(e.usagePath())
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	if err == nil {
		var raw map[string]any
		if json.Unmarshal(b, &raw) != nil {
			return fmt.Errorf("usage.json unreadable by Go; left untouched")
		}
		if v, _ := raw["schemaVersion"].(float64); v == usageSchema {
			if r, ok := raw["accounts"].(map[string]any); ok {
				rows = r
			}
		}
	}
	if !fn(rows) {
		return nil
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		return err
	}
	return writeJSONAtomic(e.usagePath(), map[string]any{"schemaVersion": usageSchema, "accounts": rows})
}

// matchRow 는 그 슬롯 행이 같은 계정(email, org)의 것일 때만 돌려준다.
func matchRow(rows map[string]any, n string, id [2]string) map[string]any {
	row, _ := rows[n].(map[string]any)
	org, ok := row["organizationUuid"]
	if !ok {
		org = ""
	}
	if row != nil && row["email"] == id[0] && org == id[1] {
		return row
	}
	return nil
}

// rowFor 는 신원이 맞는 행을, 아니면 새 행을 돌려준다(슬롯을 재사용해도 이전 계정 값이 섞이지 않게).
func rowFor(rows map[string]any, n string, id [2]string) (map[string]any, bool) {
	if row := matchRow(rows, n, id); row != nil {
		return row, true
	}
	row := map[string]any{"email": id[0], "organizationUuid": id[1]}
	rows[n] = row
	return row, false
}

// eligible 은 list/status 기준(respect_plans, repair_overslept)이다. usage_store._row_eligible.
func eligible(row map[string]any, now float64) bool {
	if s := num(row["authDeadStrikes"]); s != nil && int(*s) >= authDeadStrikes {
		return false
	}
	if b := num(row["backoffUntil"]); b != nil && now < *b {
		return false
	}
	if liveClaim(num(row["claimUntil"]), num(row["lastAttemptAt"]), now) {
		return false
	}
	f := num(row["fetchedAt"])
	stale := f == nil || now-*f > serveTTLS
	next := num(row["nextPollAt"])
	due := next != nil && now >= *next
	overslept := false
	if next != nil {
		iv := exhaustedS
		if p := num(row["pollIntervalS"]); p != nil && *p > iv {
			iv = *p
		}
		overslept = *next > now+iv*(1+jitterFrac)+resetSlackS
	}
	return stale && (due || next == nil || overslept)
}

// reserve 는 받을 차례인 계정에 잠금 아래에서 90초 임대(claim)를 건다. 두 수집기가 같은 계정을 동시에 받지 않게 한다.
func (e Env) reserve(nums []string, ids map[string][2]string) map[string]string {
	won := map[string]string{}
	if len(nums) == 0 {
		return won
	}
	err := e.mutateUsage(func(rows map[string]any) bool {
		now := nowS()
		for _, n := range nums {
			row, matched := rowFor(rows, n, ids[n])
			if matched && !eligible(row, now) {
				continue
			}
			id := newClaimID()
			row["lastAttemptAt"], row["claimId"], row["claimUntil"] = now, id, now+claimTTLS
			won[n] = id
		}
		return len(won) > 0
	})
	if err != nil {
		return map[string]string{} // 잠금이나 파일을 못 다루면 이번엔 받지 않는다
	}
	return won
}

type fetchRecord struct {
	usage      map[string]any
	err        string // "" = 성공
	retryAfter *float64
	sentinel   string // "token expired" 등. 저장하지 않고 임대만 푼다
	struckFP   string // invalid_grant 일 때 실제로 보낸 세대. 격리는 이 세대에만 묶인다
}

// record 는 임대 id 가 그대로인 행에만 결과를 반영한다. 그사이 다른 수집기가 가져갔으면 무시한다.
func (e Env) record(recs map[string]fetchRecord, ids map[string][2]string, claims map[string]string, plans map[string][2]float64) {
	if len(recs) == 0 {
		return
	}
	e.mutateUsage(func(rows map[string]any) bool {
		now := nowS()
		changed := false
		for n, rec := range recs {
			row := matchRow(rows, n, ids[n])
			if row == nil || claims[n] == "" || row["claimId"] != claims[n] {
				continue
			}
			changed = true
			row["claimId"], row["claimUntil"] = nil, 0.0
			if rec.sentinel != "" {
				continue
			}
			row["lastAttemptAt"] = now
			if rec.err == "" {
				row["lastGood"], row["fetchedAt"] = rec.usage, now
				if p, ok := plans[n]; ok {
					row["nextPollAt"], row["pollIntervalS"] = p[0], p[1]
				}
				row["consecutiveFailures"], row["lastError"], row["backoffUntil"], row["authDeadStrikes"] = 0, nil, nil, 0
				continue
			}
			fails := 1
			if f := num(row["consecutiveFailures"]); f != nil {
				fails = int(*f) + 1
			}
			row["consecutiveFailures"], row["lastError"] = fails, rec.err
			if rec.err == "http-429" {
				row["last429At"] = now
			}
			row["backoffUntil"] = now + failureBackoff(fails, rec.retryAfter, rec.err == "http-429")
			if rec.err == "invalid_grant" || rec.err == "no_refresh_token" { // 이 둘만 격리 사유다. 429·타임아웃은 근거가 못 된다
				strikes := 1
				if s := num(row["authDeadStrikes"]); s != nil {
					strikes = int(*s) + 1
				}
				row["authDeadStrikes"] = strikes
				if rec.struckFP != "" {
					row["struckFingerprint"] = rec.struckFP
				} else {
					row["struckFingerprint"] = nil
				}
			}
		}
		return changed
	})
}

// failureBackoff: 30초부터 두 배씩 600초까지. 서버가 Retry-After 를 주면 그걸 존중하되 상한이 있다.
func failureBackoff(fails int, retryAfter *float64, rateLimited bool) float64 {
	shift := fails - 1
	if shift < 0 {
		shift = 0
	}
	if shift > 32 {
		shift = 32
	}
	computed := math.Min(backoffBaseS*math.Pow(2, float64(shift)), backoffCapS)
	if retryAfter == nil {
		return computed
	}
	if *retryAfter == 0 {
		if !rateLimited {
			return computed
		}
		return math.Min(math.Max(computed, edgeBackoffS), backoffCapS)
	}
	asked := *retryAfter
	if asked > backoffCapS && rateLimited {
		asked += retryAfterMarginS
	}
	bound := trustMaxAgeS
	if rateLimited {
		bound = retryAfterFloorS
	}
	return math.Max(math.Min(asked, bound), computed)
}

// clearDeadToken 은 격리 사유가 사라진(저장된 토큰 세대가 바뀐) 행의 strike 를 지운다.
func (e Env) clearDeadToken(n string, id [2]string) {
	e.mutateUsage(func(rows map[string]any) bool {
		row, _ := rowFor(rows, n, id)
		row["claimId"], row["claimUntil"], row["authDeadStrikes"], row["struckFingerprint"] = nil, 0.0, 0, nil
		row["consecutiveFailures"], row["lastError"], row["backoffUntil"] = 0, nil, nil
		return true
	})
}
