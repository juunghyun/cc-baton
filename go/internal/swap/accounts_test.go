package swap

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func (h *fakeHome) seqRaw() map[string]any {
	b, err := os.ReadFile(filepath.Join(h.env.Backup(), "sequence.json"))
	must(h.t, err)
	var m map[string]any
	must(h.t, json.Unmarshal(b, &m))
	return m
}

func mode(t *testing.T, p string) os.FileMode {
	t.Helper()
	st, err := os.Stat(p)
	must(t, err)
	return st.Mode().Perm()
}

func TestSetAliasKeepsEverythingElse(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	m := h.seqRaw()
	m["futureKey"] = map[string]any{"x": 1.0}
	m["accounts"].(map[string]any)["1"].(map[string]any)["uuid"] = "u-1"
	h.json(".claude-swap-backup/sequence.json", m)

	num, norm, err := h.env.SetAlias("a@gmail.com", " Home ")
	must(t, err)
	if num != "1" || norm != "home" {
		t.Fatalf("= %s %s", num, norm)
	}
	got := h.seqRaw()
	a1 := got["accounts"].(map[string]any)["1"].(map[string]any)
	if a1["alias"] != "home" || a1["uuid"] != "u-1" || got["futureKey"] == nil || got["activeAccountNumber"] != 2.0 {
		t.Fatalf("다른 필드가 사라짐: %v", got)
	}
	if p := filepath.Join(h.env.Backup(), "sequence.json"); mode(t, p) != 0o600 {
		t.Fatalf("권한 %v", mode(t, p))
	}
	// 별칭으로도 찾고, 다시 이름을 바꿀 수 있다
	if _, norm, err = h.env.SetAlias("HOME", "me"); err != nil || norm != "me" {
		t.Fatalf("rename: %v %v", norm, err)
	}
	entries, _ := os.ReadDir(h.env.Backup())
	for _, e := range entries {
		if filepath.Ext(e.Name()) == ".tmp" {
			t.Fatalf("임시 파일이 남음: %s", e.Name())
		}
	}
}

func TestAliasRules(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	before := h.seqRaw()
	var ue *UserError
	var ce *ConfigError
	for _, bad := range []string{"", "42", "-x", "a b", "한글"} {
		if _, _, err := h.env.SetAlias("1", bad); !errors.As(err, &ue) {
			t.Fatalf("%q 허용됨: %v", bad, err)
		}
	}
	if _, _, err := h.env.SetAlias("1", "WORK"); !errors.As(err, &ce) {
		t.Fatalf("다른 계정 별칭과 겹치는데 허용됨: %v", err)
	}
	if _, _, err := h.env.SetAlias("9", "x"); !errors.As(err, &ue) {
		t.Fatalf("없는 슬롯: %v", err)
	}
	if after := h.seqRaw(); after["lastUpdated"] != before["lastUpdated"] {
		t.Fatal("실패했는데 파일이 바뀜")
	}
}

func TestEmailMatchingTwoAccountsIsAmbiguous(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	m := h.seqRaw()
	m["accounts"].(map[string]any)["3"] = map[string]any{"email": "b@corp.com", "organizationUuid": "org3"}
	h.json(".claude-swap-backup/sequence.json", m)
	var ce *ConfigError
	if _, _, err := h.env.SetAlias("b@corp.com", "x"); !errors.As(err, &ce) {
		t.Fatalf("= %v", err)
	}
}

func TestUnsetAliasIsIdempotent(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	_, err := h.env.UnsetAlias("work")
	must(t, err)
	if _, ok := h.seqRaw()["accounts"].(map[string]any)["2"].(map[string]any)["alias"]; ok {
		t.Fatal("별칭이 남음")
	}
	p := filepath.Join(h.env.Backup(), "sequence.json")
	before, _ := os.Stat(p)
	_, err = h.env.UnsetAlias("2")
	must(t, err)
	if after, _ := os.Stat(p); !os.SameFile(before, after) {
		t.Fatal("바꿀 게 없는데 파일을 다시 씀") // 원자적 쓰기는 rename 이라 파일이 바뀐다
	}
}

func TestMapStoresIdentityAndResolvedPath(t *testing.T) {
	h := newHome(t)
	h.twoAccounts()
	real := filepath.Join(h.env.Home, "work", "app")
	must(t, os.MkdirAll(real, 0o755))
	link := filepath.Join(h.env.Home, "app-link")
	must(t, os.Symlink(real, link))

	num, email, key, prev, err := h.env.Map("work", link)
	must(t, err)
	realResolved, _ := filepath.EvalSymlinks(real)
	if num != "2" || email != "b@corp.com" || key != realResolved || prev != "" {
		t.Fatalf("= %s %s %s %q", num, email, key, prev)
	}
	// 같은 폴더를 다른 계정에 다시 묶으면 이전 계정을 알려준다
	if _, _, _, prev, _ = h.env.Map("1", "~/work/app"); prev != "b@corp.com" {
		t.Fatalf("prev = %q", prev)
	}
	raw := readJSONMap(h.env.mappingsPath())
	en := raw["mappings"].(map[string]any)[realResolved].(map[string]any)
	if raw["schemaVersion"] != 1.0 || en["email"] != "a@gmail.com" || en["organizationUuid"] != "" || en["added"] == "" {
		t.Fatalf("mappings.json = %v", raw)
	}
	if mode(t, h.env.mappingsPath()) != 0o600 || mode(t, h.env.Backup()) != 0o700 {
		t.Fatal("권한")
	}
	rows, err := h.env.Mappings()
	must(t, err)
	if len(rows) != 1 || rows[0].Slot != "1" {
		t.Fatalf("목록 = %v", rows)
	}

	// 없는 폴더도 묶이고(경고는 CLI 몫), 있는 부분까지만 링크를 푼다
	_, _, key, _, err = h.env.Map("1", filepath.Join(link, "not-yet"))
	must(t, err)
	if key != filepath.Join(realResolved, "not-yet") {
		t.Fatalf("없는 경로 = %s", key)
	}

	key, removed, err := h.env.Unmap(link)
	must(t, err)
	if !removed || key != realResolved {
		t.Fatalf("unmap = %s %v", key, removed)
	}
	if _, removed, _ = h.env.Unmap(link); removed {
		t.Fatal("두 번 지워짐")
	}
}

func TestWritesRefuseCorruptSequence(t *testing.T) {
	h := newHome(t)
	p := filepath.Join(h.env.Backup(), "sequence.json")
	must(t, os.WriteFile(p, []byte("{oops"), 0o600))
	var ce *ConfigError
	if _, _, err := h.env.SetAlias("1", "x"); !errors.As(err, &ce) {
		t.Fatalf("= %v", err)
	}
	if _, _, _, _, err := h.env.Map("1", h.env.Home); !errors.As(err, &ce) {
		t.Fatalf("= %v", err)
	}
	if b, _ := os.ReadFile(p); string(b) != "{oops" {
		t.Fatal("깨진 파일을 덮어씀")
	}
}
