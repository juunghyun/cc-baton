# cc-baton

Pass the baton between Claude Code accounts: switch accounts mid-session with the full conversation (the transcript itself, not a summary), and see which account you're on, how much every account has left, and your context usage in a statusline HUD.

macOS only. Built on [claude-swap](https://github.com/realiti4/claude-swap) (MIT).

> Work in progress. Not yet packaged. Currently wired into the author's own `~/.claude` by symlinks.

## Layout

One command, `cc-baton <subcommand>`:

| Subcommand | What |
|---|---|
| `swap` | In-session account swap: copies the one transcript to the target profile and resumes it. `/swap` and rate-limit hooks |
| `pick` | Account picker shown at launch |
| `statusline` | HUD: account, model + effort meter, context, per-account usage, feature switches |
| `toggle` | Feature switches (`update`, `hib`, `swap`) shown on the HUD's third line |
| `hib` | Hibernate idle sessions (kill the process, keep the tab and context) |
| `update` | Keep Claude Code up to date before launch and swap |

| Path | What |
|---|---|
| `cc_baton/` | The package (`state.py` reads claude-swap's on-disk state) |
| `commands/` | `/swap`, `/sleep` slash commands |
| `shell/cc.zsh` | `cc` launcher loop (pick account, run, relaunch on swap/wake) |
| `config/cc-accounts.example.json` | Account kinds and feature settings |

Requires Python 3.12+ (claude-swap does). Dev install: `uv tool install -e .`

## Tests

```sh
uv run --group dev python -m pytest tests -q
```

Each test builds a fake `$HOME` with claude-swap state and runs the scripts as real processes, so your own setup is never touched.

## License

MIT
