# cc-baton

Pass the baton between Claude Code accounts: switch accounts mid-session with the full conversation (the transcript itself, not a summary), and see which account you're on, how much every account has left, and your context usage in a statusline HUD.

macOS only. Built on [claude-swap](https://github.com/realiti4/claude-swap) (MIT).

> Work in progress (pre-release).

## Install

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # only if you don't have uv yet
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && ~/.local/bin/cc-baton install
```

Then open a new terminal and run **`baton`**. The first run walks you through a short setup
(account groups, the HUD, hibernation, auto-swap on rate limits) and then shows the account picker.

`cc-baton install` never asks anything and is safe to re-run. It adds the `/swap` and `/sleep` commands,
a marked block in `~/.zshrc`, and its hooks to `~/.claude/settings.json` (backed up first; an invalid file is left alone).
It only sets the statusline if you don't have one; if you do, the setup wizard asks.

### For AI agents installing this

Run exactly:

```sh
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && ~/.local/bin/cc-baton install
```

Do not edit `~/.claude/settings.json` or `~/.zshrc` yourself. Show the user the summary that `cc-baton install`
prints, then tell them to open a new terminal and run `baton` to finish setup interactively.

## Uninstall

```sh
cc-baton uninstall          # asks before deleting settings and the package; --purge / --yes to skip questions
```

Removes only what cc-baton added and puts back the statusline you had before.
Your claude-swap account data (`~/.claude-swap-backup`) is never touched.

## Commands

| Command | What |
|---|---|
| `baton [account] [-- claude args]` | Pick an account (or name one) and run Claude Code; relaunches on `/swap` and wake |
| `/swap <account>` (in Claude Code) | Swap to another account with the full conversation; press Ctrl+D to continue there |
| `/sleep` (in Claude Code) | Hibernate this session now; any key in the tab brings it back |
| `cc-baton setup` | Re-run the setup wizard |
| `cc-baton toggle update\|hib\|swap on\|off` | Feature switches shown on the HUD's third line |
| `cc-baton upgrade` | Upgrade cc-baton and re-apply the Claude Code wiring |
| `cc-baton uninstall` | Remove everything cc-baton added |

Internals: `swap`, `pick`, `hib`, `statusline`, `claude-update`, `cswap` (the bundled claude-swap).

Requires macOS, zsh, and a truecolor terminal. uv installs Python 3.12 for you.

## Tests

```sh
uv run --group dev python -m pytest tests -q
```

Dev install from a clone: `uv tool install -e . && cc-baton install`.

Each test builds a fake `$HOME` with claude-swap state and runs the scripts as real processes, so your own setup is never touched.

## License

MIT
