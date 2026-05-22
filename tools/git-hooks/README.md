# Git Hooks

Shared git hooks for arXivScooper. Lives in the repo (not in
`.git/hooks/`) so the hook script travels with checkouts. Each clone
has to opt in once:

```bash
git config core.hooksPath tools/git-hooks
```

Run that command from the repo root after cloning. To turn the hooks off
again:

```bash
git config --unset core.hooksPath
```

## What's here

### `pre-push`

Runs before every `git push`. Three checks:

1. **vitest** — only if `src/` or `package.json` changed
2. **pytest** — only if `scq/`, `tests/`, or `pyproject.toml` changed
3. **secret scan** — quick grep for AWS keys, GitHub tokens, OpenAI keys,
   private key headers in changed files

Skips automatically on file types that don't affect those stacks
(docs-only pushes exit in <1s). Tests that fail block the push;
warnings (like "npm not installed") let the push through.

To bypass in a hurry: `git push --no-verify`. Don't make a habit of it.

The hook compares against `origin/main`, so make sure your local
`main` ref is reasonably fresh — `git fetch origin` if in doubt.
