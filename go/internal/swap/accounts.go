package swap

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// UserError 는 사용자가 고칠 수 있는 입력 문제(없는 계정, 잘못된 별칭 등).
type UserError struct{ msg string }

func (e *UserError) Error() string { return e.msg }

func timestamp() string { return time.Now().UTC().Format("2006-01-02T15:04:05Z") }

// writeJSONAtomic 은 같은 폴더의 임시 파일에 0600 으로 쓰고 rename 한다. 중간에 죽어도 원본은 온전하다.
func writeJSONAtomic(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+"-*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp) // rename 이 끝나면 없는 파일이라 무해하다
	if err := f.Chmod(0o600); err != nil {
		f.Close()
		return err
	}
	if _, err := f.Write(b); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// loadSequenceRaw 는 쓰기용으로 sequence.json 전체를 읽는다. 모르는 키도 그대로 다시 쓰기 위해 map 으로 둔다.
func (e Env) loadSequenceRaw() (map[string]any, error) {
	b, err := os.ReadFile(filepath.Join(e.Backup(), "sequence.json"))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, &UserError{"no accounts are managed yet"}
	}
	if err != nil {
		return nil, &ConfigError{fmt.Sprintf("cannot read sequence.json: %v", err)}
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, &ConfigError{fmt.Sprintf("sequence.json is corrupt: %v", err)}
	}
	return m, nil
}

func (e Env) saveSequence(m map[string]any) error {
	m["lastUpdated"] = timestamp()
	return writeJSONAtomic(filepath.Join(e.Backup(), "sequence.json"), m)
}

func accountsOf(m map[string]any) map[string]map[string]any {
	out := map[string]map[string]any{}
	raw, _ := m["accounts"].(map[string]any)
	for k, v := range raw {
		if a, ok := v.(map[string]any); ok {
			out[k] = a
		}
	}
	return out
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// resolve 는 번호 → 별칭 → 이메일 순으로 계정을 찾는다. 이메일이 여러 계정과 맞으면 에러.
func resolve(m map[string]any, id string) (string, map[string]any, error) {
	accs := accountsOf(m)
	num := ""
	if isDigits(id) {
		num = id
	} else if n := aliasOwner(accs, id); n != "" {
		num = n
	} else {
		var hits []string
		for n, a := range accs {
			if a["email"] == id {
				hits = append(hits, n)
			}
		}
		sort.Strings(hits)
		if len(hits) > 1 {
			return "", nil, &ConfigError{fmt.Sprintf("email '%s' matches accounts %s; use the account number", id, strings.Join(hits, ", "))}
		}
		if len(hits) == 1 {
			num = hits[0]
		}
	}
	if num == "" {
		return "", nil, &UserError{"no account found with identifier: " + id}
	}
	a, ok := accs[num]
	if !ok {
		return "", nil, &UserError{fmt.Sprintf("account-%s does not exist", num)}
	}
	return num, a, nil
}

func aliasOwner(accs map[string]map[string]any, alias string) string {
	if alias == "" {
		return ""
	}
	for n, a := range accs {
		if strings.EqualFold(str(a, "alias"), alias) {
			return n
		}
	}
	return ""
}

var aliasRE = regexp.MustCompile(`^[a-z0-9_.-]+$`)

// normalizeAlias: 숫자만은 슬롯 번호와, '-' 시작은 명령 옵션과 헷갈려서 막는다.
func normalizeAlias(name string) (string, error) {
	n := strings.ToLower(strings.TrimSpace(name))
	switch {
	case n == "":
		return "", &UserError{"alias cannot be empty"}
	case isDigits(n):
		return "", &UserError{fmt.Sprintf("alias '%s' cannot be purely numeric (reserved for slot numbers)", name)}
	case strings.HasPrefix(n, "-"):
		return "", &UserError{fmt.Sprintf("alias '%s' cannot start with '-'", name)}
	case !aliasRE.MatchString(n):
		return "", &UserError{fmt.Sprintf("alias '%s' may only contain letters, digits, '-', '_', and '.'", name)}
	}
	return n, nil
}

// Python 은 세션 셸(CLAUDE_CONFIG_DIR 이 세션 프로필)에서 계정 변경을 거부한다. 잘못된 라이브 저장소를 볼까 봐서인데,
// Go 는 그 env 를 보지 않으므로 별칭·매핑에는 그 위험이 없다. 그래서 그 검사는 옮기지 않았다.

func (e Env) SetAlias(id, alias string) (string, string, error) {
	norm, err := normalizeAlias(alias)
	if err != nil {
		return "", "", err
	}
	m, err := e.loadSequenceRaw()
	if err != nil {
		return "", "", err
	}
	num, a, err := resolve(m, id)
	if err != nil {
		return "", "", err
	}
	if owner := aliasOwner(accountsOf(m), norm); owner != "" && owner != num {
		return "", "", &ConfigError{fmt.Sprintf("alias '%s' is already used by account %s", norm, owner)}
	}
	a["alias"] = norm
	return num, norm, e.saveSequence(m)
}

// UnsetAlias 는 멱등이다. 별칭이 없으면 파일을 건드리지 않는다.
func (e Env) UnsetAlias(id string) (string, error) {
	m, err := e.loadSequenceRaw()
	if err != nil {
		return "", err
	}
	num, a, err := resolve(m, id)
	if err != nil {
		return "", err
	}
	if _, ok := a["alias"]; !ok {
		return num, nil
	}
	delete(a, "alias")
	return num, e.saveSequence(m)
}

type Alias struct{ Num, Alias, Email string }

func (e Env) Aliases() ([]Alias, error) {
	s, err := e.loadSequence()
	if err != nil || s == nil {
		return nil, err
	}
	var out []Alias
	for n, a := range s.Accounts {
		if al := str(a, "alias"); al != "" {
			out = append(out, Alias{n, al, str(a, "email")})
		}
	}
	sort.Slice(out, func(i, j int) bool { return numLess(out[i].Num, out[j].Num) })
	return out, nil
}

func numLess(a, b string) bool {
	if len(a) != len(b) {
		return len(a) < len(b)
	}
	return a < b
}
