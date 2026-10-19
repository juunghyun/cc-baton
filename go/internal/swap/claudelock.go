package swap

import (
	"errors"
	"math/rand"
	"os"
	"path/filepath"
	"time"
)

// Claude Code 는 토큰을 갱신할 때 npm proper-lockfile 방식(디렉토리 mkdir)으로 두 잠금을 이 순서로 잡는다:
// ~/.claude/.oauth_refresh.lock → ~/.claude.lock. 같은 순서로 잡아야 서로 교착하지 않는다(claude_locks.py, CC 2.1.218).
const (
	credLockStaleS = 60 * time.Second // 살아 있는 CC 가 들고 있을 수 있는 잠금은 60초 전엔 뺏지 않는다
	lockTouchEvery = 3 * time.Second
)

var claudeLockTimeout = 9 * time.Second // 테스트가 줄인다

var errClaudeLock = errors.New("Claude Code is holding its credential lock")

func properLock(dir string, stale time.Duration) (func(), error) {
	os.MkdirAll(filepath.Dir(dir), 0o755)
	deadline := time.Now().Add(claudeLockTimeout)
	for {
		if os.Mkdir(dir, 0o755) == nil {
			break
		}
		if time.Now().After(deadline) {
			return nil, errClaudeLock
		}
		if st, err := os.Stat(dir); err == nil && time.Since(st.ModTime()) > stale {
			os.Remove(dir) // 주인이 죽고 남은 잠금
			continue
		}
		time.Sleep(250*time.Millisecond + time.Duration(rand.Int63n(int64(250*time.Millisecond))))
	}
	stop := make(chan struct{})
	go func() { // 들고 있는 동안 시각을 갱신해야 다른 쪽이 죽은 잠금으로 보고 뺏지 않는다
		t := time.NewTicker(lockTouchEvery)
		defer t.Stop()
		for {
			select {
			case <-stop:
				return
			case <-t.C:
				now := time.Now()
				os.Chtimes(dir, now, now)
			}
		}
	}()
	return func() { close(stop); os.Remove(dir) }, nil
}

// claudeCredentialsLock 은 기본 프로필(~/.claude) 의 두 잠금을 잡는다. CLAUDE_CONFIG_DIR 은 보지 않는다(spec R6).
func (e Env) claudeCredentialsLock() (func(), error) {
	home := filepath.Join(e.Home, ".claude")
	un1, err := properLock(filepath.Join(home, ".oauth_refresh.lock"), credLockStaleS)
	if err != nil {
		return nil, err
	}
	un2, err := properLock(home+".lock", credLockStaleS)
	if err != nil {
		un1()
		return nil, err
	}
	return func() { un2(); un1() }, nil
}
