<div align="center">

<img src="docs/assets/logo.svg" alt="" width="96" />

# cc-baton

**Switch Claude Code accounts mid-task. Keep every word of the conversation.**

[![CI](https://github.com/juunghyun/cc-baton/actions/workflows/ci.yml/badge.svg)](https://github.com/juunghyun/cc-baton/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8250DF?style=flat-square)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-57606A?style=flat-square&logo=apple)](#install)

English | [한국어](README.ko.md)

</div>

You hit the 5-hour limit halfway through a refactor. Type `/swap personal`, press Ctrl+D, and the same conversation continues on your other account, transcript and all. A statusline HUD shows which account you're on and how much each one has left.

![Swapping accounts in Claude Code: the conversation continues on the other account](docs/demo-swap.gif)

## Install

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # only if you don't have uv
uv tool install --python 3.12 git+https://github.com/juunghyun/cc-baton && ~/.local/bin/cc-baton install
```

Open a new terminal and run **`baton`**. The first run asks a few questions (language, account groups, HUD, hibernation, auto-swap), then shows the account picker. You need at least one account in [claude-swap](https://github.com/realiti4/claude-swap), which comes bundled: log in to Claude Code and run `cswap add`.

`cc-baton install` never asks anything and is safe to re-run. It backs up `~/.claude/settings.json` before touching it and never replaces a statusline you already have without asking.

<details>
<summary>Installing with an AI agent?</summary>

Run exactly the two lines above (skip the first if `uv --version` works). Don't edit `~/.claude/settings.json` or `~/.zshrc` yourself. Show the user what `cc-baton install` printed and tell them to open a new terminal and run `baton` to finish setup.

</details>

## Why cc-baton

**The whole conversation moves, not a summary.** The transcript file itself is copied to the other account and resumed there, along with its large tool outputs and subagent logs. Your other conversations stay where they were.

**Always know which account you're on.** Three lines under the prompt show the account and its group, the usage left on every account, and which features are on.

![What each part of the HUD means](docs/assets/hud.png)

**Hard to swap the wrong way.** Give accounts groups such as `work` and `personal`. Moving a conversation to another group asks you first.

**Idle tabs stop eating memory.** Optional: a session left idle has its process stopped, and any key brings it back with the conversation intact. `/sleep` does it right away.

**Rate-limit autopilot, if you want it.** Off by default. When on, hitting a limit schedules a swap to an account that still has room.

## Commands

| Command | What it does |
|---|---|
| `baton` | Pick an account and start Claude Code (`baton work` to name one) |
| `/swap <account>` | Move this conversation to another account, then press Ctrl+D |
| `/sleep` | Hibernate this session now |
| `baton --wake` | Bring back a hibernated session |
| `cc-baton toggle update\|hib\|swap on\|off` | Turn a feature on or off |
| `cc-baton setup` · `lang en\|ko` · `upgrade` | Setup again · display language · update cc-baton |

## Uninstall

```sh
cc-baton uninstall
```

Removes only what cc-baton added and puts back the statusline you had. Your claude-swap account data (`~/.claude-swap-backup`) is never touched.

## Before you use it

- Whether using several accounts around usage limits fits Anthropic's terms is for you to check.
- Moving work conversations to a personal account (or the reverse) may be against your company's policy.
- cc-baton reads claude-swap's state files and parts of Claude Code that aren't official APIs. Tested with macOS 15.3, Claude Code 2.1.285 and claude-swap 0.25–0.26.

## More

![Account picker, HUD at each effort level, and the group check](docs/demo.gif)

Development: `uv tool install -e . && cc-baton install` from a clone, tests with `uv run --group dev python -m pytest tests -q`.

## Author's note

I switch between a team seat and a personal plan every day. Each time one ran out in the middle of a task, I lost the thread and had to explain everything again. cc-baton is the smallest thing I could build that keeps the thread.

cc-baton is an unofficial add-on, not affiliated with Anthropic or claude-swap. MIT licensed.
