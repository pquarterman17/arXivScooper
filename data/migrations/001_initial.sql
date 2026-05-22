-- 001_initial.sql — initial schema for arXivScooper's paper database.
--
-- Tables:
--   papers         core paper metadata
--   figures        per-paper figure references (file paths + captions)
--   notes          user notes with edit timestamps
--   highlights     annotation highlights with page references
--   collections    named collections of papers
--   paper_links    bidirectional manual links between papers
--   read_status    read/unread flag + priority stars
--   papers_fts     FTS5 virtual table for paper metadata search
--   pdf_text       FTS5 virtual table for full-text PDF content search
--
-- Source of truth for schema. Applied by scq.db.migrations.apply_pending().
-- Do not modify after release; create 002_*.sql etc. for changes.

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
    entry_type TEXT DEFAULT 'preprint',  -- preprint | published | website | release | thesis | review
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
    priority INTEGER DEFAULT 0      -- 0-3 stars
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

-- Key/value settings store (UI prefs, cached state)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '{}'  -- JSON-encoded
);

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_figures_paper ON figures(paper_id);
CREATE INDEX IF NOT EXISTS idx_highlights_paper ON highlights(paper_id);
CREATE INDEX IF NOT EXISTS idx_collections_paper ON collections(paper_id);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_date_added ON papers(date_added);
