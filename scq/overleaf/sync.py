#!/usr/bin/env python3
"""
Overleaf Sync — Push references.bib to an Overleaf Git repository.

Usage:
  python tools/overleaf_sync.py                    # sync using saved config
  python tools/overleaf_sync.py --setup <git-url>  # initial setup
  python tools/overleaf_sync.py --status            # check sync status
  python tools/overleaf_sync.py --force              # force push even if no changes

Config is stored in .overleaf/config.json
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────
# This module lives at scq/overleaf/sync.py; PROJECT_DIR is two levels up.
# references.bib resolves through scq.config.paths so user_config/paths.toml
# overrides take effect (the bib file moved to OneDrive in the externalize
# commit). The .overleaf/ working dir stays in the repo because it's a
# git clone of the user's Overleaf project — not user research data.
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parents[1]
OVERLEAF_DIR = PROJECT_DIR / ".overleaf"
REPO_DIR = OVERLEAF_DIR / "repo"
CONFIG_PATH = OVERLEAF_DIR / "config.json"
try:
    from scq.config.paths import paths as _scq_paths  # type: ignore[import-not-found]

    BIB_PATH = Path(_scq_paths().references_bib_path)
except Exception:
    BIB_PATH = PROJECT_DIR / "references.bib"
GITIGNORE_PATH = PROJECT_DIR / ".gitignore"


# ─── Config management ────────────────────────────────────────────


def load_config():
    """Load config from .overleaf/config.json, return None if not exists."""
    if not CONFIG_PATH.exists():
        return None
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}")
        return None


def save_config(config):
    """Save config to .overleaf/config.json."""
    OVERLEAF_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def setup_mode(git_url):
    """Setup: Clone Overleaf repo and save config."""
    print(f"\n{'=' * 60}")
    print("Overleaf Sync — Setup")
    print(f"{'=' * 60}")

    # Create .overleaf directory
    OVERLEAF_DIR.mkdir(parents=True, exist_ok=True)
    print("\nCreated .overleaf/")

    # Clone the Overleaf repo
    print("\n[1/4] Cloning Overleaf Git repo...")
    if REPO_DIR.exists():
        print("  Removing existing repo...")
        import shutil

        shutil.rmtree(REPO_DIR)

    result = subprocess.run(
        ["git", "clone", git_url, str(REPO_DIR)], capture_output=True, text=True
    )
    if result.returncode != 0:
        print("ERROR: Failed to clone repo:")
        print(result.stderr)
        sys.exit(1)
    print("  Cloned to .overleaf/repo/")

    # Save config
    print("\n[2/4] Saving configuration...")
    config = {
        "git_url": git_url,
        "bib_filename": "references.bib",
        "last_sync": None,
        "auto_sync": True,
        "created_at": datetime.now().isoformat(),
    }
    save_config(config)
    print("  Config saved to .overleaf/config.json")

    # Add .overleaf/ to .gitignore
    print("\n[3/4] Updating .gitignore...")
    gitignore_text = GITIGNORE_PATH.read_text() if GITIGNORE_PATH.exists() else ""
    if ".overleaf/" not in gitignore_text:
        with open(GITIGNORE_PATH, "a") as f:
            f.write("\n.overleaf/\n")
        print("  Added .overleaf/ to .gitignore")
    else:
        print("  .overleaf/ already in .gitignore")

    # Verify setup
    print("\n[4/4] Verifying setup...")
    if CONFIG_PATH.exists() and REPO_DIR.exists():
        print("  Setup complete!")
    else:
        print("ERROR: Setup incomplete")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print("SUCCESS — Overleaf sync configured")
    print(f"  Git URL: {git_url}")
    print(f"  Bib file: references.bib → {config['bib_filename']}")
    print("  Auto-sync: enabled")
    print("\nRun 'python tools/overleaf_sync.py' to sync.")
    print(f"{'=' * 60}\n")


def sync_mode(force=False):
    """Sync mode: Push references.bib to Overleaf repo."""
    config = load_config()
    if not config:
        print("\nERROR: .overleaf/config.json not found.")
        print("Run: python tools/overleaf_sync.py --setup <git-url>")
        sys.exit(1)

    if not REPO_DIR.exists():
        print("\nERROR: .overleaf/repo/ not found.")
        print("Re-run setup: python tools/overleaf_sync.py --setup <git-url>")
        sys.exit(1)

    if not BIB_PATH.exists():
        print(f"\nERROR: references.bib not found at {BIB_PATH}")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print("Overleaf Sync — Syncing")
    print(f"{'=' * 60}")

    # Copy references.bib to repo
    bib_filename = config.get("bib_filename", "references.bib")
    target_bib = REPO_DIR / bib_filename
    print("\n[1/4] Copying references.bib...")
    import shutil

    shutil.copy(BIB_PATH, target_bib)
    print(f"  Copied to .overleaf/repo/{bib_filename}")

    os.chdir(REPO_DIR)
    # Stage the file
    print("\n[2/4] Staging file in Git...")
    subprocess.run(["git", "add", bib_filename], capture_output=True)
    print(f"  Staged {bib_filename}")

    # Check for changes again
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    has_changes = result.returncode != 0

    if not has_changes and not force:
        print(f"\n{'=' * 60}")
        print("Already up to date — no changes to push")
        print(f"{'=' * 60}\n")
        return

    # Count entries in bib file
    bib_text = BIB_PATH.read_text()
    entry_count = len(re.findall(r"^@\w+\{", bib_text, re.MULTILINE))

    # Commit
    print("\n[3/4] Committing changes...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"Update references.bib — {entry_count} entries, synced {now}"
    result = subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Warning: {result.stderr.strip()}")
    else:
        print(f"  Committed: {commit_msg}")

    # Push
    print("\n[4/4] Pushing to Overleaf...")
    result = subprocess.run(["git", "push"], capture_output=True, text=True)
    if result.returncode != 0:
        print("\nERROR: Failed to push:")
        print(result.stderr)
        print("\nTroubleshooting:")
        print("  1. Check your Overleaf Git credentials")
        print("  2. Ensure you have push access to the repo")
        print("  3. Check if there are uncommitted changes: cd .overleaf/repo && git status")
        sys.exit(1)

    print("  Pushed to Overleaf")

    # Update config
    config["last_sync"] = datetime.now().isoformat()
    save_config(config)

    print(f"\n{'=' * 60}")
    print(f"SUCCESS — Synced {entry_count} citations to Overleaf")
    print(f"  Last sync: {config['last_sync']}")
    print(f"{'=' * 60}\n")


def status_mode():
    """Status mode: Show sync configuration and status."""
    config = load_config()
    if not config:
        print("\nERROR: .overleaf/config.json not found.")
        print("Run: python tools/overleaf_sync.py --setup <git-url>")
        sys.exit(1)

    # Count bib entries
    entry_count = 0
    if BIB_PATH.exists():
        bib_text = BIB_PATH.read_text()
        entry_count = len(re.findall(r"^@\w+\{", bib_text, re.MULTILINE))

    print(f"\n{'=' * 60}")
    print("Overleaf Sync — Status")
    print(f"{'=' * 60}\n")
    print("Configuration:")
    print(f"  Git URL:         {config.get('git_url', 'N/A')}")
    print(f"  Bib filename:    {config.get('bib_filename', 'references.bib')}")
    print(f"  Auto-sync:       {'enabled' if config.get('auto_sync', False) else 'disabled'}")
    print("\nDatabase:")
    print(f"  Total entries:   {entry_count}")
    print(f"  Bib file:        {BIB_PATH}")
    print("\nSync History:")
    if config.get("last_sync"):
        print(f"  Last sync:       {config['last_sync']}")
    else:
        print("  Last sync:       never")

    # Check uncommitted changes in repo
    if REPO_DIR.exists():
        os.chdir(REPO_DIR)
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if result.stdout.strip():
            print("\nUncommitted changes in .overleaf/repo/:")
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")
        else:
            print("\nNo uncommitted changes in .overleaf/repo/")

    print(f"\n{'=' * 60}\n")


# ─── Main ─────────────────────────────────────────────────────────


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--setup":
            if len(sys.argv) < 3:
                print("Usage: python tools/overleaf_sync.py --setup <git-url>")
                sys.exit(1)
            git_url = sys.argv[2]
            setup_mode(git_url)
        elif sys.argv[1] == "--status":
            status_mode()
        elif sys.argv[1] == "--force":
            sync_mode(force=True)
        else:
            print(f"Unknown option: {sys.argv[1]}")
            print("Usage:")
            print("  python tools/overleaf_sync.py                    # sync")
            print("  python tools/overleaf_sync.py --setup <git-url>  # setup")
            print("  python tools/overleaf_sync.py --status            # status")
            print("  python tools/overleaf_sync.py --force              # force sync")
            sys.exit(1)
    else:
        sync_mode()


if __name__ == "__main__":
    main()
