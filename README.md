<div align="center">

<img src="docs/assets/logo.svg" alt="" width="96" />

# cc-baton

**Move a Claude Code conversation between your own accounts. Keep every word of it.**

[![CI](https://github.com/juunghyun/cc-baton/actions/workflows/ci.yml/badge.svg)](https://github.com/juunghyun/cc-baton/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8250DF?style=flat-square)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-57606A?style=flat-square&logo=apple)](#install)

English | [한국어](README.ko.md)

</div>

Your team seat and your personal plan are separate Claude accounts, and a conversation normally stays in the one it started in. Type `/swap personal`, press Ctrl+D, and the same conversation continues on your other account, transcript and all. A statusline HUD shows which account you're on, so work doesn't end up on the wrong one.

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

**Always know which account you're on.** Three lines under the prompt show the account and its group, each account's usage, and which features are on.

![What each part of the HUD means](docs/assets/hud.png)

**Hard to swap the wrong way.** Give accounts groups such as `work` and `personal`. Moving a conversation to another group asks you first.

**Idle tabs stop eating memory.** Optional: a session left idle has its process stopped, and any key brings it back with the conversation intact. `/sleep` does it right away.

**Switch for you when an account runs out.** Optional and off by default. When one of your accounts hits its usage limit, cc-baton schedules a swap to another of your accounts, so pressing Ctrl+D is all it takes.

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

- **Your own accounts only.** Sharing an account or its login with anyone else is not allowed under Anthropic's [Consumer Terms](https://www.anthropic.com/legal/consumer-terms).
- **Each account keeps its own limits.** cc-baton only changes which of your accounts Claude Code uses; it doesn't raise or reset any limit. Auto-swap is off by default. Read Anthropic's [Usage Policy](https://www.anthropic.com/legal/aup) and [Claude Code terms](https://code.claude.com/docs/en/legal-and-compliance) and decide for yourself before turning it on.
- **Work accounts follow your company's rules.** Team and Enterprise seats are covered by your organization's agreement. Moving a work conversation to a personal account may be against its policy; the group check asks before doing it.
- **Your login stays with Claude Code.** cc-baton runs the unmodified Claude Code and never reads or stores your credentials. Switching logins on your machine is done by claude-swap.
- cc-baton reads claude-swap's state files and parts of Claude Code that aren't official APIs. Tested with macOS 15.3, Claude Code 2.1.285 and claude-swap 0.25–0.26.

## More

![Account picker, HUD at each effort level, and the group check](docs/demo.gif)

Development: `uv tool install -e . && cc-baton install` from a clone, tests with `uv run --group dev python -m pytest tests -q`.

## Author's note

I work across a team seat and a personal plan every day. Every time a task moved from one account to the other, the conversation stayed behind and I had to explain everything again. cc-baton is the smallest thing I could build that keeps the thread.

cc-baton is an unofficial add-on, not affiliated with or endorsed by Anthropic or claude-swap. Claude and Claude Code are products of Anthropic. MIT licensed.
