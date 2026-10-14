package swap

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"os/user"
	"strings"
	"time"
)

// Security.framework 로 직접 읽으면 항목 ACL 때문에 GUI 프롬프트가 뜬다. claude-swap 처럼 security 명령으로만 다룬다.
var securityBin = "/usr/bin/security" // 테스트가 가짜 스크립트로 바꾼다

var errKeychain = errors.New("keychain unreadable")

// kcAccount 는 Claude Code 의 getUsername() 과 같은 순서다. 다르면 다른 항목을 보게 된다.
func kcAccount() string {
	if u := os.Getenv("USER"); u != "" {
		return u
	}
	if u, err := user.Current(); err == nil && u.Username != "" {
		return u.Username
	}
	return "claude-code-user"
}

// kcGet 은 비밀값을 읽는다. 없으면(rc 44) ("", nil), 잠김·거부·타임아웃은 errKeychain.
// 둘을 구분해야 잠긴 키체인을 "백업 없음"으로 읽지 않는다.
func kcGet(service, account string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, securityBin, "find-generic-password", "-a", account, "-w", "-s", service).Output()
	if err == nil {
		return strings.TrimSuffix(string(out), "\n"), nil
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) && ee.ExitCode() == 44 {
		return "", nil
	}
	return "", errKeychain
}
