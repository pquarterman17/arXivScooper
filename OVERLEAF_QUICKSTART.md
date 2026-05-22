# Overleaf Sync - Quick Start Guide

Get your Overleaf project syncing with SCQ bibliography in 5 minutes.

## 1. Get Your Overleaf Git URL (1 min)

1. Open your Overleaf project in browser
2. Click **Menu** (top-left hamburger icon)
3. Click **Settings**
4. Find **Git** section
5. Copy the URL (it looks like: `https://git.overleaf.com/abc123def456`)

## 2. Setup Sync (1 min)

Open terminal and run:

```bash
cd /path/to/arXivScooper
python tools/overleaf_sync.py --setup https://git.overleaf.com/YOUR_PROJECT_ID
```

Replace `YOUR_PROJECT_ID` with the ID from Step 1.

You should see:
```
============================================================
SUCCESS — Overleaf sync configured
  Git URL: https://git.overleaf.com/YOUR_PROJECT_ID
  Bib file: references.bib → references.bib
  Auto-sync: enabled

Run 'python tools/overleaf_sync.py' to sync.
============================================================
```

## 3. Verify Setup (1 min)

```bash
python tools/overleaf_sync.py --status
```

Output shows:
- Your Git URL
- Number of bibliography entries
- Last sync time
- Any uncommitted changes

## 4. Test It (1 min)

Add a test paper:

```bash
python tools/fetch_arxiv.js 2311.00001
python tools/process_paper.py 2311.00001
```

Watch the output for:
```
[6/5] Auto-syncing to Overleaf...
  Overleaf sync successful
```

## 5. Verify in Overleaf (1 min)

1. Go to your Overleaf project
2. Open `references.bib`
3. You should see the new entry at the bottom

Done! Your SCQ database is now syncing with Overleaf.

---

## Common Tasks

### Manual Sync

```bash
python tools/overleaf_sync.py
```

### Disable Auto-Sync

Edit `.overleaf/config.json` and change `"auto_sync": true` to `"auto_sync": false`

Then manually run sync when needed.

### Check What Will Sync

```bash
python tools/overleaf_sync.py --status
```

### Force Sync (Even if Unchanged)

```bash
python tools/overleaf_sync.py --force
```

---

## Troubleshooting

### "authentication failed"

You need to configure Git credentials:

```bash
git config user.email "your-email@example.com"
git config user.name "Your Name"
```

If using HTTPS, you may need a Personal Access Token instead of your password.

### ".overleaf/repo directory not found"

Run setup again:

```bash
python tools/overleaf_sync.py --setup https://git.overleaf.com/YOUR_PROJECT_ID
```

### Still having issues?

1. Check detailed status:
   ```bash
   python tools/overleaf_sync.py --status
   ```

2. Read the full guide:
   ```
   OVERLEAF_SYNC_README.md
   ```

---

## What Happens When You Add a Paper

1. Run `process_paper.py <arxiv_id>`
2. Script adds paper to database
3. Updates `references.bib` locally
4. **Auto-syncs to Overleaf** (if enabled):
   - Copies `references.bib` to Overleaf clone
   - Commits with message like: "Update references.bib — 42 entries, synced 2026-04-05 14:30"
   - Pushes to your Overleaf project
5. You can now cite papers in Overleaf using the updated bibliography

---

## How to Update Bib Filename

If your Overleaf project uses a different bib filename (e.g., `references-scq.bib`):

1. Edit `.overleaf/config.json`
2. Change `"bib_filename": "references.bib"` to `"bib_filename": "references-scq.bib"`
3. Create the file in Overleaf (via web editor, add empty file)
4. Run sync:
   ```bash
   python tools/overleaf_sync.py
   ```

---

## File Locations

| File | Purpose |
|------|---------|
| `tools/overleaf_sync.py` | Main sync script |
| `.overleaf/config.json` | Configuration (created by setup) |
| `.overleaf/repo/` | Local clone of Overleaf project |
| `references.bib` | Source bibliography file |

---

## Next Steps

1. Set up in Overleaf (this guide)
2. Add papers normally with `process_paper.py`
3. Auto-sync happens automatically
4. Cite from `references.bib` in Overleaf

For advanced features, see `OVERLEAF_SYNC_README.md`.

---

**Time to setup**: ~5 minutes
**One-time setup**: Yes, never needs to be done again
**Auto-sync**: Enabled by default, runs on every paper import
