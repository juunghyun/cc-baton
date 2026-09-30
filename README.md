# cc-baton

**Pass the baton between Claude Code accounts.** Switch accounts in the middle of a session and keep the whole conversation (the transcript itself, not a summary). A statusline HUD shows which account you're on, how much every account has left, and how full your context is.

macOS only · [한국어 README](README.ko.md) · Built on [claude-swap](https://github.com/realiti4/claude-swap) (MIT). cc-baton is an unofficial add-on, not affiliated with Anthropic or claude-swap.

> Pre-release. Feedback welcome in Issues.

```
● work [WORK] · Opus 5.5[1M] ▰▰▰▱▱ high ▌ Lv.60  · ctx 12% · ~/src/app (ft/login-302*)
5h ██░░░░ 31% 2h04m │ 1w █░░░░░ 11% 3d11h │ ↔ personal 0%/3%, side 12%/40% +1 more
⟳ Update ▮▮▮ · ⏾ Sleep ▮▮▮ 90m · ⇄ Auto-swap ▮▮▮ same group
```

- **Line 1:** the account you're on and its group, the model with an effort meter, context usage, folder and branch
- **Line 2:** 5-hour and weekly usage for this account, then your other accounts, most recently used first
- **Line 3:** which features are on (`cc-baton toggle`), with a warning if one is on but not actually running

## What you get

- **`/swap <account>`**: moves this conversation to another account. Press Ctrl+D and it continues there with the full transcript. It works even after you've hit your rate limit, because a hook handles it before any model call.
- **Group check**: give accounts groups (`work`, `personal`, …). Swapping across groups asks you first, so a work conversation doesn't land on a personal account by accident.
- **Account picker**: `baton` shows every account with its remaining usage, so you start on the one with room.
- **Hibernate** (optional): sessions left idle get their process stopped to free memory. The tab and the conversation stay; any key brings it back. `/sleep` does it right away.
- **Auto-swap on rate limits** (optional, off by default): when you hit the limit, a swap to an account with room is scheduled for you.

## Install

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # only if you don't have uv yet
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && ~/.local/bin/cc-baton install
```

Then open a new terminal and run **`baton`**. The first run walks you through a short setup (language, account groups, the HUD, hibernation, auto-swap) and then shows the account picker.

`cc-baton install` never asks anything and is safe to re-run. It adds the `/swap` and `/sleep` commands, a marked block in `~/.zshrc`, and its hooks in `~/.claude/settings.json` (backed up first; an invalid file is left alone). It only sets the statusline if you don't already have one; if you do, the setup wizard shows a preview and asks.

You need at least one account registered in claude-swap. If you haven't used it before: log in to Claude Code, run `cswap add`, then `/login` to your next account and `cswap add` again.

### For AI agents installing this

Run exactly:

```sh
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && ~/.local/bin/cc-baton install
```

Don't edit `~/.claude/settings.json` or `~/.zshrc` yourself. Show the user the summary that `cc-baton install` prints, then tell them to open a new terminal and run `baton` to finish setup interactively.

## Commands

| Command | What |
|---|---|
| `baton [account] [-- claude args]` | Pick an account (or name one) and run Claude Code. Relaunches on `/swap` and wake |
| `baton --wake` | Pick a hibernated session and bring it back where it was |
| `/swap <account>` (in Claude Code) | Swap to another account with the full conversation, then Ctrl+D |
| `/sleep` (in Claude Code) | Hibernate this session now |
| `cc-baton setup` | Run the setup wizard again |
| `cc-baton toggle update\|hib\|swap on\|off` | Turn a feature on or off |
| `cc-baton lang en\|ko` | Display language |
| `cc-baton upgrade` | Upgrade cc-baton and re-apply the wiring |
| `cc-baton uninstall` | Remove everything cc-baton added |

## Uninstall

```sh
cc-baton uninstall          # asks before deleting settings and the package; --purge / --yes skip the questions
```

It removes only what cc-baton added and puts back the statusline you had before. Your claude-swap account data (`~/.claude-swap-backup`) is never touched.

## Before you use it

- **Terms**: whether using several accounts around usage limits fits Anthropic's terms is for you to check. Auto-swap is off by default.
- **Work and personal accounts**: moving a work conversation to a personal account (or the reverse) may be against your company's policy. The group check is there to make you stop and think.
- **Internal formats**: cc-baton reads claude-swap's state files and parts of Claude Code's statusline input and transcript layout that aren't official APIs. An update to either can break something; cc-baton degrades to blank values instead of crashing.

Tested with macOS 15.3, Claude Code 2.1.285, claude-swap 0.25–0.26, zsh, and Warp. Needs a truecolor terminal. uv installs Python 3.12 for you.

## How it works

- `/swap` writes a request; when Claude Code exits, `baton` copies that one transcript (and its side folder of large tool outputs and subagent logs) into the target account's profile and runs `claude --resume` there. Other conversations and prompt history stay separate per account.
- The account picker, HUD and usage numbers come from claude-swap's local cache. The statusline never waits on the network: when the cache is stale it asks claude-swap to refresh in the background and shows the last numbers meanwhile.

## Development

```sh
uv tool install -e . && cc-baton install      # from a clone; edits apply immediately
uv run --group dev python -m pytest tests -q
```

Each test builds a fake `$HOME` with claude-swap state and runs cc-baton as real processes (the picker and wizard in a pseudo-terminal), so your own setup is never touched.

## License

MIT
