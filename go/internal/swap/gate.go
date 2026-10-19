package swap

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var tokenURL = "https://platform.claude.com/v1/oauth/token" // 테스트가 가짜 서버로 바꾼다

const oauthClientID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

// 보관본을 읽을 수 없으면 그 안에 다음 세대가 있을 수 있으니, 지금 백업을 보내지 않고 미룬다.
var errStashUnreadable = errors.New("stash unreadable")

// 잠금 대기 시간. Python FileLock 기본값과 같다. 테스트가 줄인다.
var (
	consumeLockTimeout = 10 * time.Second
	slotLockTimeout    = 10 * time.Second
)

// refreshOutcome: creds 는 새 토큰(실패면 ""). err 는 "" = 성공, invalid_grant·no_refresh_token(영구, 격리 사유),
// transient(다음에 다시), 또는 store-unmirrored·invalid_client·consume-busy·stash-unreadable.
type refreshOutcome struct {
	creds, err, consumedFP string
	stashed                bool
}

// refreshToken 은 refresh token 을 한 번 쓴다. 응답의 새 값만 바꾸고 나머지 필드는 그대로 둔다(oauth.try_refresh_oauth_credentials).
func refreshToken(creds string) refreshOutcome {
	dec := json.NewDecoder(strings.NewReader(creds))
	dec.UseNumber() // expiresAt 같은 숫자를 그대로 다시 쓰려고
	var data map[string]any
	if dec.Decode(&data) != nil || data == nil {
		return refreshOutcome{err: "transient"}
	}
	o, _ := data["claudeAiOauth"].(map[string]any)
	rt, _ := o["refreshToken"].(string)
	if rt == "" {
		return refreshOutcome{err: "no_refresh_token"}
	}
	body, _ := json.Marshal(map[string]string{"grant_type": "refresh_token", "refresh_token": rt, "client_id": oauthClientID})
	req, _ := http.NewRequest("POST", tokenURL, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", userAgent)
	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		return refreshOutcome{err: "transient"}
	}
	defer resp.Body.Close()
	var r map[string]any
	jerr := json.NewDecoder(resp.Body).Decode(&r)
	if resp.StatusCode != 200 {
		// 영구 실패는 400/401/403 + JSON 최상위 error 로만 판단한다. 상태 코드만 보면 살아 있는 계정을 격리한다(spec R7).
		if resp.StatusCode == 400 || resp.StatusCode == 401 || resp.StatusCode == 403 {
			if e, _ := r["error"].(string); e == "invalid_grant" || e == "invalid_client" {
				return refreshOutcome{err: e}
			}
		}
		return refreshOutcome{err: "transient"}
	}
	at, ok1 := r["access_token"].(string)
	exp, ok2 := r["expires_in"].(float64)
	if jerr != nil || !ok1 || !ok2 {
		return refreshOutcome{err: "transient"}
	}
	o["accessToken"] = at
	o["expiresAt"] = time.Now().UnixMilli() + int64(exp*1000)
	if nrt, _ := r["refresh_token"].(string); nrt != "" {
		o["refreshToken"] = nrt
	}
	if sc, _ := r["scope"].(string); sc != "" {
		o["scopes"] = strings.Fields(sc)
	}
	data["claudeAiOauth"] = o
	out, err := json.Marshal(data)
	if err != nil {
		return refreshOutcome{err: "transient"}
	}
	return refreshOutcome{creds: string(out)}
}

// consumeBackupGrant 는 쉬는 계정 백업의 refresh token 을 안전하게 쓴다(switcher.consume_backup_grant).
// refresh token 은 한 번 쓰면 끝이라, 같은 세대를 두 번 보내면 invalid_grant 로 계정이 격리된다(spec R1). 그래서:
//   - 계정별 consume 잠금으로 동시에 둘이 보내지 않는다
//   - 보내기 직전 잠금 안에서 다시 읽고, 보관된 다음 세대나 더 새 프로필 토큰이 있으면 그걸 쓴다
//   - 보낸 뒤 잠금 안에서 그사이 백업이 바뀌었는지 비교하고, 바뀌었거나 쓸 수 없으면 새 토큰을 보관한다(버리지 않는다)
func (e Env) consumeBackupGrant(num, email, snapshot string) refreshOutcome {
	if os.Getenv("CLAUDE_SECURESTORAGE_CONFIG_DIR") != "" {
		return refreshOutcome{err: "store-unmirrored"}
	}
	credDir := filepath.Join(e.Backup(), "credentials")
	os.MkdirAll(credDir, 0o700)
	unlock, err := flock(filepath.Join(credDir, ".consume-"+num+".lock"), consumeLockTimeout)
	if err != nil {
		return refreshOutcome{err: "consume-busy"}
	}
	defer unlock()

	input, consumed, early := e.prepareConsume(num, email)
	if early != nil {
		return *early
	}
	io := oauthData(input)
	snapAT := accessToken(snapshot)
	// 다시 읽어 보니 그새 누가 이미 갱신해 둔(아직 유효한) 토큰이면 보내지 않고 그걸 쓴다
	if at := str(io, "accessToken"); at != "" && snapAT != "" && at != snapAT && !tokenExpired(io) {
		return refreshOutcome{creds: input, consumedFP: consumed}
	}
	res := refreshToken(input)
	res.consumedFP = consumed
	if res.err != "" {
		return res
	}
	return e.persistSuccessor(num, email, res)
}

// prepareConsume 은 슬롯 잠금 안에서 보낼 토큰을 정한다. early 가 nil 이 아니면 보내지 말고 그 결과를 돌려준다.
func (e Env) prepareConsume(num, email string) (input, consumedFP string, early *refreshOutcome) {
	unlock, err := flock(filepath.Join(e.Backup(), ".lock"), slotLockTimeout)
	if err != nil {
		return "", "", &refreshOutcome{err: "transient"}
	}
	defer unlock()
	cur, unreadable := e.readBackup(num, email)
	if unreadable {
		return "", "", &refreshOutcome{err: "transient"} // 잠긴 키체인을 "백업 없음"으로 보고 옛 값을 보내지 않게
	}
	adopted, err := e.adoptStash(num, email, cur)
	if errors.Is(err, errStashUnreadable) {
		e.log("INFO", fmt.Sprintf("Account %s's stashed successor is unreadable; deferring the refresh.", num))
		return "", "", &refreshOutcome{err: "stash-unreadable"}
	}
	if err != nil {
		return "", "", &refreshOutcome{err: "transient"}
	}
	if adopted != "" {
		cur = adopted
	}
	if cur == "" {
		return "", "", &refreshOutcome{err: "transient"} // 슬롯이 지워졌다
	}
	input = cur
	// 프로필에서 claude 가 갱신해 둔 더 새 세대가 있으면 그게 진짜다. 조용한 프로필일 때만 본다(Python 보다 엄격: 못 읽는 기록도 "돌고 있음")
	dir := e.sessionDir(num, email)
	m, _ := e.loadSequenceRaw()
	org := str(accountsOf(m)[num], "organizationUuid")
	if exists(dir) && quiescent(dir) && !isStale(dir) && !sessionDrifted(dir, email, org) {
		if prof := sessionCreds(dir); prof != "" {
			po, io := oauthData(prof), oauthData(input)
			pe, _ := po["expiresAt"].(float64)
			ie, _ := io["expiresAt"].(float64)
			if str(po, "accessToken") != "" && str(po, "refreshToken") != "" && fingerprint(prof) != fingerprint(input) && pe > ie {
				if err := e.writeAccountCredentials(num, email, prof); err != nil {
					return "", "", &refreshOutcome{err: "transient"}
				}
				input = prof
			}
		}
	}
	return input, fingerprint(input), nil
}

// persistSuccessor 는 받은 새 토큰을 백업에 쓴다. 그사이 백업이 바뀌었거나 쓸 수 없으면 보관한다.
func (e Env) persistSuccessor(num, email string, res refreshOutcome) refreshOutcome {
	reason := ""
	stash := func(why, note string) {
		if err := e.stashSuccessor(num, res.creds, res.consumedFP, why); err != nil {
			reason = "consume-gate-unpersisted"
			e.log("ERROR", fmt.Sprintf("Account %s's consumed successor could not be persisted or stashed — it survives only for this pass. Fix the storage failure, then re-login and add the account again if it gets quarantined.", num))
			return
		}
		reason = why
		e.log("WARNING", fmt.Sprintf(note, num))
	}
	out := res.creds
	unlock, err := flock(filepath.Join(e.Backup(), ".lock"), slotLockTimeout)
	if err != nil {
		stash("consume-gate-persist-lock-failed", "Slot lock unavailable after consuming account %s's grant; successor stashed for the next pass.")
	} else {
		now, unreadable := e.readBackup(num, email)
		switch {
		case unreadable:
			stash("consume-gate-store-unreadable", "Account %s's stored credential was unreadable (keychain) after a refresh POST; successor stashed, nothing rewritten.")
		case now == "":
			stash("consume-gate-slot-removed", "Account %s's stored credential disappeared during a refresh POST; successor stashed, nothing rewritten.")
		case fingerprint(now) != res.consumedFP:
			stash("consume-gate-cas-conflict", "Backup lineage for account %s moved during a refresh POST; successor stashed, adopting the newer store credential.")
			out = now
		default:
			if err := e.writeAccountCredentials(num, email, res.creds); err != nil {
				stash("consume-gate-persist-failed", "Persist failed after consuming account %s's grant; successor stashed for the next pass.")
			}
		}
		unlock()
	}
	switch reason {
	case "consume-gate-persist-failed", "consume-gate-persist-lock-failed", "consume-gate-unpersisted", "consume-gate-store-unreadable":
		return refreshOutcome{creds: out, err: "transient", consumedFP: res.consumedFP, stashed: reason != "consume-gate-unpersisted"}
	}
	return refreshOutcome{creds: out, consumedFP: res.consumedFP}
}

func (e Env) manifestPath() string {
	return filepath.Join(e.Backup(), "credentials", ".unclaimed-manifest.json")
}

func (e Env) stashEntryPath(id string) string {
	return filepath.Join(e.Backup(), "credentials", ".unclaimed-"+id+".enc")
}

// readManifestEx: verdict 는 ok / unreadable(읽기 실패) / corrupt(내용이 깨짐).
func (e Env) readManifestEx() (map[string]any, string) {
	b, err := os.ReadFile(e.manifestPath())
	if errors.Is(err, fs.ErrNotExist) {
		return map[string]any{}, "ok"
	}
	if err != nil {
		return map[string]any{}, "unreadable"
	}
	var m map[string]any
	if json.Unmarshal(b, &m) != nil {
		return map[string]any{}, "corrupt"
	}
	en, ok := m["entries"].(map[string]any)
	if !ok {
		return map[string]any{}, "corrupt"
	}
	return en, "ok"
}

func (e Env) stashFilesExist() bool {
	ents, err := os.ReadDir(filepath.Join(e.Backup(), "credentials"))
	if err != nil {
		return !errors.Is(err, fs.ErrNotExist)
	}
	for _, d := range ents {
		if strings.HasPrefix(d.Name(), ".unclaimed-") && strings.HasSuffix(d.Name(), ".enc") {
			return true
		}
	}
	return false
}

// mutateManifest 는 manifest 잠금 아래에서 고친다. 못 읽은 manifest 를 빈 표로 다시 쓰면 보관본이 고아가 되므로 거부한다.
func (e Env) mutateManifest(fn func(map[string]any)) error {
	p := e.manifestPath()
	unlock, err := flock(strings.TrimSuffix(p, ".json")+".lock", 10*time.Second)
	if err != nil {
		return err
	}
	defer unlock()
	en, verdict := e.readManifestEx()
	if verdict == "unreadable" {
		return errors.New("unclaimed manifest unreadable")
	}
	if verdict == "corrupt" && exists(p) {
		os.Rename(p, fmt.Sprintf("%s.corrupt-%d", p, time.Now().Unix())) // 깨진 원본은 옆에 남긴다
	}
	fn(en)
	return writeJSONAtomic(p, map[string]any{"schemaVersion": 1, "entries": en})
}

// stashSuccessor 는 새 토큰을 보관본으로 남긴다. 다음 수집에서 백업이 아직 consumedFP 세대면 이걸 채택한다.
func (e Env) stashSuccessor(num, creds, consumedFP, reason string) error {
	h := sha256.Sum256([]byte(creds))
	r := make([]byte, 3)
	rand.Read(r)
	id := time.Now().UTC().Format("20060102T150405") + "-" + hex.EncodeToString(h[:])[:12] + "-" + hex.EncodeToString(r)
	if err := e.writeEnc(e.stashEntryPath(id), creds); err != nil {
		return err
	}
	return e.mutateManifest(func(en map[string]any) {
		en[id] = map[string]any{"createdAt": timestamp(), "reason": reason, "configSlot": num, "consumedFp": consumedFP, "fingerprint": fingerprint(creds)}
	})
}

func (e Env) readStashEntry(id string) (string, bool) {
	b, err := os.ReadFile(e.stashEntryPath(id))
	if errors.Is(err, fs.ErrNotExist) {
		return "", false
	}
	if err != nil {
		return "", true
	}
	d, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(b)))
	if err != nil {
		return "", false
	}
	return string(d), false
}

func (e Env) retireStash(id string) {
	os.Remove(e.stashEntryPath(id))
	e.mutateManifest(func(en map[string]any) { delete(en, id) })
}

// adoptStash: 백업이 아직 이미 쓴 세대(consumedFp)라면 그 다음 세대 보관본을 백업으로 올린다. 안 하면 쓴 토큰을 또 보낸다(spec R10).
func (e Env) adoptStash(num, email, cur string) (string, error) {
	curFP := fingerprint(cur)
	if curFP == "" {
		return "", nil
	}
	en, verdict := e.readManifestEx()
	if verdict == "unreadable" || (verdict == "corrupt" && e.stashFilesExist()) {
		return "", errStashUnreadable
	}
	deferred := false
	for id, v := range en {
		meta, _ := v.(map[string]any)
		if meta["configSlot"] != num {
			continue
		}
		if meta["consumedFp"] != curFP {
			// 다른 쪽이 이긴 경쟁의 보관본, 또는 내용이 사라진 보관본은 다시 쓸 일이 없다
			if b, unreadable := e.readStashEntry(id); meta["reason"] == "consume-gate-cas-conflict" || (b == "" && !unreadable) {
				e.retireStash(id)
			}
			continue
		}
		b, unreadable := e.readStashEntry(id)
		if unreadable {
			deferred = true
			continue
		}
		if b == "" {
			e.retireStash(id)
			continue
		}
		if err := e.writeAccountCredentials(num, email, b); err != nil {
			return "", err
		}
		e.retireStash(id)
		e.log("INFO", fmt.Sprintf("Adopted account %s's stashed successor (%v): the stored generation was already consumed by the gate pass that stashed it.", num, meta["reason"]))
		return b, nil
	}
	if deferred {
		return "", errStashUnreadable
	}
	return "", nil
}
