---
description: Switch this conversation to another Claude account with its full context (cc-baton)
argument-hint: [account number|alias] [--yes]
allowed-tools: Bash({cc_baton} swap:*)
---

> Normally the `UserPromptSubmit` hook (`cc-baton swap hook prompt`) handles `/swap` **before any model call** and blocks the prompt, so it works even in a session that has hit its 5h limit. If this instruction reached you, the hook failed; use the fallback below.

Run `{cc_baton} swap request $ARGUMENTS` and show the user its output as is. Reply in the user's language.

By exit code:
- **0** — Swap scheduled. Tell the user to press `Ctrl+D` to continue on the new account, then **stop**. Don't start any other work (the process is about to exit).
- **2** — The target is in a different account group and needs approval. Show the warning as is and wait for the user. Re-run with `--yes` only if the user explicitly says to go ahead. **Never add `--yes` on your own.**
- **3** — Not running inside `baton`, so it can't relaunch automatically. Relay the two manual commands it printed.
- **1** — Bad argument. Running `/swap` with no argument lists the accounts.

With no argument it only lists accounts. In that case show the list and ask which account to switch to.
