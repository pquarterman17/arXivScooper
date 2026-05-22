# Overleaf Sync Feature

Auto-push `references.bib` to an Overleaf-linked Git repository when papers are added to the SCQ database.

## Overview

When enabled, the Overleaf sync feature automatically:
1. Detects when papers are added via `process_paper.py`
2. Copies the updated `references.bib` to a local Overleaf Git clone
3. Commits and pushes changes to your Overleaf project
4. Updates sync metadata in `.overleaf/config.json`

This keeps your Overleaf project's bibliography in sync with the SCQ database.

## Requirements

- **Overleaf Plan**: Premium or Institutional (Git access required)
- **Git**: Installed and in PATH
- **Python 3.7+**: For the sync script

## Setup

### Step 1: Get Your Overleaf Git URL

1. Open your Overleaf project
2. Click **Menu** → **Settings** → **Git**
3. Copy the Git URL (looks like `https://git.overleaf.com/abc123def456`)

### Step 2: Initialize Overleaf Sync

Run the setup command:

```bash
cd /path/to/References\ -\ Claude\ v0p1\ Build
python tools/overleaf_sync.py --setup https://git.overleaf.com/YOUR_PROJECT_ID
```

This will:
- Create `.overleaf/` directory
- Clone your Overleaf repo to `.overleaf/repo/`
- Save configuration to `.overleaf/config.json`
- Add `.overleaf/` to `.gitignore`

Example output:
```
============================================================
Overleaf Sync — Setup
============================================================

Created .overleaf/

[1/4] Cloning Overleaf Git repo...
  Cloned to .overleaf/repo/

[2/4] Saving configuration...
  Config saved to .overleaf/config.json

[3/4] Updating .gitignore...
  Added .overleaf/ to .gitignore

[4/4] Verifying setup...
  Setup complete!

============================================================
SUCCESS — Overleaf sync configured
  Git URL: https://git.overleaf.com/YOUR_PROJECT_ID
  Bib file: references.bib → references.bib
  Auto-sync: enabled

Run 'python tools/overleaf_sync.py' to sync.
============================================================
```

### Step 3: Configure in Settings (Optional)

In the app's Settings modal:
1. Navigate to **Overleaf Integration** section
2. Paste your Git URL (from Step 1)
3. Confirm bib filename (`references.bib`)
4. Enable "Auto-sync when papers are added"
5. Click **Save**

## Usage

### Auto-sync (Default)

When `auto_sync` is enabled in `.overleaf/config.json`:
- Every time you add a paper via `process_paper.py`, the script automatically syncs to Overleaf
- Status is printed to console

Example:
```
[6/5] Auto-syncing to Overleaf...
  Overleaf sync successful
```

### Manual Sync

Sync at any time:

```bash
python tools/overleaf_sync.py
```

Output:
```
============================================================
Overleaf Sync — Syncing
============================================================

[1/4] Copying references.bib...
  Copied to .overleaf/repo/references.bib

[2/4] Staging file in Git...
  Staged references.bib

[3/4] Committing changes...
  Committed: Update references.bib — 42 entries, synced 2026-04-05 14:30

[4/4] Pushing to Overleaf...
  Pushed to Overleaf

============================================================
SUCCESS — Synced 42 citations to Overleaf
  Last sync: 2026-04-05T14:30:00.123456
============================================================
```

### Check Sync Status

```bash
python tools/overleaf_sync.py --status
```

Output:
```
============================================================
Overleaf Sync — Status
============================================================

Configuration:
  Git URL:         https://git.overleaf.com/abc123def456
  Bib filename:    references.bib
  Auto-sync:       enabled

Database:
  Total entries:   42
  Bib file:        /path/to/arXivScooper/references.bib

Sync History:
  Last sync:       2026-04-05T14:30:00.123456

No uncommitted changes in .overleaf/repo/

============================================================
```

### Force Sync (Even if Unchanged)

```bash
python tools/overleaf_sync.py --force
```

## Configuration File

Stored at `.overleaf/config.json`:

```json
{
  "git_url": "https://git.overleaf.com/abc123def456",
  "bib_filename": "references.bib",
  "last_sync": "2026-04-05T14:30:00.123456",
  "auto_sync": true,
  "created_at": "2026-04-05T10:00:00.000000"
}
```

**Key fields:**
- `git_url`: Your Overleaf Git repository URL
- `bib_filename`: Filename in Overleaf repo (usually `references.bib`)
- `last_sync`: Timestamp of last successful sync
- `auto_sync`: Whether to auto-sync on `process_paper.py` runs
- `created_at`: When sync was configured

## Troubleshooting

### Git Authentication Errors

If you see: `ERROR: Failed to push: ... authentication failed ...`

**Solution:**
1. Ensure your Overleaf account has Git access (Premium/Institutional plan required)
2. Verify Git credentials are configured:
   ```bash
   git config user.email "your-email@example.com"
   git config user.name "Your Name"
   ```
3. For SSH: Ensure SSH key is added to your GitHub/Overleaf account
4. For HTTPS: You may need to use a Personal Access Token instead of password

Manual push test:
```bash
cd .overleaf/repo
git push
```

### "No changes to push"

This is normal. The script checks if `references.bib` changed before committing. If you see this and there should be new papers, run:

```bash
python tools/overleaf_sync.py --status
```

to verify the entry count matches your database.

### Permission Denied on `.overleaf/repo`

If you see file permission errors:

```bash
# On macOS/Linux
chmod -R u+w .overleaf/repo

# Then retry
python tools/overleaf_sync.py
```

### Sync Script Not Found

If `process_paper.py` can't find the sync script:
- Ensure `tools/overleaf_sync.py` exists
- Run from the project root: `python tools/process_paper.py <arxiv_id>`

### Network/Connectivity Issues

- Check internet connection
- Verify Overleaf status at https://status.overleaf.com
- Check arXiv API availability (sometimes slow/offline)

## Integration with process_paper.py

The `process_paper.py` script automatically triggers sync at the end if:
1. `.overleaf/config.json` exists
2. `auto_sync` is `true`

The sync runs in a subprocess with 60-second timeout. If it fails, a warning is printed but the paper import completes successfully.

## Disabling Auto-Sync

Edit `.overleaf/config.json` and set:

```json
{
  "auto_sync": false
}
```

Then manually run `python tools/overleaf_sync.py` when needed.

## Security Notes

- `.overleaf/repo/` is added to `.gitignore` to avoid committing the cloned Overleaf repo
- Git credentials are stored per-user in Git's credential helper or SSH keys
- Never commit `.overleaf/` to your own Git repository
- The script does NOT store your Overleaf password anywhere

## File Structure

```
arXivScooper/
├── tools/
│   ├── overleaf_sync.py          # Main sync script
│   └── process_paper.py           # (auto-calls overleaf_sync.py)
├── .overleaf/                     # (Created by setup)
│   ├── config.json                # Configuration
│   ├── repo/                       # Local clone of Overleaf Git repo
│   │   ├── references.bib         # Synced bib file
│   │   ├── .git/                  # Git metadata
│   │   └── [other Overleaf files]
│   └── .gitignore
├── references.bib                 # Source bibliography file
└── OVERLEAF_SYNC_README.md        # This file
```

## Command Reference

| Command | Purpose |
|---------|---------|
| `python tools/overleaf_sync.py --setup <url>` | Initial setup: clone repo and create config |
| `python tools/overleaf_sync.py` | Sync (default): push changes if any |
| `python tools/overleaf_sync.py --status` | Show configuration and last sync time |
| `python tools/overleaf_sync.py --force` | Force sync even if no changes |

## Version History

- **v1.0** (2026-04-05): Initial release with auto-sync, setup, status, and force options

## Questions or Issues?

Refer to the logs in:
- Console output from `process_paper.py`
- Manual run output from `python tools/overleaf_sync.py`
- Check `.overleaf/config.json` for configuration
