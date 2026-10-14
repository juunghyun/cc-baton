// ccswap: claude-swap(cswap) 의 Go 판. 지금은 읽기 전용 list/status --json 만 있다.
package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/juunghyun/cc-baton/go/internal/swap"
)

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	if len(args) != 2 || args[1] != "--json" || (args[0] != "list" && args[0] != "status") {
		fmt.Fprintln(os.Stderr, "usage: ccswap list --json | ccswap status --json")
		return 2
	}
	home, err := os.UserHomeDir()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	env := swap.Env{Home: home}
	var out map[string]any
	if args[0] == "list" {
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
