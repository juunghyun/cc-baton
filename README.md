# cc-baton

Pass the baton between Claude Code accounts: switch accounts mid-session with the full conversation (the transcript itself, not a summary), and see which account you're on, how much every account has left, and your context usage in a statusline HUD.

macOS only. Built on [claude-swap](https://github.com/realiti4/claude-swap) (MIT).

> Work in progress. Not yet packaged. Currently wired into the author's own `~/.claude` by symlinks.

## Layout

| Path | What |
|---|---|
| `bin/cc-swap` | In-session account swap: copies the one transcript to the target profile and resumes it. `/swap` and rate-limit hooks |
| `bin/cc-pick` | Account picker shown at launch |
| `bin/statusline.py` | HUD: account, model + effort meter, context, per-account usage, feature switches |
| `bin/cc-toggle` | Feature switches (`update`, `hib`, `swap`) shown on the HUD's third line |
| `bin/cc-hib` | Hibernate idle sessions (kill the process, keep the tab and context) |
| `bin/cc-update` | Keep Claude Code up to date before launch and swap |
| `bin/cswap_state.py` | Read-only access to claude-swap's on-disk state |
| `commands/` | `/swap`, `/sleep` slash commands |
| `shell/cc.zsh` | `cc` launcher loop (pick account, run, relaunch on swap/wake) |
| `config/cc-accounts.example.json` | Account kinds and feature settings |

## License

MIT
