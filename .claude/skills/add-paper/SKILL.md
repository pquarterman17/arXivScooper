---
name: add-paper
description: "Add a paper to the SCQ database from an arXiv ID. Use this skill whenever the user says 'add paper', 'add this paper', gives you an arXiv ID (like 2401.12345), shares an arXiv URL, says 'fetch paper', or asks to import a paper. This skill handles the full pipeline: fetching metadata + PDF from arXiv, processing into the database, and optionally enriching with a summary. Even if the user just pastes an arXiv link with no other context, use this skill."
---

# Add Paper

This skill runs the full pipeline to add an arXiv paper to the arXivScooper database. It's a two-step process because the Cowork sandbox can't reach arxiv.org directly — Step 1 runs on the host machine via Desktop Commander, Step 2 runs in the sandbox.

## Path Setup

The project root in the sandbox is wherever the user's workspace folder is mounted. Find it with:
```bash
# The mount point follows this pattern — look for the folder containing data/arxiv_scooper.db
find /sessions -name "arxiv_scooper.db" -path "*/mnt/*/data/*" 2>/dev/null | head -1 | xargs dirname | xargs dirname
```
Store the result as `PROJECT_ROOT` and use it throughout.

On the host machine (for Desktop Commander), the project lives at:
- **Windows:** `C:\Users\patri\OneDrive\Coding\git\arXivScooper`
- **macOS:** Check OneDrive or iCloud sync path — likely `~/OneDrive/Coding/git/arXivScooper`

The `papers/`, `figures/`, and `inbox/` subdirectories are Windows directory junctions that point into `OneDrive\Work and School Research\arXivScooper\`. From the code's perspective they behave like normal subfolders.

## Extracting the arXiv ID

The user might give you any of these formats — normalize to just the numeric ID:
- `2401.12345` → `2401.12345`
- `2401.12345v2` → `2401.12345` (strip version suffix)
- `https://arxiv.org/abs/2401.12345` → `2401.12345`
- `https://arxiv.org/pdf/2401.12345` → `2401.12345`

## Step 1: Fetch (Host Machine via Desktop Commander)

This downloads the PDF and saves metadata JSON to `inbox/`.

### Windows
```
C:\Users\patri\AppData\Local\Temp\run_fetch.bat <arxiv_id>
```
Use `shell: "cmd"` with Desktop Commander — PowerShell swallows stdout.

If `run_fetch.bat` doesn't exist yet, create it first:
```bat
@echo off
"C:\Program Files\nodejs\node.exe" "C:\Users\patri\OneDrive\Coding\git\arXivScooper\tools\fetch_arxiv.js" %*
```

### macOS / Linux
```bash
cd "<project_path>"
bash tools/fetch.sh <arxiv_id>
```

**What to expect:** The script prints the paper title, authors, and confirms the PDF was saved. If you see a 429 error, wait a minute and retry — arXiv rate-limits.

## Step 2: Process (Sandbox)

```bash
cd "$PROJECT_ROOT"
python3 tools/process_paper.py <arxiv_id> --note "optional user note"
```

This automatically:
1. Reads the metadata JSON from `inbox/`
2. Extracts figures and captions from the PDF
3. Generates BibTeX and plain-text citations
4. Auto-tags based on arXiv categories + keyword matching
5. Inserts everything into the SQLite database at `data/arxiv_scooper.db`
6. Appends to `references.bib` and `references.txt` (resolved through `paths.toml` — typically in `OneDrive/arXivScooper/citations/`)

If the user provided a note (e.g., "interesting T1 results"), pass it with `--note`.

## Step 3: Confirm and Offer Enrichment

After processing, tell the user what was added (title, authors, tags) and ask if they'd like you to:
- Read the paper and write a better summary
- Extract key results
- Identify the research group

If they say yes, switch to the **enrich-paper** skill workflow.

## Adding Multiple Papers

If the user gives you several arXiv IDs at once, run all the fetch commands first (they can go in sequence via Desktop Commander), then process them all in the sandbox.

## Troubleshooting

- **"run_fetch.bat not found"**: Create it at the Temp path shown above
- **PDF download fails**: Check if the arXiv ID is valid. Old-style IDs (like `quant-ph/0301234`) may need different handling
- **"already exists in database"**: `process_paper.py` has duplicate detection. Use db-maintenance skill to delete first if re-processing is needed
- **Figure extraction fails**: Non-fatal — the paper still gets added without figure thumbnails
