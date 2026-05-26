# Git hooks

Versioned git hooks for arXivScooper. They live here (not in `.git/hooks/`,
which isn't tracked) so they're reviewable and shared across machines.

## Enable (once per clone, on each machine)

```bash
git config core.hooksPath tools/hooks
```

That's the only setup step. It's a local config value, so each fresh
clone (your PC, the MacBook, a CI checkout) runs it once.

## What's here

- **`pre-commit`** — shell shim git invokes before each commit. Delegates to:
- **`pre_commit_secret_scan.py`** — blocks the commit if a secret would be
  committed. Two layers:
  1. **Known-value scan** — reads each registered secret from the OS
     keyring/env via `scq.config.secrets` and checks whether its *literal
     value* appears in any staged file. Detects your actual API key
     directly. The value is never printed — only the file and which
     secret matched.
  2. **Pattern scan** — flags a hardcoded PatentsView/SCQ key literal,
     ignoring obvious placeholders (so `.example` templates and tests
     don't trip it).

Registered secrets are listed in `REGISTERED_SECRETS` in the scanner;
add new ones there as the project grows.

## Bypass (discouraged)

```bash
git commit --no-verify
```

Use only for a confirmed false positive. The scanner prints why it
fired so you can tell.
