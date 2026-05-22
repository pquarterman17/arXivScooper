# arXivScooper — Features & Roadmap

A lightweight, browser-based literature management system for superconducting quantum computing (SCQ) research. Inspired by Mendeley circa 2017: powerful enough to be useful, simple enough to not get in the way.

---

## Architecture

Two HTML pages served via a lightweight local server (`scq/server.py`), backed by a SQLite database compiled to WebAssembly via sql.js. Launch with `START.bat` (Windows), `START.command` (macOS), or `python -m scq serve`. The server also provides an arXiv API proxy to avoid CORS issues. Python scripts handle PDF processing and imports.

### File Structure

```
arXivScooper/
├── START.bat                Double-click to launch (Windows)
├── scq/server.py            Local server + arXiv API proxy (renamed from serve.py in plan #12)
├── paper_database.html      Main app (Library + Reading List + Cite tabs)
├── paper_scraper.html       Paper discovery (Search + Inbox + Quick Search tabs)
├── data/arxiv_scooper.db   SQLite database (canonical data source, served via HTTP)
├── db_utils.js              Shared sql.js utility layer (legacy; superseded by src/core/db.js)
├── scraper_config.js        Domain config (presets, tags, sources)
├── papers/                  [junction] PDFs in arXivScooper/papers/
├── figures/                 [junction] Figures in arXivScooper/figures/
├── inbox/                   [junction] arXiv-fetch staging in arXivScooper/inbox/
│
│ // All user data — DB, citations, digests — lives external in
│ // <OneDrive>/Work and School Research/arXivScooper/, mapped
│ // via data/user_config/paths.toml. The repo only carries code.
├── src/                     Frontend ES modules (no build step)
│   ├── core/                  db, store, events, config, search-config-bridge
│   ├── services/              papers, citations, search, exports, settings, ...
│   ├── config/                JSON Schemas + ship-defaults
│   ├── ui/                    database/, scraper/, settings/ — DOM-coupled
│   └── dev/                   Storybook-style harness for ui/ modules
├── scq/                     Python package — invoke via `scq <subcommand>`
│   ├── server.py              HTTP server (renamed from serve.py)
│   ├── cli.py                 subcommand dispatcher
│   ├── config/                paths, user, secrets, portable (export/import)
│   ├── db/                    init, migrations, merge
│   ├── arxiv/                 search, render, email, digest
│   ├── ingest/                process, extract, inbox, mendeley, watch
│   ├── overleaf/sync.py       references.bib → Overleaf project
│   ├── search/index.py        full-text index builder (legacy)
│   ├── schedule.py            digest cron-line manager
│   └── migrate.py             scraper_config.js → user_config converter
├── tools/                   Thin compat shims (each delegates to its scq.* counterpart)
│   ├── fetch_arxiv.js       arXiv API + PDF download (Node.js)
│   ├── fetch.bat / fetch.sh Windows / macOS-Linux wrappers
│   └── *.py                 shim files: process_paper, extract_figures, process_inbox,
│                              import_mendeley, init_database, merge_database — all
│                              equivalent to `scq <subcommand>` (kept so legacy docs
│                              and skill scripts continue to work unchanged)
├── docs/                    Architecture + configuration deep dives
│   ├── architecture.md      Layered structure, page bridge, config-subscribe rule
│   ├── configuration.md     Four-layer config model, x-mergeKey, JS/Python parity
│   ├── adding-a-search-source.md
│   └── adding-a-config-key.md
├── CLAUDE.md                Claude session guide
├── FEATURES.md              This file
└── README.md                Project overview
```

---

## Implemented Features

### Core Database (`paper_database.html`)

**Three views** for different workflows:

- **Cards view** — Expandable paper cards with full summary, key results, figure thumbnails with lightbox, notes editor, tags, citation copy buttons, and related papers
- **Table view** — Sortable columns (author, title, group, year, date added, read status, priority). Click any column header to sort ascending/descending. Includes per-row Word cite button and PDF link
- **Cite view** — All citations listed with one-click copy for .bib or plain text. Bulk export buttons for all papers

**Per-paper data:**

- Title, full author list, short author reference (e.g., "Hedrick et al.")
- Year, research group, date added
- Tags (filterable)
- Summary and key results
- Figure thumbnails with captions (extracted from PDFs, stored as base64)
- BibTeX citation and plain-text citation (Physical Review format)

### Search & Filtering

- **Full-text search** across title, authors, summary, key results, tags, group, arXiv ID, and personal notes
- **Tag filter bar** — Click tags to filter; multiple tags use AND logic; clear button to reset
- **Read/unread filter** — All / Unread / Read toggle buttons
- **Priority filter** — Any / 1+ stars / 3 stars
- **Collection filter** — Sidebar selection scopes all views to a single collection
- Search available on all three HTML pages

### Read Tracking

- Checkbox on every paper (card view and table view) to toggle read/unread
- State persists in SQLite database via `db_utils.js`
- Unread count shown in reading list tab

### Priority Rating

- 1–3 star rating per paper (click to set, click same star to clear)
- Reading list sorts unread papers by priority (highest first)
- Filterable: show only starred papers or only 3-star papers

### Collections

- Create named collections (e.g., "Dissertation Ch. 3", "Group meeting")
- Assign papers to collections via dropdown on each card
- Sidebar shows collections with paper counts
- Click a collection to filter all views to just those papers
- Delete collections (removes assignment, not papers)
- **Export collection as .bib** — When viewing a collection, a sidebar button downloads a .bib file with only those papers

### Related Papers

- **Automatic detection** based on shared authors (2+), shared tags (2+), and same research group
- **Manual linking** — "Link" button on each card opens a picker modal to manually mark papers as related. Links are bidirectional and stored in the `paper_links` table
- Manual links appear as orange chips; auto-detected links appear as default blue chips
- Click any related paper chip to jump to that paper

### PDF Linking

- Convention: place PDFs at `pdfs/<arXivId>.pdf`
- Green "PDF" button on every card and in the table view links directly to the file
- Opens in a new browser tab

### Notes

- Per-paper textarea that auto-saves to SQLite after 500ms of inactivity
- "Saved" confirmation indicator with relative timestamp ("2m ago", "3h ago")
- Notes are included in search results

### State Durability

- All state stored in SQLite database at `data/arxiv_scooper.db` (served directly via HTTP and loaded into the browser by sql.js)
- **Save database** button downloads the `.db` file
- **Export JSON** button downloads full state as JSON backup
- **Import** button restores from `.db` or `.json` file
- **Merge .db** button combines another database with yours (new papers added, existing ones updated)
- Sync indicator shows "synced" vs "unsaved changes"

### Citations

- `.bib` and plain-text citation for every paper
- One-click copy buttons on cards, table rows, and cite view
- "Copy for Word" — formats citation for direct paste into a Word reference list
- Bulk copy: all .bib, all plain text, all for Word
- `references.bib` file maintained alongside the HTML

---

### Reading List (tab in `paper_database.html`)

- Shows only unread papers, sorted by priority (highest first)
- Grouped by date: "Added this week", "Added this month", "Older"
- Card layout with summary, tags, badges
- "Mark as read" button removes paper from list
- Search bar for filtering
- PDF and arXiv link buttons

---

### Citation Helper (Cite tab in `paper_database.html`)

- Multi-select papers with checkboxes for batch citation copy
- Live preview panel shows selected citations
- Format toggle: Word/plain text vs. .bib
- Quick-cite button per row for single-paper copy
- Keyboard shortcuts: `/` to focus search, `Ctrl+C` to copy selection, `Escape` to clear
- Full search across title, authors, group, tags, and arXiv ID

---

### Inline PDF Viewer

- Side panel (50vw) slides in from the right when clicking the PDF button on any card
- Toolbar shows paper title, "Pop out" button (opens in new tab), and close button
- Body shrinks to make room — no overlay, so you can still see the card
- Escape key closes the panel
- Loads PDFs from `pdfs/<arXivId>.pdf`

### Annotation Highlights

- Per-paper system for recording highlighted passages with page references
- Each highlight: page number (optional) + quoted text
- Highlights appear between Notes and Related Papers in the card view
- Add via inline form (page # + text + Add button), remove with × button
- Stored in the `highlights` SQLite table and included in state export/import

### arXiv Search (Quick Search tab in `paper_scraper.html`)

- Keyword search against the arXiv API (returns up to 25 results, sorted by date)
- Preset query buttons for common SCQ topics (Ta resonators, TLS loss, JJ fabrication, etc.)
- Results show title, authors, abstract (collapsible), arXiv categories
- Checkbox selection with select all / clear
- "Export selected for Claude" generates JSON that can be pasted or downloaded for batch import

### Tag Management

- "manage tags" button appears at the end of the tag bar
- Opens a modal listing all tags with paper counts, sorted by frequency
- Per-tag actions: Rename (prompt for new name), Merge (prompt for target tag), Delete (with confirmation)
- All operations update the PAPERS array in place and re-render immediately

---

### Add-Paper Pipeline

The primary workflow for adding papers from arXiv. Two-step process: fetch (host machine) → process (sandbox).

**`tools/fetch_arxiv.js`** — arXiv metadata + PDF downloader (Node.js, runs on host)

- Queries arXiv API for full metadata (title, authors, abstract, categories)
- Downloads PDF to `papers/`
- Saves structured JSON to `inbox/<id>_meta.json`
- Cross-platform: use `fetch.bat` (Windows) or `fetch.sh` (macOS/Linux)
- Handles redirects, duplicate detection, versioned papers

**`scq process` / `tools/process_paper.py`** — Full database ingestion pipeline (Python, runs in sandbox)

- Reads `_meta.json` from inbox, extracts figures, generates citations
- Auto-tags from arXiv categories + keyword detection (18+ SCQ domain terms)
- Inserts into SQLite: paper, figures, FTS index, read status, optional notes
- Appends to `references.bib` and `references.txt` with duplicate detection
- Writes directly to `data/arxiv_scooper.db` (no re-export step — the canonical store is the .db file itself)
- Usage: `scq process <arxiv_id> [--note "..."]` (canonical) or
  `python3 tools/process_paper.py <arxiv_id>` (compat shim)

### Python Tools

**`tools/extract_figures.py`** — Figure & caption extraction from PDFs

- Uses PyMuPDF (fitz) to scan for "Figure N" / "Fig. N" / "FIG. N" patterns
- Rasterizes pages at 200 DPI, crops using image bounding boxes
- Saves as JPEG (800px max width, quality 70)
- Outputs `captions.json` with figure metadata
- Fallback mode: if no captions found, extracts all detected images

**`tools/import_mendeley.py`** — Mendeley/Zotero/Scholar .bib import

- Parses .bib files using bibtexparser
- Handles LaTeX cleanup, author abbreviation, arXiv ID extraction from multiple fields
- Outputs JSON with all entries for adding to the database
- `--dry-run` mode for preview without changes

**`tools/process_inbox.py`** — Batch inbox processor

- Drop PDFs into `inbox/`, run script
- Extracts arXiv ID and DOI from first pages using PyMuPDF or pdftotext
- Runs figure extraction automatically
- Moves PDFs to `pdfs/` with standardized naming
- Outputs `inbox_manifest.json` for Claude to add entries to the database
- `--dry-run` mode for preview

**`tools/build_search_index.py`** — Full-text PDF search index builder (legacy, replaced by FTS5)

- Extracts text from each PDF in `pdfs/` using PyMuPDF (with pdftotext fallback)
- Tokenizes and builds per-page text snippets (500 chars) and top-50 term frequency lists
- Outputs `search_index.json` — now superseded by the `pdf_text` FTS5 table in the SQLite database
- `--stats` mode for viewing index info without rebuilding

**`tools/init_database.py`** — SQLite database initializer and migration tool

- Creates `arxiv_scooper.db` with full schema (papers, figures, notes, highlights, collections, links, FTS5)
- `--migrate` mode: extracts data from `paper_database.html` PAPERS array + `notes.json` + `search_index.json` into the database
- `--stats` mode: prints table counts and index statistics
- Backs up existing `.db` file before overwriting

---

### Scheduled Tasks

- **Weekly arXiv digest** (`scq-weekly-digest`) — Runs Mondays at 9am, searches arXiv for new superconducting quantum computing papers

---

### Auto-Tagging

Keyword dictionary (18+ domain terms) for auto-suggesting tags when papers are added. Covers: tantalum, aluminum, niobium, TLS, surface loss, Josephson junction, transmon, resonator, qubit, kinetic inductance, quasiparticle, oxide, sapphire, silicon, coherence, decoherence, microwave, cryogenic, and more.

---

### CI/CD Pipeline

The GitHub Actions CI has two workflows:

**Test workflow** (`.github/workflows/test.yml`) — runs on every push and pull request:
1. **Lint gate** — `ruff check` + `ruff format --check` runs first; pytest and vitest jobs are blocked until lint passes
2. **pytest** — runs the full Python test suite with `--cov-fail-under=40` coverage enforcement
3. **vitest** — runs the full frontend test suite
4. **E2E smoke test** — spins up `scq serve` (unbuffered) and hits a representative slice of HTTP endpoints

**Digest workflow** (`.github/workflows/digest.yml`) — runs on schedule (weekly):
- **Fail-fast secrets check** — validates `SCQ_EMAIL_FROM`, `SCQ_EMAIL_APP_PASSWORD`, and `SCQ_EMAIL_TO` are non-empty before any digest work starts; fails immediately with a clear error if any are missing
- **`--require-email` flag** — the digest script exits with code 2 if the email step fails, ensuring CI correctly reports failure rather than silently succeeding
- **GitHub Actions job summary** — writes paper counts and email delivery status to the run's summary page for quick inspection without reading logs
- **Self-healing on failure** — if the digest job fails, the workflow automatically opens a GitHub Issue labelled `digest-failure` with a diagnosis of the likely cause and step-by-step fix instructions

Branch protection configuration (required status checks, dismiss-stale-reviews settings) is documented at `.github/branch-protection-setup.md`.

---

### Relevance & Ranking System

Paper relevance scoring is **config-driven** — keywords, weights, and author boosts live in JSON files, not hardcoded Python. The system falls back to built-in defaults if the config file is missing or malformed.

**Config files:**
- `src/config/schema/relevance.schema.json` — JSON Schema; the authoritative definition of valid keys and value ranges
- `src/config/defaults/relevance.json` — ship defaults committed to the repo; covers all major SCQ topic areas
- `data/user_config/relevance.json` — user overrides (gitignored; copy from `relevance.json.example` to start)

**Interest profiles** — seven named profiles, each with a `focus` multiplier and weighted keyword list:
- `materials` — substrate, deposition, and materials processing terms
- `coherence` — T1/T2/loss mechanisms
- `characterization` — spectroscopy and measurement techniques
- `readout` — dispersive readout, amplifier chains
- `gates` — gate fidelity, cross-resonance, two-qubit operations
- `general_scq` — broad superconducting qubit terms
- `off_topic` — negative-weight terms that reduce score (e.g., photonics, classical ML)

**Author boosts** — substring matches on the `authors` field give a configurable bonus, letting you up-weight papers from groups you follow closely.

**Tunable parameters:** `titleMultiplier` (title hits score higher than abstract hits), `minScoreToInclude` (minimum score for a paper to appear in the digest).

---

### CLI Commands

**`scq doctor`** — local health-check. Validates 9 aspects of the installation:
- Python version compatibility
- Keyring secrets present (email credentials)
- Config files exist and parse correctly
- Recipient list non-empty
- DB path resolves and is writable
- Digests output directory exists
- GitHub secrets configured (checks `.github/workflows/` for referenced secret names)
- SMTP connectivity (attempts a test connection without sending)

**`scq monitor`** — checks the most recent GitHub Actions digest run status. Flags:
- `--notify` — structured output suitable for piping or desktop notifications
- `--fix` — runs `scq doctor` and suggests specific remedies for any detected failures

**`scq relevance show`** — prints the active relevance config: profile names, `focus` multipliers, author boost list, and keyword counts per profile.

**`scq relevance learn`** — scans papers you have marked as read or starred, computes frequent authors and terms not yet in your config, and suggests additions as a JSON patch you can paste into `data/user_config/relevance.json`.

**`scq relevance test <query>`** — scores a free-text query as if it were a paper abstract and prints a breakdown: total score, per-profile contribution, and every keyword/author match with its weight.

---

## Current Papers (5)

| ID | Authors | Title | Group |
|----|---------|-------|-------|
| 2603.13183 | Hedrick et al. | Quantifying surface losses in superconducting aluminum microwave resonators | de Leon (Princeton) |
| 2603.13174 | Joshi et al. | Beta Tantalum Transmon Qubits with Quality Factors Approaching 10 Million | de Leon (Princeton) |
| 2510.20114 | Potluri et al. | Fabrication and Structural Analysis of Trilayers for Tantalum Josephson Junctions with Ta₂O₅ Barriers | Eley (UW) / Pappas (Rigetti) |
| 2510.15182 | Yang et al. | Tantalum alloy-based resonators for quantum information systems | de Leon / Cava (Princeton) |
| 2603.17921 | Pitton et al. | Quantum-Material Josephson Junctions: Unconventional Barriers, Emerging Functionality | Ali (TU Delft) |

---

## Future Roadmap

### ~~Near-term~~ (implemented)

- ~~**Inline PDF viewer**~~ — Side panel opens PDFs within the database page. "Pop out" button for new tab. Escape to close.
- ~~**Annotation highlights**~~ — Per-paper highlight/annotation system with page references. Stored in localStorage and included in state export/import.
- ~~**Batch add from arXiv search**~~ — New `arxiv_search.html` page with keyword search, preset queries, paper selection, and JSON export for Claude to ingest.
- ~~**Tag management UI**~~ — "manage tags" button on the tag bar opens a modal to rename, merge, or delete tags across all papers.
- ~~**Smart date grouping**~~ — Reading list now groups papers under "Added this week", "Added this month", and "Older" headers.

### ~~Medium-term~~ (implemented)

- ~~**Move from embedded base64 to external images**~~ — Figures extracted to `figures/extracted/*.jpg` files. FIGS object now stores file paths instead of base64. HTML reduced from ~930KB to ~74KB
- ~~**Migrate state from localStorage to notes.json as primary**~~ — notes.json loaded on page open as source of truth. localStorage serves as live cache. Sync indicator shows "synced" vs "unsaved changes". "Save to file" button downloads current state as notes.json
- ~~**Full-text PDF search**~~ — `tools/build_search_index.py` extracts text from PDFs into `search_index.json`. "PDF text" checkbox in search controls loads the index and searches full paper content with page-level snippet results
- ~~**Paper versioning**~~ — Notes track last-edit timestamps per paper with relative time display ("2m ago", "3h ago", etc.). Edit history included in notes.json export/import and state durability

### ~~Longer-term~~ SQLite backend (implemented)

- ~~**SQLite backend via sql.js (WebAssembly)**~~ — All paper data, notes, highlights, collections, links, and read status stored in `arxiv_scooper.db`. No more localStorage limits. Database loaded in browser via sql.js WASM (~1MB). "Save database" button downloads the full `.db` file. JSON export still available for backup/migration.
- ~~**FTS5 full-text search**~~ — Paper metadata and PDF text indexed in SQLite FTS5 virtual tables with Porter stemming. Replaces the separate `search_index.json` file.
- ~~**Data separated from presentation**~~ — Paper data no longer hardcoded in HTML files. All pages load from the shared `arxiv_scooper.db` via `db_utils.js`. Adding papers only requires updating the database, not editing HTML.
- ~~**Migration tooling**~~ — `tools/init_database.py --migrate` converts the old HTML PAPERS array + notes.json into the new SQLite database. Backs up existing `.db` before overwriting.

### ~~Web scraper~~ (implemented)

- ~~**Web scraper for papers** (`paper_scraper.html`)~~ — Multi-source paper discovery with staging inbox
  - **Sources:** arXiv API + Physical Review Letters + PR Applied + PR Materials RSS feeds
  - **Saved queries:** Persist search terms per source in sidebar; "Run all" fetches every query in sequence with rate limiting
  - **Staging inbox:** Papers land in a review queue; approve/dismiss individually or bulk-approve all; add quick notes before committing
  - **Auto-tagging:** 18+ domain keyword dictionary suggests tags on fetch (same dictionary as paper_database)
  - **Duplicate detection:** Papers already in the database are flagged; can't be re-added
  - **DB integration:** Approved papers written via `SCQ.addPaper()` with auto-generated BibTeX and plain-text citations
  - **Preset queries:** Quick buttons for common SCQ topics (Ta resonators, TLS loss, etc.)
  - **Keyboard shortcuts:** `/` to focus search, `Escape` to close modal
  - Linked from paper_database.html header toolbar

### ~~Entry types & website bookmarks~~ (implemented)

- ~~**Entry type system**~~ — Every database entry has a `entry_type` column (preprint, published, website, new release, thesis, review)
  - Type filter bar in paper_database.html shows counts per type; click to filter
  - Type badge shown on cards and in table view, color-coded from `scraper_config.js`
  - Types are defined in `SCRAPER_CONFIG.entryTypes` — add new ones in config and they appear everywhere
  - Automatic migration: existing databases get the column added on load
- ~~**Add Website / Link**~~ — "Add link" button in header opens a modal to paste any URL
  - Auto-detects arXiv URLs and fetches title/authors/abstract from arXiv API
  - For other URLs, pre-fills domain as source; manual title/description entry
  - Choose entry type (defaults to "Website"), add tags and notes
  - Saved as a regular database entry, filterable and searchable like papers
- ~~**Scraper type tagging**~~ — Papers ingested from the scraper or suggestions banner are auto-typed:
  - arXiv source → "preprint"
  - Journal-ref sources (PRL, PR Applied, PR Materials) → "published"

### Future roadmap
- **Citation graph visualization** — Interactive D3-based network graph showing how papers connect through shared authors, citations, and manual links
- **Reading analytics** — Track reading pace, coverage by topic, time-to-read for flagged papers
- **Collaboration support** — Share collections or the full database with lab members via shared OneDrive folder with db merge logic
- **BibTeX auto-generation from DOI** — Given a DOI, fetch full metadata from CrossRef API and auto-populate all citation fields
- **Zotero connector** — Browser extension integration to add papers directly from journal websites
- **LaTeX/Overleaf integration** — Auto-sync references.bib with an Overleaf project for seamless manuscript writing

---

## Tech Stack

- HTML/CSS/JavaScript (vanilla, no frameworks)
- **sql.js 1.8.0** (SQLite compiled to WebAssembly) for in-browser database
- `scq/server.py` — lightweight local server with arXiv API proxy (renamed from `serve.py` in plan #12)
- `db_utils.js` — shared database access layer used by all HTML pages
- IBM Plex Sans font, dark theme (#0e1117 background, #58a6ff accent)
- Python 3 + PyMuPDF + bibtexparser for tooling
- Node.js for arXiv fetch script