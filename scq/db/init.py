#!/usr/bin/env python3
"""
Initialize the SQLite database for the SCQ paper reference system.

Creates arxiv_scooper.db with:
  - papers: core metadata (title, authors, summary, citations, etc.)
  - figures: per-paper figure references (file paths + captions)
  - notes: user notes with edit timestamps
  - highlights: annotation highlights with page references
  - collections: named collections of papers
  - paper_links: bidirectional manual links between papers
  - read_status: read/unread flag + priority stars
  - papers_fts: FTS5 virtual table for fast full-text search
  - pdf_text: FTS5 virtual table for full-text PDF content search

Usage:
  python tools/init_database.py                  # create fresh db
  python tools/init_database.py --migrate        # migrate from current HTML + notes.json
  python tools/init_database.py --stats          # show db statistics
"""

import argparse
import json
import os
import re
import sqlite3
import sys

# Make the repo-root scq package importable when running from a fresh
# checkout (`python -m scq.db.init`); harmless when installed via pip.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from scq.config.paths import paths as _scq_paths

    DB_PATH = str(_scq_paths().db_path)
except Exception:
    DB_PATH = os.path.join(_REPO_ROOT, "data", "arxiv_scooper.db")

SCHEMA = """
-- Core paper metadata
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,          -- full author string
    short_authors TEXT,             -- "Hedrick et al."
    year INTEGER,
    journal TEXT DEFAULT '',
    volume TEXT DEFAULT '',
    pages TEXT DEFAULT '',
    doi TEXT DEFAULT '',
    arxiv_id TEXT DEFAULT '',
    url TEXT DEFAULT '',
    group_name TEXT DEFAULT '',     -- e.g. "de Leon (Princeton)"
    date_added TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',         -- JSON array of strings
    summary TEXT DEFAULT '',
    key_results TEXT DEFAULT '[]',  -- JSON array of strings
    cite_bib TEXT DEFAULT '',
    cite_txt TEXT DEFAULT '',
    pdf_path TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Figure references (external files)
CREATE TABLE IF NOT EXISTS figures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    figure_key TEXT NOT NULL,       -- e.g. "hedrick_fig1"
    file_path TEXT NOT NULL,        -- e.g. "figures/extracted/hedrick_fig1.jpg"
    label TEXT DEFAULT '',          -- e.g. "Fig. 1"
    caption TEXT DEFAULT '',        -- description
    sort_order INTEGER DEFAULT 0,
    UNIQUE(paper_id, figure_key)
);

-- User notes per paper
CREATE TABLE IF NOT EXISTS notes (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    content TEXT DEFAULT '',
    last_edited TEXT DEFAULT ''
);

-- Annotation highlights
CREATE TABLE IF NOT EXISTS highlights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    page INTEGER DEFAULT NULL,
    color TEXT DEFAULT '#58a6ff',
    created_at TEXT DEFAULT (datetime('now'))
);

-- Named collections
CREATE TABLE IF NOT EXISTS collections (
    name TEXT NOT NULL,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    PRIMARY KEY (name, paper_id)
);

-- Manual bidirectional links between papers
CREATE TABLE IF NOT EXISTS paper_links (
    paper_a TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    paper_b TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (paper_a, paper_b),
    CHECK (paper_a < paper_b)  -- canonical ordering prevents duplicates
);

-- Read status and priority
CREATE TABLE IF NOT EXISTS read_status (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    is_read INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 0    -- 0-3 stars
);

-- FTS5 for paper metadata search
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    id, title, authors, summary, tags, key_results,
    content=papers,
    content_rowid=rowid,
    tokenize='porter unicode61'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, id, title, authors, summary, tags, key_results)
    VALUES (new.rowid, new.id, new.title, new.authors, new.summary, new.tags, new.key_results);
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, id, title, authors, summary, tags, key_results)
    VALUES ('delete', old.rowid, old.id, old.title, old.authors, old.summary, old.tags, old.key_results);
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, id, title, authors, summary, tags, key_results)
    VALUES ('delete', old.rowid, old.id, old.title, old.authors, old.summary, old.tags, old.key_results);
    INSERT INTO papers_fts(rowid, id, title, authors, summary, tags, key_results)
    VALUES (new.rowid, new.id, new.title, new.authors, new.summary, new.tags, new.key_results);
END;

-- FTS5 for full-text PDF content search
CREATE VIRTUAL TABLE IF NOT EXISTS pdf_text USING fts5(
    paper_id,
    page_num,
    content,
    tokenize='porter unicode61'
);

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_figures_paper ON figures(paper_id);
CREATE INDEX IF NOT EXISTS idx_highlights_paper ON highlights(paper_id);
CREATE INDEX IF NOT EXISTS idx_collections_paper ON collections(paper_id);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_date_added ON papers(date_added);
"""


def create_database(db_path=DB_PATH):
    """Create a fresh database with the schema."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Created database: {db_path}")
    return db_path


def migrate_from_html(db_path=DB_PATH):
    """Migrate data from current paper_database.html + notes.json into SQLite."""
    base_dir = os.path.dirname(db_path)
    html_path = os.path.join(base_dir, "paper_database.html")
    notes_path = os.path.join(base_dir, "notes.json")

    if not os.path.exists(html_path):
        print(f"Error: {html_path} not found")
        sys.exit(1)

    # --- Extract PAPERS array from HTML ---
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    # Find the PAPERS array
    m = re.search(r"const PAPERS\s*=\s*\[", html)
    if not m:
        print("Error: Could not find PAPERS array in HTML")
        sys.exit(1)

    # Find the matching closing bracket
    start = m.start()
    bracket_start = html.index("[", start)
    depth = 0
    end = bracket_start
    for i, c in enumerate(html[bracket_start:], bracket_start):
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    papers_js = html[bracket_start:end]

    # Convert JS object notation to JSON
    # Add quotes around unquoted keys
    papers_json = re.sub(r"(?m)^\s*(\w+)\s*:", r'"\1":', papers_js)
    # Fix nested object keys in figures arrays
    papers_json = re.sub(r"(?<=\{)\s*(\w+)\s*:", r'"\1":', papers_json)
    # Handle trailing commas (arrays and objects)
    papers_json = re.sub(r",\s*([}\]])", r"\1", papers_json)
    # Replace single quotes with double quotes (careful with apostrophes)
    # Actually, the JS uses double quotes already for strings

    try:
        papers = json.loads(papers_json)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print("Falling back to manual extraction...")
        papers = extract_papers_manually(html)

    # --- Extract FIGS mapping ---
    figs = {}
    figs_match = re.search(r"const FIGS\s*=\s*\{([^}]+)\}", html)
    if figs_match:
        for line in figs_match.group(1).strip().split("\n"):
            m = re.match(r'\s*"([^"]+)":\s*"([^"]+)"', line)
            if m:
                figs[m.group(1)] = m.group(2)

    # --- Load notes.json ---
    notes_data = {}
    if os.path.exists(notes_path):
        with open(notes_path, encoding="utf-8") as f:
            notes_data = json.load(f)

    papers_state = notes_data.get("papers", {})
    edit_history = notes_data.get("noteEditHistory", {})

    # --- Build database ---
    if os.path.exists(db_path):
        os.rename(db_path, db_path + ".bak")
        print(f"Backed up existing db to {db_path}.bak")

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    for p in papers:
        pid = p.get("id", "")
        tags = json.dumps(p.get("tags", []))
        key_results = json.dumps(p.get("keyResults", []))

        conn.execute(
            """
            INSERT OR REPLACE INTO papers
            (id, title, authors, short_authors, year, group_name, date_added,
             tags, summary, key_results, cite_bib, cite_txt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                pid,
                p.get("title", ""),
                p.get("authors", ""),
                p.get("shortAuthors", ""),
                p.get("year", 0),
                p.get("group", ""),
                p.get("dateAdded", ""),
                tags,
                p.get("summary", ""),
                key_results,
                p.get("citeBib", ""),
                p.get("citeTxt", ""),
            ),
        )

        # Figures
        for i, fig in enumerate(p.get("figures", [])):
            key = fig.get("key", "")
            file_path = figs.get(key, f"figures/extracted/{key}.jpg")
            conn.execute(
                """
                INSERT OR REPLACE INTO figures (paper_id, figure_key, file_path, label, caption, sort_order)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (pid, key, file_path, fig.get("label", ""), fig.get("desc", ""), i),
            )

        # State from notes.json
        ps = papers_state.get(pid, {})

        # Notes
        note_content = ps.get("notes", "")
        last_edited = edit_history.get(pid, "")
        if note_content or last_edited:
            conn.execute(
                """
                INSERT OR REPLACE INTO notes (paper_id, content, last_edited)
                VALUES (?, ?, ?)
            """,
                (pid, note_content, last_edited),
            )

        # Read status + priority
        conn.execute(
            """
            INSERT OR REPLACE INTO read_status (paper_id, is_read, priority)
            VALUES (?, ?, ?)
        """,
            (pid, 1 if ps.get("read") else 0, ps.get("priority", 0)),
        )

        # Highlights
        for hl in ps.get("highlights", []):
            conn.execute(
                """
                INSERT INTO highlights (paper_id, text, page, color)
                VALUES (?, ?, ?, ?)
            """,
                (pid, hl.get("text", ""), hl.get("page"), hl.get("color", "#58a6ff")),
            )

        # Manual links
        for linked_id in ps.get("manualLinks", []):
            a, b = sorted([pid, linked_id])
            conn.execute(
                """
                INSERT OR IGNORE INTO paper_links (paper_a, paper_b)
                VALUES (?, ?)
            """,
                (a, b),
            )

        # Collections
        for coll_name in ps.get("collections", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO collections (name, paper_id)
                VALUES (?, ?)
            """,
                (coll_name, pid),
            )

    conn.commit()

    # --- Index existing PDFs for FTS ---
    pdfs_dir = os.path.join(base_dir, "pdfs")
    if os.path.exists(pdfs_dir):
        try:
            import fitz  # PyMuPDF

            pdf_count = 0
            for fname in os.listdir(pdfs_dir):
                if not fname.lower().endswith(".pdf"):
                    continue
                paper_id = fname.rsplit(".", 1)[0].replace("_", ".")
                fpath = os.path.join(pdfs_dir, fname)
                try:
                    doc = fitz.open(fpath)
                    for page_num in range(len(doc)):
                        text = doc[page_num].get_text()
                        if text.strip():
                            conn.execute(
                                """
                                INSERT INTO pdf_text (paper_id, page_num, content)
                                VALUES (?, ?, ?)
                            """,
                                (paper_id, str(page_num + 1), text),
                            )
                    doc.close()
                    pdf_count += 1
                except Exception as e:
                    print(f"  Warning: could not index {fname}: {e}")
            conn.commit()
            if pdf_count:
                print(f"Indexed {pdf_count} PDFs for full-text search")
        except ImportError:
            print("  PyMuPDF not installed, skipping PDF indexing (pip install PyMuPDF)")

    # Also try to import from existing search_index.json
    index_path = os.path.join(base_dir, "search_index.json")
    if os.path.exists(index_path):
        with open(index_path) as f:
            idx = json.load(f)
        idx_papers = idx.get("papers", {})
        if idx_papers:
            for safe_id, data in idx_papers.items():
                paper_id = safe_id.replace("_", ".")
                for pg in data.get("pages", []):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO pdf_text (paper_id, page_num, content)
                        VALUES (?, ?, ?)
                    """,
                        (paper_id, str(pg.get("p", "")), pg.get("t", "")),
                    )
            conn.commit()
            print(f"Imported search index for {len(idx_papers)} papers")

    conn.close()
    print(f"\nMigration complete: {len(papers)} papers → {db_path}")
    print(f"Database size: {os.path.getsize(db_path):,} bytes")


def extract_papers_manually(html):
    """Fallback: extract papers by finding each object in the PAPERS array."""
    papers = []
    # Find each paper block between { and the matching }
    m = re.search(r"const PAPERS\s*=\s*\[", html)
    if not m:
        return papers

    pos = m.end()
    while True:
        # Find next opening brace
        idx = html.find("{", pos)
        if idx == -1:
            break
        # Check if we've passed the closing ] of PAPERS
        closing = html.find("];", pos)
        if closing != -1 and closing < idx:
            break

        # Find matching closing brace
        depth = 0
        end = idx
        for i in range(idx, len(html)):
            if html[i] == "{":
                depth += 1
            elif html[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        obj_str = html[idx:end]
        paper = parse_js_object(obj_str)
        if paper and "id" in paper:
            papers.append(paper)
        pos = end

    return papers


def parse_js_object(js_str):
    """Parse a JS object literal into a Python dict (handles common cases)."""
    # Strategy: convert to valid JSON step by step
    s = js_str.strip()

    # Unquoted keys → quoted keys
    s = re.sub(r"(?m)(?<=\{|\,)\s*(\w+)\s*:", r'"\1":', s)

    # Trailing commas
    s = re.sub(r",\s*([}\]])", r"\1", s)

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def show_stats(db_path=DB_PATH):
    """Print database statistics."""
    if not os.path.exists(db_path):
        print(f"No database found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    print(f"Database: {db_path}")
    print(f"Size: {os.path.getsize(db_path):,} bytes\n")

    tables = [
        ("papers", "Papers"),
        ("figures", "Figures"),
        ("notes", "Notes (with content)"),
        ("highlights", "Highlights"),
        ("collections", "Collection memberships"),
        ("paper_links", "Paper links"),
        ("read_status", "Read status entries"),
    ]
    for table, label in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {label}: {count}")

    # FTS stats
    fts_count = conn.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0]
    print(f"  FTS indexed papers: {fts_count}")

    pdf_pages = conn.execute("SELECT COUNT(*) FROM pdf_text").fetchone()[0]
    pdf_papers = conn.execute("SELECT COUNT(DISTINCT paper_id) FROM pdf_text").fetchone()[0]
    print(f"  PDF text pages: {pdf_pages} (from {pdf_papers} papers)")

    # Collections breakdown
    colls = conn.execute("SELECT name, COUNT(*) FROM collections GROUP BY name").fetchall()
    if colls:
        print("\n  Collections:")
        for name, count in colls:
            print(f"    {name}: {count} papers")

    conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Initialize SCQ papers database")
    parser.add_argument("--migrate", action="store_true", help="Migrate from HTML + notes.json")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    parser.add_argument("--db", default=DB_PATH, help="Database path")
    args = parser.parse_args(argv)

    if args.stats:
        show_stats(args.db)
    elif args.migrate:
        migrate_from_html(args.db)
    else:
        create_database(args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
