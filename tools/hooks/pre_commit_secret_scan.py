#!/usr/bin/env python3
"""Pre-commit secret tripwire for arXivScooper.

Blocks a commit if a real secret value (or an obvious hardcoded API key)
would be committed into a tracked file. This is a belt-and-suspenders
guard on top of the keyring design — secrets are *supposed* to live only
in the OS keyring / env vars, never in the repo, and this catches the
"oops I pasted it into a file while debugging" case.

Two detection layers, both scanning the *staged* blob (what git would
actually commit), not the working tree:

  1. Known-value scan (primary): read each registered secret from the
     keyring/env via scq.config.secrets and search staged content for its
     literal value. This detects YOUR actual key directly. The value is
     never printed — only the file name and which secret matched.

  2. Pattern scan (backup): flag a quoted literal assigned to a
     PatentsView/SCQ key name, ignoring obvious placeholders. Kept tight
     to avoid false positives on .example templates and tests.

Exit codes: 0 = clean, 1 = secret found (commit blocked), 2 = scanner error.
Bypass (discouraged): ``git commit --no-verify``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Secret names to look up and scan for by literal value. Add new secrets
# here as the project grows (e.g. overleaf tokens).
REGISTERED_SECRETS = ("patentsview_api_key", "email_app_password")

# Files we never scan (binary-ish or self). Matched against the repo-
# relative POSIX path.
SKIP_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".woff", ".woff2")
SKIP_PATHS = {"tools/hooks/pre_commit_secret_scan.py"}

# Backup pattern: an assignment of a quoted value to a key-ish name.
_PATTERN = re.compile(
    r"""(?ix)
    (patentsview[_-]?api[_-]?key | scq_patentsview_api_key | x-api-key)
    \s* [:=] \s*
    ['"]?(?P<val>[^'"\s]{16,})['"]?
    """
)

# Strings that mark a value as a placeholder, not a real secret.
_PLACEHOLDER_HINTS = (
    "your",
    "xxx",
    "example",
    "placeholder",
    "changeme",
    "<",
    "...",
    "abc123",
    "test",
    "dummy",
    "redacted",
)


def _staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _staged_blob(path: str) -> str | None:
    """Return the staged (index) content of a file, or None if unreadable."""
    res = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    if res.returncode != 0:
        return None
    try:
        return res.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None  # binary — nothing to scan


def _known_secret_values() -> dict[str, str]:
    """Map secret-name → value for any registered secret that resolves.

    Best-effort: if scq isn't importable (running outside the venv) we
    skip the known-value layer and rely on the pattern layer.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from scq.config import secrets as secrets_mod
    except Exception:
        return {}
    found: dict[str, str] = {}
    for name in REGISTERED_SECRETS:
        try:
            val = secrets_mod.get(name)
        except Exception:
            val = None
        if val and len(val) >= 8:  # ignore trivially short / empty
            found[name] = val
    return found


def _is_placeholder(value: str) -> bool:
    low = value.lower()
    if len(set(value)) <= 2:  # e.g. "xxxxxxxxxxxxxxxx"
        return True
    return any(h in low for h in _PLACEHOLDER_HINTS)


def main() -> int:
    try:
        files = _staged_files()
    except subprocess.CalledProcessError as e:
        print(f"secret-scan: could not list staged files ({e})", file=sys.stderr)
        return 2

    known = _known_secret_values()
    findings: list[str] = []

    for path in files:
        posix = path.replace("\\", "/")
        if posix in SKIP_PATHS or posix.lower().endswith(SKIP_SUFFIXES):
            continue
        content = _staged_blob(path)
        if not content:
            continue

        # Layer 1: literal known-secret value (never print the value).
        for name, value in known.items():
            if value in content:
                findings.append(f"  {posix}: contains the literal value of secret '{name}'")

        # Layer 2: hardcoded key pattern, ignoring placeholders.
        for m in _PATTERN.finditer(content):
            val = m.group("val")
            if not _is_placeholder(val):
                line = content[: m.start()].count("\n") + 1
                findings.append(
                    f"  {posix}:{line}: looks like a hardcoded API key "
                    f"('{m.group(1)}' = a {len(val)}-char literal)"
                )

    if findings:
        print("\n✖ COMMIT BLOCKED — possible secret in staged changes:\n", file=sys.stderr)
        for f in dict.fromkeys(findings):  # dedupe, preserve order
            print(f, file=sys.stderr)
        print(
            "\nSecrets must live in the OS keyring, not the repo. Move it with:\n"
            "    scq config set-secret <name>\n"
            "then remove the literal from the file. If this is a false positive,\n"
            "bypass with 'git commit --no-verify' (use sparingly).",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
