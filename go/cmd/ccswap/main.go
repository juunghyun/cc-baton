// ccswap: claude-swap(cswap) 의 Go 판. cc-baton 이 쓰는 list/status --json, alias, map, unmap, run, add, add-token, remove.
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"slices"
	"strings"
	"syscall"

	"github.com/juunghyun/cc-baton/go/internal/swap"
)

const usage = `usage:
  ccswap list --json | status --json
  ccswap alias [NUM|EMAIL|ALIAS NAME | NUM|EMAIL|ALIAS --unset]
  ccswap map [NUM|EMAIL|ALIAS [PATH]]
  ccswap unmap [PATH]
  ccswap run NUM|EMAIL|ALIAS [--no-share] [-- CLAUDE_ARGS...]
  ccswap add [--alias NAME]
  ccswap add-token [TOKEN|-] [--email EMAIL]
  ccswap remove NUM|EMAIL|ALIAS [--yes]`

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	home, err := os.UserHomeDir()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	env := swap.Env{Home: home}
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, usage)
		return 2
	}
	cmd, rest := args[0], args[1:]
	switch cmd {
	case "list", "status":
		return jsonCmd(env, cmd, rest)
	case "run":
		return runCmd(env, rest)
	case "add", "add-token", "remove":
		if os.Geteuid() == 0 {
			return fail(errors.New("do not run this as root"))
		}
		return accountCmd(env, cmd, rest)
	case "alias", "map", "unmap":
		if os.Geteuid() == 0 {
			return fail(errors.New("do not run this as root"))
		}
	}
	switch {
	case cmd == "alias" && len(rest) == 0:
		rows, err := env.Aliases()
		if err != nil {
			return fail(err)
		}
		if len(rows) == 0 {
			fmt.Println("No aliases set")
		}
		for _, r := range rows {
			fmt.Printf("  %s: %s (%s)\n", r.Num, r.Alias, r.Email)
		}
	case cmd == "alias" && len(rest) == 2 && (rest[0] == "--unset" || rest[1] == "--unset"):
		id := rest[0]
		if id == "--unset" {
			id = rest[1]
		}
		num, err := env.UnsetAlias(id)
		if err != nil {
			return fail(err)
		}
		fmt.Printf("Removed alias for Account %s\n", num)
	case cmd == "alias" && len(rest) == 2:
		num, norm, err := env.SetAlias(rest[0], rest[1])
		if err != nil {
			return fail(err)
		}
		fmt.Printf("Set alias '%s' for Account %s\n", norm, num)
	case cmd == "map" && len(rest) == 0:
		rows, err := env.Mappings()
		if err != nil {
			return fail(err)
		}
		if len(rows) == 0 {
			fmt.Println("No directory mappings yet.\nMap one with: ccswap map <NUM|EMAIL> [PATH]")
		}
		for _, r := range rows {
			if r.Slot != "" {
				fmt.Printf("  %s → %s: %s\n", r.Path, r.Slot, r.Email)
			} else {
				fmt.Printf("  %s → %s (account removed)\n", r.Path, r.Email)
			}
		}
	case cmd == "map" && len(rest) <= 2:
		target := cwdOr(rest[1:])
		if st, err := os.Stat(target); err != nil || !st.IsDir() {
			fmt.Fprintf(os.Stderr, "Warning: %s is not an existing directory (mapping it anyway)\n", target)
		}
		num, email, key, prev, err := env.Map(rest[0], target)
		if err != nil {
			return fail(err)
		}
		if prev != "" && prev != email {
			fmt.Printf("Mapped %s → Account-%s (%s) (was %s)\n", key, num, email, prev)
		} else {
			fmt.Printf("Mapped %s → Account-%s (%s)\n", key, num, email)
		}
	case cmd == "unmap" && len(rest) <= 1:
		key, removed, err := env.Unmap(cwdOr(rest))
		if err != nil {
			return fail(err)
		}
		if removed {
			fmt.Printf("Unmapped %s\n", key)
		} else {
			fmt.Printf("No mapping for %s\n", key)
		}
	default:
		fmt.Fprintln(os.Stderr, usage)
		return 2
	}
	return 0
}

func cwdOr(args []string) string {
	if len(args) > 0 {
		return args[0]
	}
	wd, _ := os.Getwd()
	return wd
}

func fail(err error) int {
	fmt.Fprintf(os.Stderr, "Error: %v\n", err)
	return 1
}

func jsonCmd(env swap.Env, cmd string, rest []string) int {
	if len(rest) != 1 || rest[0] != "--json" {
		fmt.Fprintln(os.Stderr, usage)
		return 2
	}
	var out map[string]any
	var err error
	if cmd == "list" {
		out, err = env.ListJSON()
	} else {
		out, err = env.StatusJSON()
	}
	code := 0
	if err != nil {
		typ := "Error"
		if _, ok := err.(*swap.ConfigError); ok {
			typ = "ConfigError"
		}
		out = map[string]any{"schemaVersion": 1, "error": map[string]any{"type": typ, "message": err.Error()}}
		code = 1
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	enc.Encode(out)
	return code
}

// runCmd 는 claude 로 프로세스를 바꾼다(돌아오지 않는다). baton.zsh 가 `cswap run <계정> [-- 인자]` 로 부른다.
func runCmd(env swap.Env, args []string) int {
	if os.Geteuid() == 0 {
		return fail(errors.New("do not run this as root"))
	}
	var head, tail []string
	head = args
	for i, a := range args {
		if a == "--" {
			head, tail = args[:i], args[i+1:]
			break
		}
	}
	share, id := true, ""
	for _, a := range head {
		switch {
		case a == "--no-share":
			share = false
		case id == "" && !strings.HasPrefix(a, "-"):
			id = a
		default:
			fmt.Fprintln(os.Stderr, usage)
			return 2
		}
	}
	if id == "" {
		fmt.Fprintln(os.Stderr, usage)
		return 2
	}
	l, err := env.PrepareRun(id, tail, share)
	if err != nil {
		return fail(err)
	}
	fmt.Println(l.Note)
	return fail(syscall.Exec(l.Bin, l.Argv, l.Env)) // 성공하면 여기 오지 않는다
}

// flags 는 "--이름 값" 과 단독 플래그를 나눈다. 모르는 플래그면 ok=false.
func flags(args []string, valued, bare []string) (pos []string, val map[string]string, set map[string]bool, ok bool) {
	val, set = map[string]string{}, map[string]bool{}
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch {
		case slices.Contains(valued, a) && i+1 < len(args):
			val[a] = args[i+1]
			i++
		case slices.Contains(bare, a):
			set[a] = true
		case strings.HasPrefix(a, "--"):
			return nil, nil, nil, false
		default:
			pos = append(pos, a)
		}
	}
	return pos, val, set, true
}

func accountCmd(env swap.Env, cmd string, args []string) int {
	pos, val, set, ok := flags(args, []string{"--alias", "--email"}, []string{"--yes"})
	if !ok {
		fmt.Fprintln(os.Stderr, usage)
		return 2
	}
	switch {
	case cmd == "add" && len(pos) == 0 && val["--email"] == "":
		num, updated, err := env.Add(val["--alias"])
		if err != nil {
			return fail(err)
		}
		if updated {
			fmt.Printf("Updated credentials for Account %s.\n", num)
		} else {
			fmt.Printf("Added Account %s.\n", num)
		}
	case cmd == "add-token" && len(pos) <= 1 && val["--alias"] == "":
		tok := ""
		if len(pos) == 1 {
			tok = pos[0]
		}
		if tok == "-" {
			line, _ := bufio.NewReader(os.Stdin).ReadString('\n')
			tok = strings.TrimRight(line, "\n")
		} else if tok == "" {
			tok = readSecret("Token: ")
		}
		num, updated, err := env.AddToken(tok, val["--email"])
		if err != nil {
			return fail(err)
		}
		if updated {
			fmt.Printf("Updated token for Account %s.\n", num)
		} else {
			fmt.Printf("Added Account %s (from token).\n", num)
		}
	case cmd == "remove" && len(pos) == 1 && len(val) == 0:
		num, email, err := env.RemoveTarget(pos[0])
		if err != nil {
			return fail(err)
		}
		if !set["--yes"] {
			fmt.Printf("Are you sure you want to permanently remove Account-%s (%s)? Its session profile, including conversation history, is deleted too. [y/N] ", num, email)
			line, _ := bufio.NewReader(os.Stdin).ReadString('\n')
			if strings.ToLower(strings.TrimSpace(line)) != "y" {
				fmt.Println("Cancelled")
				return 0
			}
		}
		if err := env.Remove(num); err != nil {
			return fail(err)
		}
		fmt.Printf("Removed Account-%s (%s)\n", num, email)
	default:
		fmt.Fprintln(os.Stderr, usage)
		return 2
	}
	return 0
}

// readSecret 은 입력을 화면에 보이지 않게 받는다(토큰이 스크롤백에 남지 않게). 터미널이 아니면 그냥 한 줄 읽는다.
func readSecret(prompt string) string {
	fmt.Fprint(os.Stderr, prompt)
	off := exec.Command("stty", "-echo")
	off.Stdin = os.Stdin
	echoOff := off.Run() == nil
	line, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	if echoOff {
		on := exec.Command("stty", "echo")
		on.Stdin = os.Stdin
		on.Run()
		fmt.Fprintln(os.Stderr)
	}
	return strings.TrimRight(line, "\n")
}
