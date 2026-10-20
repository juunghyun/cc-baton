package swap

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const staleMarker = ".cswap-stale-credentials"

func kcQuote(v string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(v) + `"`
}

// kcSet 은 비밀값을 argv 에 올리지 않으려고 `security -i` 표준입력으로 쓴다. 한 줄 한도를 넘을 때만 argv 로 넘긴다.
func kcSet(service, account, value string) error {
	h := hex.EncodeToString([]byte(value))
	line := fmt.Sprintf("add-generic-password -U -a %s -s %s -X %s\n", kcQuote(account), kcQuote(service), h)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var cmd *exec.Cmd
	if len(line) <= 4096-64 {
		cmd = exec.CommandContext(ctx, securityBin, "-i")
		cmd.Stdin = strings.NewReader(line)
	} else {
		cmd = exec.CommandContext(ctx, securityBin, "add-generic-password", "-U", "-a", account, "-s", service, "-X", h)
	}
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("security add-generic-password failed: %v %s", err, bytes.TrimSpace(out))
	}
	return nil
}

// kcDelete: 없던 항목(rc 44)도 성공이다.
func kcDelete(service, account string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := exec.CommandContext(ctx, securityBin, "delete-generic-password", "-a", account, "-s", service).Run()
	var ee *exec.ExitError
	if err == nil || errors.As(err, &ee) && ee.ExitCode() == 44 {
		return nil
	}
	return errKeychain
}

func (e Env) encPath(num, email string) string {
	return filepath.Join(e.Backup(), "credentials", ".creds-"+num+"-"+email+".enc")
}

func (e Env) writeEnc(path, creds string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), "*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	f.Chmod(0o600)
	_, werr := f.WriteString(base64.StdEncoding.EncodeToString([]byte(creds)))
	if cerr := f.Close(); werr != nil || cerr != nil {
		return errors.Join(werr, cerr)
	}
	return os.Rename(f.Name(), path)
}

// writeBackup 은 계정 백업을 바꾼다(credentials.py _write_account_credentials).
// 먼저 지금 세대를 .prev 로 남긴다: refresh token 은 한 곳에만 있어서, 잘못 덮었을 때 재로그인 없이 되살릴 마지막 기회다.
func (e Env) writeBackup(num, email, creds string) error {
	acct := "account-" + num + "-" + email
	if cur, unreadable := e.readBackup(num, email); unreadable {
		e.log("WARNING", fmt.Sprintf("Could not retain previous credential generation for account %s: the current backup exists but could not be read", num))
	} else if cur != "" && cur != creds {
		if err := kcSet(backupService, acct+".prev", cur); err != nil {
			e.log("WARNING", fmt.Sprintf("Failed to retain previous credential generation for account %s: %v", num, err))
		}
	}
	enc := e.encPath(num, email)
	if err := kcSet(backupService, acct, creds); err == nil {
		// .enc 가 키체인보다 먼저 읽히므로, 남아 있으면 방금 쓴 값을 가린다. 못 지우면 같은 값으로 맞춘다.
		if rerr := os.Remove(enc); rerr != nil && !errors.Is(rerr, fs.ErrNotExist) {
			return e.writeEnc(enc, creds)
		}
		return nil
	} else {
		e.log("WARNING", "Keychain backup write failed, falling back to file: "+err.Error())
	}
	if err := e.writeEnc(enc, creds); err != nil {
		return err
	}
	kcDelete(backupService, acct) // 키체인이 돌아왔을 때 옛 사본이 새 파일을 가리지 않게
	return nil
}

// writeAccountCredentials 는 백업을 바꾸고 그 계정 세션 프로필을 다음 실행 때 새 백업으로 다시 채우게 한다.
// 백업 쓰기가 성공한 뒤로는 에러를 내지 않는다: 호출자가 "저장 실패"로 읽고 이미 쓴 토큰을 다시 보내게 된다.
func (e Env) writeAccountCredentials(num, email, creds string) error {
	if err := e.writeBackup(num, email, creds); err != nil {
		return err
	}
	e.postBackupWrite(num, email)
	return nil
}

// postBackupWrite: 프로필에서 claude 가 돌고 있거나, 돌고 있는지 확실히 모르면 토큰을 건드리지 않고 표시만 남긴다.
// Python 은 읽지 못한 세션 기록을 무시하지만 여기서는 "모름 = 돌고 있음"으로 본다(spec R2).
func (e Env) postBackupWrite(num, email string) {
	dir := e.sessionDir(num, email)
	if _, err := os.Stat(dir); err != nil {
		return
	}
	live, unreadable := liveSessions(dir)
	if live > 0 || unreadable > 0 || !isASCII(dir) {
		if err := markStale(dir); err != nil {
			e.log("ERROR", fmt.Sprintf("Account %s's backup credentials changed but its session profile could not be marked stale", num))
		}
		return
	}
	if err := kcDelete(sessionService(dir), kcAccount()); err != nil {
		markStale(dir) // 못 지웠으면 다음 실행 때라도 다시 채우게
		e.log("WARNING", fmt.Sprintf("Stored account %s's credential but could not invalidate its session profile; marked it stale", num))
		return
	}
	os.Remove(filepath.Join(dir, ".credentials.json"))
	clearStale(dir)
	e.log("INFO", "Invalidated session credentials for account "+num)
}

// isASCII: sessionService 가 NFC 정규화를 생략하므로, 비ASCII 경로면 키체인 이름을 확신할 수 없다.
func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// 표시는 프로필 폴더 옆에 둔다. 프로필 폴더 자체에 쓰기가 막힌 경우에도 남길 수 있게(session.py stale_marker_for).
func markStale(dir string) error {
	f, err := os.OpenFile(filepath.Join(filepath.Dir(dir), "."+filepath.Base(dir)+staleMarker), os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	return f.Close()
}

func clearStale(dir string) {
	os.Remove(filepath.Join(filepath.Dir(dir), "."+filepath.Base(dir)+staleMarker))
	os.Remove(filepath.Join(dir, staleMarker)) // 예전 위치
}

func isStale(dir string) bool {
	return exists(filepath.Join(filepath.Dir(dir), "."+filepath.Base(dir)+staleMarker)) || exists(filepath.Join(dir, staleMarker))
}
