-- 002_patents.sql — patents as a first-class entity alongside papers.
--
-- Patents are deliberately a SEPARATE table from `papers`: their primary
-- text (claims) is legalese that gets transformed into plain-English
-- summaries, and their valuable signal (assignee, classification codes,
-- independent claims) doesn't map cleanly onto the paper schema.
--
-- Tables:
--   patents        canonical patent record (one row per patent number)
--   patents_fts    FTS5 index over title/abstract/claims/summary
--
-- CPC/IPC classification codes are STORED here (cpc_codes JSON) but not
-- yet used for relevance scoring — that lands in Phase 2 (see
-- plans/patent-scraping.md). Independent claims are stored separately
-- from the full claim list because they define the actual legal scope.
--
-- Source of truth for the patents schema. Applied by
-- scq.db.migrations.apply_pending(). Do not modify after release.

CREATE TABLE IF NOT EXISTS patents (
    -- Canonical patent number, normalized to "<COUNTRY><NUMBER><KIND>"
    -- e.g. "US10374134B2". This is the dedupe key.
    number TEXT PRIMARY KEY,
    country TEXT DEFAULT 'US',          -- ISO country prefix (US, EP, WO, ...)
    doc_number TEXT NOT NULL,           -- digits only, e.g. "10374134"
    kind_code TEXT DEFAULT '',          -- e.g. "B2", "A1"
    is_application INTEGER DEFAULT 0,   -- 1 = published application, 0 = granted

    title TEXT NOT NULL DEFAULT '',
    abstract TEXT DEFAULT '',

    assignee TEXT DEFAULT '',           -- primary assignee org, e.g. "International Business Machines"
    inventors TEXT DEFAULT '',          -- full inventor string, comma-separated
    short_inventors TEXT DEFAULT '',    -- "Gambetta et al."

    filing_date TEXT DEFAULT '',        -- ISO date (YYYY-MM-DD)
    grant_date TEXT DEFAULT '',         -- ISO date; empty for applications
    pub_date TEXT DEFAULT '',           -- publication date

    claims TEXT DEFAULT '[]',           -- JSON array of {num, text, is_independent}
    independent_claims TEXT DEFAULT '[]',  -- JSON array of strings (the legal scope)
    cpc_codes TEXT DEFAULT '[]',        -- JSON array of CPC/IPC code strings
    cites TEXT DEFAULT '[]',            -- JSON array of cited patent numbers
    cited_by TEXT DEFAULT '[]',         -- JSON array of citing patent numbers

    url TEXT DEFAULT '',                -- canonical link (Google Patents / USPTO)
    source TEXT DEFAULT '',             -- provider: patentsview | epo | google
    tags TEXT DEFAULT '[]',             -- JSON array of strings

    -- Plain-English summary fields (filled by the summarize-patent skill /
    -- Phase 2 automation). plain_summary = what it does; protected_scope =
    -- plain reading of the independent claims; prior_art_note = what it
    -- builds on / distinguishes from.
    plain_summary TEXT DEFAULT '',
    protected_scope TEXT DEFAULT '',
    prior_art_note TEXT DEFAULT '',

    date_added TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- FTS5 over the searchable text. External-content table mirrors `patents`
-- by rowid (same pattern as papers_fts in 001_initial.sql).
CREATE VIRTUAL TABLE IF NOT EXISTS patents_fts USING fts5(
    number, title, abstract, claims, plain_summary, protected_scope,
    content=patents,
    content_rowid=rowid,
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS patents_ai AFTER INSERT ON patents BEGIN
    INSERT INTO patents_fts(rowid, number, title, abstract, claims, plain_summary, protected_scope)
    VALUES (new.rowid, new.number, new.title, new.abstract, new.claims, new.plain_summary, new.protected_scope);
END;

CREATE TRIGGER IF NOT EXISTS patents_ad AFTER DELETE ON patents BEGIN
    INSERT INTO patents_fts(patents_fts, rowid, number, title, abstract, claims, plain_summary, protected_scope)
    VALUES ('delete', old.rowid, old.number, old.title, old.abstract, old.claims, old.plain_summary, old.protected_scope);
END;

CREATE TRIGGER IF NOT EXISTS patents_au AFTER UPDATE ON patents BEGIN
    INSERT INTO patents_fts(patents_fts, rowid, number, title, abstract, claims, plain_summary, protected_scope)
    VALUES ('delete', old.rowid, old.number, old.title, old.abstract, old.claims, old.plain_summary, old.protected_scope);
    INSERT INTO patents_fts(rowid, number, title, abstract, claims, plain_summary, protected_scope)
    VALUES (new.rowid, new.number, new.title, new.abstract, new.claims, new.plain_summary, new.protected_scope);
END;

CREATE INDEX IF NOT EXISTS idx_patents_assignee ON patents(assignee);
CREATE INDEX IF NOT EXISTS idx_patents_grant_date ON patents(grant_date);
CREATE INDEX IF NOT EXISTS idx_patents_date_added ON patents(date_added);
