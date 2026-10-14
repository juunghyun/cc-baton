package swap

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	liveService    = "Claude Code-credentials"
	managedService = "Claude Code"
	backupService  = "claude-swap"
	expiryBufferMs = 5 * 60 * 1000
)

// Env 는 홈 경로 하나로 모든 위치를 정한다. 테스트는 가짜 HOME 을 넘긴다.
type Env struct{ Home string }

func (e Env) Backup() string { return filepath.Join(e.Home, ".claude-swap-backup") }

// GlobalConfig 는 기본 프로필의 설정 파일이다. CLAUDE_CONFIG_DIR 은 일부러 보지 않는다:
// 세션 프로필을 가리킨 채 돌면 다른 계정 토큰을 기본 로그인과 짝짓게 된다(spec R6).
func (e Env) GlobalConfig() string {
	if legacy := filepath.Join(e.Home, ".claude", ".config.json"); exists(legacy) {
		return legacy
	}
	return filepath.Join(e.Home, ".claude.json")
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

func readJSONMap(p string) map[string]any {
	b, err := os.ReadFile(p)
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(b, &m) != nil {
		return nil
	}
	return m
}

// readBackup 은 계정 백업을 읽는다. .enc 가 키체인보다 우선한다(키체인 쓰기 실패 때 남는 최신본).
// unreadable 은 "없음"이 아니라 "읽다가 실패"다.
func (e Env) readBackup(num, email string) (value string, unreadable bool) {
	enc := filepath.Join(e.Backup(), "credentials", ".creds-"+num+"-"+email+".enc")
	if _, err := os.Stat(enc); err == nil {
		if b, err := os.ReadFile(enc); err != nil {
			unreadable = true
		} else if d, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(b))); err == nil && len(d) > 0 {
			return string(d), false
		} // 깨진 .enc 는 키체인 사본으로 넘어간다
	} else if !errors.Is(err, fs.ErrNotExist) {
		unreadable = true
	}
	v, err := kcGet(backupService, "account-"+num+"-"+email)
	if err != nil {
		return "", true
	}
	return v, unreadable && v == ""
}

// readActive 는 Claude Code 의 기본 프로필 로그인 정보를 읽는다.
// 키체인 → ~/.claude/.credentials.json → 관리형 API 키 순서. unreadable 은 키체인이 실패했고 다른 데서도 못 메웠다는 뜻.
func (e Env) readActive() (value string, unreadable bool) {
	failed := false
	for i := 0; i < 2; i++ {
		v, err := kcGet(liveService, kcAccount())
		if err == nil {
			if v != "" {
				return v, false
			}
			failed = false
			break
		}
		failed = true
		if i == 0 {
			time.Sleep(300 * time.Millisecond)
		}
	}
	if p := filepath.Join(e.Home, ".claude", ".credentials.json"); exists(p) {
		b, err := os.ReadFile(p)
		if err != nil {
			return "", failed
		}
		if strings.TrimSpace(string(b)) != "" {
			return string(b), false
		}
	}
	if v, err := kcGet(managedService, kcAccount()); err == nil && v != "" {
		return v, false
	}
	if cfg := readJSONMap(e.GlobalConfig()); cfg != nil {
		if k, ok := cfg["primaryApiKey"].(string); ok && k != "" {
			return k, false
		}
	}
	return "", failed
}

func oauthData(creds string) map[string]any {
	var m map[string]any
	if json.Unmarshal([]byte(creds), &m) != nil {
		return nil
	}
	o, _ := m["claudeAiOauth"].(map[string]any)
	return o
}

func accessToken(creds string) string {
	t, _ := oauthData(creds)["accessToken"].(string)
	return t
}

func looksLikeAPIKey(creds string) bool {
	t := strings.TrimSpace(creds)
	return strings.HasPrefix(t, "sk-ant-api")
}

func tokenExpired(o map[string]any) bool {
	ms, ok := o["expiresAt"].(float64)
	return ok && float64(time.Now().UnixMilli()+expiryBufferMs) >= ms
}

// fingerprint 는 usage.json 의 struckFingerprint 와 바이트 단위로 같아야 한다.
func fingerprint(creds string) string {
	if creds == "" {
		return ""
	}
	if rt, _ := oauthData(creds)["refreshToken"].(string); rt != "" {
		h := sha256.Sum256([]byte(rt))
		return "sha256:" + hex.EncodeToString(h[:])
	}
	h := sha256.Sum256([]byte(creds))
	return "sha256-full:" + hex.EncodeToString(h[:])
}
