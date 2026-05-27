# arXivScooper — Claude Session Guide

This is a scientific literature management system for superconducting quantum computing (SCQ) research. It runs as two HTML pages served via a local Python server (`scq/server.py`, launched via `python -m scq serve` or by double-clicking `START.bat`), backed by a SQLite database (`data/arxiv_scooper.db`, served directly via HTTP and loaded into the browser by sql.js).

> Naming note: the project lives at `github.com/pquarterman17/arXivScooper`. User-facing branding says "arXivScooper"; internal docs and code may still say "SCQ" since the *research domain* (superconducting quantum computing) is unchanged. The Python package is `scq` and the env-var prefix is `SCQ_` — don't rename those. The repo was renamed from `arXivPoopScooper` to `arXivScooper` on 2026-05-21, and the database file was correspondingly renamed from `arxiv_poop_scooper.db` to `arxiv_scooper.db` (earlier history: `scientific_litter_scoop.db` → `arxiv_poop_scooper.db` on 2026-05-03; `scq_papers.db` → `scientific_litter_scoop.db` on 2026-05-01).

> **Refactor complete (2026-05-03):** the layered architecture target
> is reached. Frontend lives under `src/{core,services,ui,config,dev}/`,
> Python under `scq/`. Both halves share JSON Schemas + test vectors.
> The full plan archive is at `plans/archive/architecture-refactor.md`.
> The two HTML pages still have inline boot blocks that import from the
> module layer via the page-bridge pattern (see `docs/architecture.md`);
> don't extend the boot blocks with new features. New functionality goes
> in `src/services/*` (DOM-free) or `src/ui/*` (DOM-coupled).

## Architecture Documentation

Deep-dive docs live in `docs/`. Read these before extending subsystems you haven't touched before:

- [`docs/architecture.md`](docs/architecture.md) — the layered structure (`core/` / `services/` / `ui/` / `scq/`), the three rules that keep a future Vue 3 port viable, the **page bridge** pattern with frozen-list specs, the **config-subscribe rule** for surfaces that read merged config, the dev harness, and TypeScript-via-JSDoc setup.
- [`docs/configuration.md`](docs/configuration.md) — the four-layer config model, `x-mergeKey` semantics, the JS/Python parity story, the **domain-config vs. user reference data** distinction.
- [`docs/adding-a-search-source.md`](docs/adding-a-search-source.md) — step-by-step for new journals.
- [`docs/adding-a-config-key.md`](docs/adding-a-config-key.md) — schema → defaults → loader → JS service → Python loader → Settings UI.

## Custom Skills

Four project-specific skills are available in `.claude/skills/`. Use them instead of working from scratch:

| Skill | When to use |
|---|---|
| **add-paper** | User gives you an arXiv ID or URL. Handles fetch → process → offer enrichment. |
| **enrich-paper** | Read a paper's PDF and fill in summary, key results, research group. |
| **db-maintenance** | Delete papers, update tags, edit notes, manage collections, fix citations. |
| **literature-review** | Synthesize papers on a topic into a structured field overview. |

These skills contain ready-to-use code snippets, the DB access pattern, and domain-specific guidance. Always check them first before writing database code from scratch.

## Adding a Paper from arXiv

This is the most common task. It's a two-step pipeline. See the **add-paper** skill for full details.

### Step 1: Fetch (runs on host machine via Desktop Commander)

The Cowork sandbox cannot reach arxiv.org. Use Desktop Commander to run the fetch script on the user's actual machine.

**Windows:**
```
%TEMP%\run_fetch.bat <arxiv_id>
```
> Note: `run_fetch.bat` is a wrapper in `%TEMP%` that handles Windows path quoting.
> The actual script lives at `tools/fetch_arxiv.js`. If `run_fetch.bat` doesn't exist,
> create it with: `"C:\Program Files\nodejs\node.exe" "<project_path>\tools\fetch_arxiv.js" %*`

**macOS/Linux:**
```
bash tools/fetch.sh <arxiv_id>
```

**What it does:** Queries the arXiv API for metadata, downloads the PDF to `papers/`, and saves a JSON file to `inbox/<arxiv_id>_meta.json`.

**Desktop Commander tips:**
- Use `shell: "cmd"` on Windows to capture output (PowerShell swallows stdout)
- Paths with spaces need a .bat wrapper on Windows — don't try to quote them inline
- The script is pure Node.js (`fetch_arxiv.js`) and is cross-platform

### Step 2: Process (runs in the Cowork sandbox)

```bash
cd "$PROJECT_ROOT"
scq process <arxiv_id> --note "optional note"
# or, equivalent: python3 tools/process_paper.py <arxiv_id> --note "..."
# (the tools/ wrappers are thin compat shims that delegate to scq.ingest.process)
```

Find `PROJECT_ROOT` dynamically:
```bash
PROJECT_ROOT=$(find /sessions -name "arxiv_scooper.db" -path "*/mnt/*/data/*" 2>/dev/null | head -1 | xargs dirname | xargs dirname)
```

**What it does (all automatic):**
1. Reads `inbox/<arxiv_id>_meta.json`
2. Extracts figures + captions from the PDF via `scq.ingest.extract` (`tools/extract_figures.py` is a shim)
3. Generates BibTeX and plain-text (Physical Review style) citations
4. Auto-tags based on arXiv categories + keyword matching
5. Inserts into SQLite: paper entry, figures, FTS index, read status
6. Appends to `references.bib` and `references.txt` (with duplicate detection)

The DB at `data/arxiv_scooper.db` is the canonical store; the browser fetches
it directly via HTTP and reads it with sql.js. There is no re-export step.

### Enriching a Paper

See the **enrich-paper** skill for full instructions. In short:

1. Read the PDF: `papers/<arxiv_id>_<Author>_<ShortTitle>.pdf`
2. Write a 2-3 sentence summary focused on what was done and why it matters
3. Extract 3-5 key results as a JSON array of strings
4. Identify the research group (e.g., "de Leon (Princeton)", "Ali (TU Delft)")
5. Update the DB at `data/arxiv_scooper.db` (no re-export step needed)

## File Structure

The project is split between two OneDrive locations (paths shown below
are examples — the actual values depend on your username and OneDrive
mount). On macOS the equivalents live under
`~/Library/CloudStorage/OneDrive-*` or wherever you sync OneDrive.
- **Code repo:** `<OneDrive>\Coding\git\arXivScooper\` — only code, configs, docs, and tests. No PDFs, no figures, no DB, no citations, no digests.
- **Paper library (all user data):** `<OneDrive>\Work and School Research\arXivScooper\`
  - `papers/`, `figures/`, `inbox/` — surfaced in the repo via Windows directory junctions
  - `database/arxiv_scooper.db` — the SQLite database (resolver: `paths.db_path`)
  - `citations/references.bib`, `citations/references.txt` — BibTeX + plain-text citations (resolver: `paths.references_bib_path` / `references_txt_path`)
  - `digests/digest_YYYY-MM-DD.html` — daily arXiv-digest reports (resolver: `paths.digests_dir`)
- The repo's `data/user_config/paths.toml` (gitignored) maps each name above to its OneDrive absolute path. New machines copy `paths.toml.example`, edit, and the resolver picks up the override.

```
arXivScooper/
├── START.bat                Double-click to launch (Windows)
├── scq/server.py            Local server + arXiv API proxy + no-cache headers (renamed from serve.py 2026-05-03)
├── paper_database.html      Main app (Library / Reading List / Cite / Settings)
├── paper_scraper.html       Scraper (Search / Inbox / Quick Search)
├── dev.html                 Storybook-style dev harness for UI modules
├── db_utils.js              sql.js IIFE used by the inline boot blocks; new code uses src/core/db.js
├── scraper_config.js        Ship-default scraper config; user overrides flow through src/config/ + the search-config-bridge
├── (references.bib + .txt)  → arXivScooper/citations/ (external; see paths.toml)
├── data/
│   ├── arxiv_scooper.db    Canonical SQLite database (served via HTTP)
│   ├── migrations/          Versioned schema (001_initial.sql, etc.)
│   └── user_config/         User overrides (gitignored) + .example starters
│       └── relevance.json.example   Relevance profile template
├── src/                     Layered frontend (no build step, ES modules)
│   ├── core/                  db, store, events, config, search-config-bridge — DOM-free
│   ├── services/              papers/notes/tags/citations/arxiv/exports/etc — DOM-free
│   ├── config/                schemas + ship-defaults
│   │   ├── schema/relevance.schema.json   JSON Schema for relevance config
│   │   └── defaults/relevance.json        Ship-default keyword profiles
│   ├── ui/                    DOM-coupled (database/, scraper/, settings/)
│   ├── dev/                   Storybook-style harness — stories under stories/
│   └── tests/                 vitest specs (run with `npm test`)
├── scq/                     Python package
│   ├── server.py              HTTP server + arXiv proxy (renamed from repo-root serve.py 2026-05-03)
│   ├── cli.py                 `scq <subcommand>` dispatcher
│   ├── __main__.py            `python -m scq <subcommand>` entry
│   ├── config/                paths, user, secrets, portable (export/import)
│   ├── db/                    init, migrations, merge
│   ├── arxiv/                 search, render, email, digest
│   ├── ingest/                process, extract, inbox, mendeley, watch
│   ├── overleaf/              sync (references.bib → Overleaf project)
│   ├── search/                build-index
│   ├── schedule.py            `scq schedule show/update` for the digest cron line
│   ├── migrate.py             `scq migrate-from-legacy` — scraper_config.js → user_config
│   ├── doctor.py              `scq doctor` — local health-check (9 aspects)
│   ├── monitor.py             `scq monitor` — GitHub Actions digest run status
│   └── relevance.py           `scq relevance show/learn/test` — config-driven ranking
├── papers/                  [Junction → arXivScooper\papers] PDFs: <arXivId>_<Author>_<ShortTitle>.pdf
├── figures/                 [Junction → arXivScooper\figures] Extracted figures by arXiv ID
│   └── <arXivId>/           fig1.jpg, fig2.jpg, ..., captions.json
├── inbox/                   [Junction → arXivScooper\inbox] Staging area for _meta.json files
├── tools/                   Thin compat shims that delegate to scq.* (kept so existing
│                              docs and skill scripts keep working unchanged)
├── docs/                    Architecture deep-dives (README, architecture, configuration,
│                              adding-a-search-source, adding-a-config-key)
├── plans/archive/           Archived plans from completed refactors (gitignored)
├── .claude/skills/          Project-specific Claude skills
│   ├── add-paper/           Full arXiv → DB pipeline
│   ├── enrich-paper/        PDF → summary/results/group
│   ├── db-maintenance/      CRUD operations on the database
│   └── literature-review/   Synthesize papers into field overviews
├── .github/
│   ├── workflows/           CI: test.yml (lint→pytest→vitest→e2e), digest.yml (weekly)
│   └── branch-protection-setup.md   GitHub branch protection configuration guide
├── LICENSE                  MIT
├── SECURITY.md              Vulnerability disclosure policy
├── CLAUDE.md                This file
├── FEATURES.md              Full feature documentation
├── README.md                Quick overview
├── CODE_OF_CONDUCT.md       Contributor Covenant 2.1
└── pyproject.toml           Python package config
```

## Database Schema (key tables)

- **papers** — id (arXiv ID), title, authors, short_authors, year, tags (JSON array), summary, key_results (JSON array), cite_bib, cite_txt, pdf_path, group_name, date_added
- **figures** — paper_id, figure_key, file_path, label, caption, sort_order
- **notes** — paper_id, content, last_edited
- **read_status** — paper_id, is_read, priority (0-3 stars)
- **collections** — name, paper_id
- **settings** — key, value (JSON) — stores user preferences like sources/presets
- **papers_fts** — FTS5 full-text search index over papers

The DB is `data/arxiv_scooper.db`, a regular SQLite file. To work with it from Python:

```python
import sqlite3, glob

# Find the repo root dynamically (sandbox or local)
matches = glob.glob("/sessions/*/mnt/*/data/arxiv_scooper.db")
DB = matches[0] if matches else "data/arxiv_scooper.db"

conn = sqlite3.connect(DB)
conn.execute("PRAGMA foreign_keys = ON")
# ... do work ...
conn.commit()
conn.close()
```

The `scq.db.connection` helper (in `scq/db/connection.py`) does this for you and resolves the path from `data/user_config/paths.toml` if it exists. The legacy `scq_data.js` base64 bootstrap was retired in commit `c3694d1`.

## Platform Notes

| | Windows PC | MacBook |
|---|---|---|
| Node.js path | `C:\Program Files\nodejs\node.exe` | `node` (in PATH) |
| Fetch wrapper | `fetch.bat` (or `run_fetch.bat` in Temp) | `bash tools/fetch.sh` |
| DC shell | Use `shell: "cmd"` (PowerShell eats stdout) | Default shell works |
| Code path | `<OneDrive>\Coding\git\arXivScooper` | `~/Library/CloudStorage/OneDrive-*/Coding/git/arXivScooper` (or wherever you sync) |
| Data path | `<OneDrive>\Work and School Research\arXivScooper` (junctioned into the repo as `papers/`, `figures/`, `inbox/`) | Equivalent OneDrive path on the Mac side |
| Python (sandbox) | Always Linux sandbox — same on both | Same |

## arXiv API Connectivity

The browser-based scraper/database need to reach the arXiv API. This is handled via a
local proxy in `scq/server.py` that avoids CORS and sets a proper User-Agent header:

- **scq/server.py** exposes `/api/arxiv?<query>` which forwards to `https://arxiv.org/api/query?<query>`
- Both `paper_scraper.html` and `paper_database.html` auto-detect localhost and route
  through the proxy. Falls back to CORS proxies (allorigins, corsproxy.io) then direct fetch.
- `export.arxiv.org` is **unreachable** from the user's network (Fastly CDN routing issue).
  All code uses `arxiv.org` instead. Do NOT switch back to `export.arxiv.org`.
- If 429 rate-limit errors occur, wait a few minutes between searches.

## CI Pipeline

The GitHub Actions CI gate runs in three sequential stages:

1. **Lint** (`ruff check` + `ruff format --check`) — must pass before any tests run
2. **Tests** — `pytest` (enforces `--cov-fail-under=40`) + `vitest`; both depend on the lint job
3. **E2E smoke** — spins up `scq serve` with unbuffered Python output and hits a representative slice of endpoints

The **digest workflow** (`.github/workflows/digest.yml`) adds:
- **Fail-fast secrets check** — validates `SCQ_EMAIL_FROM`, `SCQ_EMAIL_APP_PASSWORD`, `SCQ_EMAIL_TO` are non-empty before running
- **`--require-email` flag** on the digest script — exits 2 if email fails (CI-safe)
- **GitHub Actions job summary** — writes paper counts + email status to the run summary page
- **Self-healing on failure** — auto-opens a GitHub Issue labelled `digest-failure` with diagnosis + fix instructions

Branch protection setup is documented at `.github/branch-protection-setup.md`.

## Relevance Config System

Keywords and ranking parameters are **config-driven**, not hardcoded. The system falls back to built-in defaults if config loading fails.

**Config files:**
- `src/config/schema/relevance.schema.json` — JSON Schema (source of truth for valid keys)
- `src/config/defaults/relevance.json` — ship defaults (committed; covers all SCQ topics)
- `data/user_config/relevance.json` — user overrides (gitignored; copy from `.example`)

**Profiles** — each profile has a `focus` multiplier and a list of keywords with weights:
- `materials`, `coherence`, `characterization`, `readout`, `gates`, `general_scq`, `off_topic`

**Author boosts** — substring match on the `authors` field → bonus score points.

**Tunable parameters:** `titleMultiplier` (title keyword hits score higher), `minScoreToInclude` (paper score threshold for digest inclusion).

**Patent scoring** (`scq/patents/relevance.py`, `score_patent`) reuses the same config plus two patent-only maps: `cpcBoosts` (CPC-prefix → points, e.g. `G06N10`, `H10N60`) and `assigneeBoosts` (assignee substring → points, the patent analogue of `authorBoosts`). Patent keyword matching is **word-boundary** anchored (not substring) so acronyms like `MBE`/`TiN` don't false-match inside `number`/`destination`. `scq relevance test <patent-number>` scores a stored patent and explains the CPC/assignee/keyword matches. Other patent CLI: `scq patents summarize <num>` (LLM summary via Anthropic if keyed, else prints the prompt) and `scq patents monitor --assignee NAME` (recent-filings tracker; dormant until a PatentsView key is stored).

**CLI commands for relevance:**
```bash
scq relevance show           # active profiles, focus values, author boosts, keyword counts
scq relevance learn          # scans read/starred papers, suggests author boosts + keywords
scq relevance test "query"   # scores a paper and explains every keyword/author match
```

## Patents (Phase 1)

Patents are a first-class entity alongside arXiv papers, stored in a
**separate `patents` table** (migration `002_patents.sql`). The package
`scq/patents/` mirrors `scq/arxiv/`: `providers/patentsview.py` (USPTO
PatentsView, the first source), `normalize.py` (canonical `Patent` +
patent-number parsing), `store.py`, `summarize.py`, `cli.py`. Full design
and phasing live in `plans/patent-scraping.md`.

The flow mirrors add-paper's host/sandbox split:
```bash
scq patents fetch US10374134B2      # network (host): provider → inbox/<num>_patent.json
scq patents process US10374134B2    # sandbox: inbox JSON → patents table
scq patents show US10374134B2       # print a stored patent
```

Two providers, selected with `--source` (both converge on the canonical
`Patent` shape, so everything downstream is source-agnostic):
- **`google`** (default) — keyless HTML scrape of `patents.google.com`.
  No API key, works immediately; bibliographic data from server-rendered
  `<meta>` tags is reliable, claims extraction is best-effort/brittle.
  ToS-gray — fine for personal low-volume research use.
- **`patentsview`** — USPTO PatentsView PatentSearch API. Needs a free
  key: `scq config set-secret patentsview_api_key` (or `SCQ_PATENTSVIEW_API_KEY`).
  More robust + structured claims. The browser/host routes through the
  `/api/patents` proxy in `scq/server.py`, which injects the key header.
  Base overridable via `SCQ_PATENTSVIEW_API_BASE` (see the migration note
  in `plans/patent-scraping.md`).

**GUI:** the scraper page (`paper_scraper.html`) has a **Patents tab** —
fetch-by-number (keyless Google), a filterable browse list of stored
patents, and a dormant PatentsView keyword search (shows a "set your key"
message until the key is stored). It's backed by two server endpoints,
`POST /api/patents/add` and `GET /api/patents/list` (fetch+store happen
server-side; the browser can't run the ingest). Frontend lives in
`src/services/patents.js` + `src/ui/scraper/patents-tab.js`. The database
page (`paper_database.html`) also has a **Patents main-tab** (filterable
list → expandable detail with the summary fields + independent claims),
in `src/ui/database/patents-view.js`, backed by `GET /api/patents/get`
and `/api/patents/list`.

After `process` (or the GUI add), use the **summarize-patent** skill to
translate the claim legalese into three plain-English fields: `plain_summary` (what it does),
`protected_scope` (a plain reading of the independent claims = the real
legal scope), and `prior_art_note` (what it builds on). CPC/IPC codes are
stored but not yet scored — relevance scoring is Phase 2.

## Common Tasks Quick Reference

**Add paper:** Use the `add-paper` skill, or manually: fetch.bat/sh → process_paper.py → enrich
**Enrich paper:** Use the `enrich-paper` skill to read PDF and fill summary/results/group
**Add note:** Use `db-maintenance` skill, or: update `notes` table, then `conn.commit()` (no re-export — the .db is canonical)
**Change tags:** Use `db-maintenance` skill, or: update `tags` JSON in `papers` table, then `conn.commit()`
**Literature review:** Use the `literature-review` skill to synthesize papers on a topic
**Bulk import:** Use `tools/import_mendeley.py` for .bib files
**DB migration:** Use `tools/init_database.py` to create/update schema
**Health check:** `scq doctor` — validates Python version, keyring secrets, config files, recipients, DB path, digests dir, GitHub secrets, SMTP connectivity (9 checks total)
**CI status:** `scq monitor` — checks last GitHub Actions digest run; `--notify` for structured output, `--fix` runs doctor + suggests remedies
**Tune relevance:** `scq relevance show/learn/test` — inspect and evolve the paper-ranking config
