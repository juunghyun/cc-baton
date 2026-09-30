---
description: Put this session to sleep now; the conversation stays, only the memory is freed (cc-baton)
argument-hint: [pid|name] [--all] [--force] [--dry]
allowed-tools: Bash({cc_baton} hib:*)
---

Run `{cc_baton} hib sleep $ARGUMENTS` and show the user its output as is. Reply in the user's language.

With no argument the target is **this session**. It ends right away, with no grace period or notification, because the user asked for it explicitly.

## Hibernating yourself (no argument)

The process is about to be killed, so:

- **Before** running it, say in one line what is being put to sleep and how to come back.
- **After** running it, don't start anything. Make no more tool calls; stop there.
- The marker is written to disk before SIGTERM, so the state is intact even if this session dies mid-way.

## Exit codes

- **0** — Put to sleep (or a `--dry` preview). Relay the output as is.
- **1** — Refused; the reason is in the output. Show it as is and **never add `--force` on your own.** Re-run only if the user explicitly asks.

The refusal is one of two kinds:

- **Blockers** — a scheduled wakeup, unfinished background work, or an artifact with an active comment thread. Putting it to sleep would lose it. Tell the user what would be lost and let them decide.
- **Tab not running baton** — that tab can't show the waiting banner and drops to the shell prompt. The conversation is safe and `baton --wake` brings it back. The tab gets a notice.

## Coming back

- In a tab showing the waiting banner: **any key**
- Anywhere else: `baton --wake`

## Other targets

- `/sleep 12345` — a pid
- `/sleep workspace-3e` — a name (a prefix is enough)
- `/sleep --all` — every idle session that qualifies right now. Add minutes like `--all 60`
- `/sleep --dry` — show what would be put to sleep without doing it

If the target is ambiguous or not found, show `cc-baton hib scan` and ask the user.
