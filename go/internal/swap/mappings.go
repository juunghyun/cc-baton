package swap

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func (e Env) mappingsPath() string { return filepath.Join(e.Backup(), "mappings.json") }

// normalizePath 는 Python Path.expanduser().resolve() 와 같다: 있는 부분까지 심볼릭 링크를 풀고 나머지는 그대로 붙인다.
func (e Env) normalizePath(p string) (string, error) {
	if p == "~" || strings.HasPrefix(p, "~/") {
		p = filepath.Join(e.Home, p[1:])
	}
	p, err := filepath.Abs(p)
	if err != nil {
		return "", err
	}
	rest := ""
	for cur := p; ; cur = filepath.Dir(cur) {
		if r, err := filepath.EvalSymlinks(cur); err == nil {
			return filepath.Join(r, rest), nil
		}
		if cur == filepath.Dir(cur) {
			return p, nil
		}
		rest = filepath.Join(filepath.Base(cur), rest)
	}
}

// loadMappings: 없거나 깨졌으면 빈 표로 본다(Python mappings.py:46-57 과 같다).
func (e Env) loadMappings() map[string]any {
	m := readJSONMap(e.mappingsPath())
	mp, _ := m["mappings"].(map[string]any)
	if mp == nil {
		mp = map[string]any{}
	}
	return mp
}

func (e Env) saveMappings(mp map[string]any) error {
	if err := os.MkdirAll(e.Backup(), 0o700); err != nil {
		return err
	}
	if err := os.Chmod(e.Backup(), 0o700); err != nil {
		return err
	}
	return writeJSONAtomic(e.mappingsPath(), map[string]any{"schemaVersion": 1, "mappings": mp})
}

// Map 은 폴더를 계정에 묶는다. 슬롯 번호는 재사용되므로 (email, org) 로 저장한다.
// 돌려주는 값: 슬롯 번호, 이메일, 정규화한 경로, 이전에 묶여 있던 이메일("" = 없었음).
func (e Env) Map(id, path string) (num, email, key, prev string, err error) {
	m, err := e.loadSequenceRaw()
	if err != nil {
		return
	}
	num, a, err := resolve(m, id)
	if err != nil {
		return
	}
	if key, err = e.normalizePath(path); err != nil {
		return
	}
	mp := e.loadMappings()
	if old, ok := mp[key].(map[string]any); ok {
		prev = str(old, "email")
	}
	email = str(a, "email")
	mp[key] = map[string]any{"email": email, "organizationUuid": str(a, "organizationUuid"), "added": timestamp()}
	err = e.saveMappings(mp)
	return
}

// Unmap 은 정확히 그 경로의 매핑만 지운다. 없었으면 removed=false 이고 파일은 그대로다.
func (e Env) Unmap(path string) (key string, removed bool, err error) {
	if key, err = e.normalizePath(path); err != nil {
		return
	}
	mp := e.loadMappings()
	if _, ok := mp[key]; !ok {
		return key, false, nil
	}
	delete(mp, key)
	return key, true, e.saveMappings(mp)
}

type Mapping struct{ Path, Email, Slot string }

// Mappings 는 경로순 목록. 계정이 지워졌으면 Slot 이 "".
func (e Env) Mappings() ([]Mapping, error) {
	s, err := e.loadSequence()
	if err != nil {
		return nil, err
	}
	mp := e.loadMappings()
	keys := make([]string, 0, len(mp))
	for k := range mp {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var out []Mapping
	for _, k := range keys {
		en, _ := mp[k].(map[string]any)
		row := Mapping{Path: k, Email: str(en, "email")}
		if s != nil {
			row.Slot = s.slotOf(row.Email, str(en, "organizationUuid"))
		}
		out = append(out, row)
	}
	return out, nil
}
