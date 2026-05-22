/**
 * SCQ Papers Database — sql.js utility layer
 *
 * Shared by paper_database.html, to_read.html, cite_helper.html.
 * Loads the SQLite database via sql.js WASM, provides query helpers,
 * and manages save/export.
 *
 * Usage:
 *   <script src="https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0/sql-wasm.js"></script>
 *   <script src="scq_data.js"></script>  <!-- base64-embedded database for file:// -->
 *   <script src="db_utils.js"></script>
 *   <script>
 *     SCQ.init().then(() => { ... });
 *   </script>
 */

const SCQ = (function () {
  // Canonical DB location, served by serve.py from data/arxiv_scooper.db.
  // The legacy base64 bootstrap (scq_data.js) was retired — the page must
  // be served over HTTP (file:// no longer supported).
  const DB_FILE = "data/arxiv_scooper.db";
  let db = null;
  let SQL = null;
  let dirty = false;
  let lastSavedAt = null;

  // ─── Initialization ───

  async function init(opts = {}) {
    SQL = await initSqlJs({
      locateFile: f => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0/${f}`
    });
    // Load order:
    //   1. HTTP fetch from data/arxiv_scooper.db (canonical)
    //   2. localStorage cache (offline fallback)
    //   3. Empty database with schema (last resort)
    let loaded = false;

    // 1. HTTP fetch (canonical)
    try {
      const resp = await fetch(DB_FILE + "?" + Date.now());
      if (resp.ok) {
        const buf = await resp.arrayBuffer();
        db = new SQL.Database(new Uint8Array(buf));
        loaded = true;
        console.log("[SCQ] Loaded database from " + DB_FILE + " (" + (buf.byteLength / 1024).toFixed(0) + "KB)");
      } else {
        console.warn("[SCQ] " + DB_FILE + " returned HTTP " + resp.status);
      }
    } catch (e) {
      console.warn("[SCQ] fetch failed — page may be loaded via file://. Run via serve.py.", e.message);
    }

    // 2. localStorage cache (offline fallback)
    if (!loaded) {
      const cached = localStorage.getItem("scq-db-base64");
      if (cached) {
        try {
          const binary = atob(cached);
          const bytes = new Uint8Array(binary.length);
          for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
          db = new SQL.Database(bytes);
          loaded = true;
          console.log("[SCQ] Loaded database from localStorage cache");
        } catch (e) {
          console.warn("[SCQ] localStorage cache corrupted:", e.message);
        }
      }
    }

    // 3. Last resort: empty database
    if (!loaded) {
      console.warn("[SCQ] No database found — creating empty database");
      db = new SQL.Database();
      _initSchema();
    }

    // Run migrations for existing databases
    _migrateSchema();

    // Cache to localStorage as fallback
    _cacheToLocalStorage();
    lastSavedAt = Date.now();
    dirty = false;

    if (opts.onReady) opts.onReady();
    return db;
  }

  function _initSchema() {
    db.run(`
      CREATE TABLE IF NOT EXISTS papers (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL,
        short_authors TEXT, year INTEGER, journal TEXT DEFAULT '',
        volume TEXT DEFAULT '', pages TEXT DEFAULT '', doi TEXT DEFAULT '',
        arxiv_id TEXT DEFAULT '', url TEXT DEFAULT '', group_name TEXT DEFAULT '',
        date_added TEXT DEFAULT '', tags TEXT DEFAULT '[]', summary TEXT DEFAULT '',
        key_results TEXT DEFAULT '[]', cite_bib TEXT DEFAULT '', cite_txt TEXT DEFAULT '',
        pdf_path TEXT DEFAULT '',
        entry_type TEXT DEFAULT 'preprint',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
      );
      CREATE TABLE IF NOT EXISTS figures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        figure_key TEXT NOT NULL, file_path TEXT NOT NULL,
        label TEXT DEFAULT '', caption TEXT DEFAULT '', sort_order INTEGER DEFAULT 0,
        UNIQUE(paper_id, figure_key)
      );
      CREATE TABLE IF NOT EXISTS notes (
        paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
        content TEXT DEFAULT '', last_edited TEXT DEFAULT ''
      );
      CREATE TABLE IF NOT EXISTS highlights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        text TEXT NOT NULL, page INTEGER DEFAULT NULL,
        color TEXT DEFAULT '#58a6ff', created_at TEXT DEFAULT (datetime('now'))
      );
      CREATE TABLE IF NOT EXISTS collections (
        name TEXT NOT NULL,
        paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        PRIMARY KEY (name, paper_id)
      );
      CREATE TABLE IF NOT EXISTS paper_links (
        paper_a TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        paper_b TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (paper_a, paper_b), CHECK (paper_a < paper_b)
      );
      CREATE TABLE IF NOT EXISTS read_status (
        paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
        is_read INTEGER DEFAULT 0, priority INTEGER DEFAULT 0
      );
      CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
        id, title, authors, summary, tags, key_results,
        content=papers, content_rowid=rowid, tokenize='porter unicode61'
      );
      CREATE VIRTUAL TABLE IF NOT EXISTS pdf_text USING fts5(
        paper_id, page_num, content, tokenize='porter unicode61'
      );
      CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT '{}'
      );
    `);
  }

  function _migrateSchema() {
    // v2: add entry_type column to papers
    try {
      const cols = db.exec("PRAGMA table_info(papers)");
      if (cols.length > 0) {
        const colNames = cols[0].values.map(r => r[1]);
        if (!colNames.includes("entry_type")) {
          db.run("ALTER TABLE papers ADD COLUMN entry_type TEXT DEFAULT 'preprint'");
          console.log("[SCQ] Migration: added entry_type column");
        }
      }
    } catch (e) {
      console.warn("[SCQ] Migration check failed:", e.message);
    }
    // v3: add settings table
    try {
      db.run("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '{}')");
    } catch (e) {
      // already exists — fine
    }
  }

  // ─── Low-level query helpers ───

  function run(sql, params = []) {
    db.run(sql, params);
    _markDirty();
  }

  function exec(sql, params = []) {
    return db.exec(sql, params);
  }

  /** Run a SELECT and return array of objects */
  function query(sql, params = []) {
    const stmt = db.prepare(sql);
    stmt.bind(params);
    const rows = [];
    while (stmt.step()) {
      rows.push(stmt.getAsObject());
    }
    stmt.free();
    return rows;
  }

  /** Run a SELECT and return first row as object, or null */
  function queryOne(sql, params = []) {
    const rows = query(sql, params);
    return rows.length > 0 ? rows[0] : null;
  }

  /** Run a SELECT and return single scalar value */
  function scalar(sql, params = []) {
    const stmt = db.prepare(sql);
    stmt.bind(params);
    let val = null;
    if (stmt.step()) {
      val = stmt.get()[0];
    }
    stmt.free();
    return val;
  }

  // ─── Paper CRUD ───

  function getAllPapers() {
    return query(`
      SELECT p.*, rs.is_read, rs.priority, n.content AS note, n.last_edited
      FROM papers p
      LEFT JOIN read_status rs ON rs.paper_id = p.id
      LEFT JOIN notes n ON n.paper_id = p.id
      ORDER BY p.date_added DESC
    `);
  }

  function getPaper(id) {
    const p = queryOne(`
      SELECT p.*, rs.is_read, rs.priority, n.content AS note, n.last_edited
      FROM papers p
      LEFT JOIN read_status rs ON rs.paper_id = p.id
      LEFT JOIN notes n ON n.paper_id = p.id
      WHERE p.id = ?
    `, [id]);
    if (p) {
      p.figures = getFigures(id);
      p.highlights = getHighlights(id);
      p.links = getLinkedPapers(id);
      p.collections = getCollectionsForPaper(id);
      p.tags = _jsonParse(p.tags, []);
      p.key_results = _jsonParse(p.key_results, []);
    }
    return p;
  }

  function addPaper(p) {
    run(`
      INSERT OR REPLACE INTO papers
      (id, title, authors, short_authors, year, journal, volume, pages, doi, arxiv_id, url,
       group_name, date_added, tags, summary, key_results, cite_bib, cite_txt, pdf_path, entry_type)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    `, [
      p.id, p.title, p.authors, p.shortAuthors || p.short_authors || "",
      p.year || 0, p.journal || "", p.volume || "", p.pages || "",
      p.doi || "", p.arxiv_id || p.id, p.url || "",
      p.group || p.group_name || "", p.dateAdded || p.date_added || new Date().toISOString().slice(0, 10),
      JSON.stringify(p.tags || []), p.summary || "",
      JSON.stringify(p.keyResults || p.key_results || []),
      p.citeBib || p.cite_bib || "", p.citeTxt || p.cite_txt || "",
      p.pdf_path || "",
      p.entry_type || p.entryType || "preprint"
    ]);

    // Ensure read_status row exists
    run(`INSERT OR IGNORE INTO read_status (paper_id) VALUES (?)`, [p.id]);

    // Update FTS manually since we can't use triggers in sql.js
    _updateFTS(p.id);
  }

  function deletePaper(id) {
    run("DELETE FROM figures WHERE paper_id = ?", [id]);
    run("DELETE FROM notes WHERE paper_id = ?", [id]);
    run("DELETE FROM highlights WHERE paper_id = ?", [id]);
    run("DELETE FROM collections WHERE paper_id = ?", [id]);
    run("DELETE FROM paper_links WHERE paper_a = ? OR paper_b = ?", [id, id]);
    run("DELETE FROM read_status WHERE paper_id = ?", [id]);
    run("DELETE FROM papers WHERE id = ?", [id]);
    // FTS cleanup
    try { run("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')"); } catch(e) {}
  }

  function _updateFTS(paperId) {
    // Rebuild FTS for this paper
    try {
      run("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')");
    } catch (e) {
      console.warn("[SCQ] FTS rebuild warning:", e.message);
    }
  }

  // ─── Figures ───

  function getFigures(paperId) {
    return query("SELECT * FROM figures WHERE paper_id = ? ORDER BY sort_order", [paperId]);
  }

  function addFigure(paperId, key, filePath, label, caption, sortOrder) {
    run(`INSERT OR REPLACE INTO figures (paper_id, figure_key, file_path, label, caption, sort_order)
         VALUES (?, ?, ?, ?, ?, ?)`, [paperId, key, filePath, label || "", caption || "", sortOrder || 0]);
  }

  // ─── Notes ───

  function getNote(paperId) {
    return queryOne("SELECT * FROM notes WHERE paper_id = ?", [paperId]);
  }

  function setNote(paperId, content) {
    const ts = new Date().toISOString();
    run(`INSERT OR REPLACE INTO notes (paper_id, content, last_edited) VALUES (?, ?, ?)`,
        [paperId, content, ts]);
  }

  // ─── Read Status & Priority ───

  function setReadStatus(paperId, isRead) {
    run(`INSERT OR REPLACE INTO read_status (paper_id, is_read, priority)
         VALUES (?, ?, COALESCE((SELECT priority FROM read_status WHERE paper_id = ?), 0))`,
        [paperId, isRead ? 1 : 0, paperId]);
  }

  function setPriority(paperId, priority) {
    run(`INSERT OR REPLACE INTO read_status (paper_id, is_read, priority)
         VALUES (?, COALESCE((SELECT is_read FROM read_status WHERE paper_id = ?), 0), ?)`,
        [paperId, paperId, priority]);
  }

  // ─── Entry Type ───

  function setEntryType(paperId, entryType) {
    run("UPDATE papers SET entry_type = ?, updated_at = datetime('now') WHERE id = ?",
        [entryType, paperId]);
  }

  function getEntryTypes() {
    const rows = query("SELECT DISTINCT entry_type FROM papers WHERE entry_type IS NOT NULL AND entry_type != ''");
    return rows.map(r => r.entry_type);
  }

  // ─── Highlights ───

  function getHighlights(paperId) {
    return query("SELECT * FROM highlights WHERE paper_id = ? ORDER BY id", [paperId]);
  }

  function addHighlight(paperId, text, page, color) {
    run("INSERT INTO highlights (paper_id, text, page, color) VALUES (?, ?, ?, ?)",
        [paperId, text, page || null, color || "#58a6ff"]);
  }

  function removeHighlight(highlightId) {
    run("DELETE FROM highlights WHERE id = ?", [highlightId]);
  }

  // ─── Collections ───

  function getCollections() {
    const rows = query("SELECT DISTINCT name FROM collections ORDER BY name");
    return rows.map(r => r.name);
  }

  function getCollectionPapers(collName) {
    return query(`
      SELECT p.*, rs.is_read, rs.priority
      FROM papers p
      JOIN collections c ON c.paper_id = p.id
      LEFT JOIN read_status rs ON rs.paper_id = p.id
      WHERE c.name = ?
      ORDER BY p.date_added DESC
    `, [collName]);
  }

  function getCollectionsForPaper(paperId) {
    const rows = query("SELECT name FROM collections WHERE paper_id = ?", [paperId]);
    return rows.map(r => r.name);
  }

  function addToCollection(collName, paperId) {
    run("INSERT OR IGNORE INTO collections (name, paper_id) VALUES (?, ?)", [collName, paperId]);
  }

  function removeFromCollection(collName, paperId) {
    run("DELETE FROM collections WHERE name = ? AND paper_id = ?", [collName, paperId]);
  }

  function renameCollection(oldName, newName) {
    run("UPDATE collections SET name = ? WHERE name = ?", [newName, oldName]);
  }

  function deleteCollection(collName) {
    run("DELETE FROM collections WHERE name = ?", [collName]);
  }

  // ─── Paper Links ───

  function getLinkedPapers(paperId) {
    return query(`
      SELECT p.id, p.title, p.short_authors, p.year
      FROM paper_links pl
      JOIN papers p ON (p.id = CASE WHEN pl.paper_a = ? THEN pl.paper_b ELSE pl.paper_a END)
      WHERE pl.paper_a = ? OR pl.paper_b = ?
    `, [paperId, paperId, paperId]);
  }

  function linkPapers(idA, idB) {
    const [a, b] = [idA, idB].sort();
    run("INSERT OR IGNORE INTO paper_links (paper_a, paper_b) VALUES (?, ?)", [a, b]);
  }

  function unlinkPapers(idA, idB) {
    const [a, b] = [idA, idB].sort();
    run("DELETE FROM paper_links WHERE paper_a = ? AND paper_b = ?", [a, b]);
  }

  // ─── Tags ───

  function getAllTags() {
    const papers = query("SELECT tags FROM papers");
    const tagCounts = {};
    papers.forEach(p => {
      _jsonParse(p.tags, []).forEach(t => {
        tagCounts[t] = (tagCounts[t] || 0) + 1;
      });
    });
    return tagCounts;
  }

  function renameTags(oldTag, newTag) {
    const papers = query("SELECT id, tags FROM papers");
    papers.forEach(p => {
      const tags = _jsonParse(p.tags, []);
      const idx = tags.indexOf(oldTag);
      if (idx !== -1) {
        tags[idx] = newTag;
        // Dedupe
        const unique = [...new Set(tags)];
        run("UPDATE papers SET tags = ? WHERE id = ?", [JSON.stringify(unique), p.id]);
      }
    });
    _updateFTS();
  }

  function deleteTag(tag) {
    const papers = query("SELECT id, tags FROM papers");
    papers.forEach(p => {
      const tags = _jsonParse(p.tags, []);
      const filtered = tags.filter(t => t !== tag);
      if (filtered.length !== tags.length) {
        run("UPDATE papers SET tags = ? WHERE id = ?", [JSON.stringify(filtered), p.id]);
      }
    });
    _updateFTS();
  }

  // ─── Search ───

  function searchPapers(queryStr) {
    if (!queryStr || !queryStr.trim()) return getAllPapers();

    const q = queryStr.trim();
    // Use FTS for matching
    const ftsResults = new Set();
    try {
      // Escape special FTS characters
      const ftsQ = q.replace(/['"(){}[\]*:^~!@#$%&]/g, " ").trim();
      if (ftsQ) {
        const ftsRows = query(
          `SELECT id FROM papers_fts WHERE papers_fts MATCH ?`,
          [ftsQ + "*"]
        );
        ftsRows.forEach(r => ftsResults.add(r.id));
      }
    } catch (e) {
      // FTS query failed, fall back to LIKE
      console.warn("[SCQ] FTS search failed, using LIKE fallback:", e.message);
    }

    // Also do LIKE search for partial matches FTS might miss
    const likeQ = "%" + q + "%";
    const likeRows = query(`
      SELECT id FROM papers
      WHERE title LIKE ? OR authors LIKE ? OR summary LIKE ? OR tags LIKE ? OR short_authors LIKE ?
    `, [likeQ, likeQ, likeQ, likeQ, likeQ]);
    likeRows.forEach(r => ftsResults.add(r.id));

    if (ftsResults.size === 0) return [];

    const ids = [...ftsResults];
    const placeholders = ids.map(() => "?").join(",");
    return query(`
      SELECT p.*, rs.is_read, rs.priority, n.content AS note, n.last_edited
      FROM papers p
      LEFT JOIN read_status rs ON rs.paper_id = p.id
      LEFT JOIN notes n ON n.paper_id = p.id
      WHERE p.id IN (${placeholders})
      ORDER BY p.date_added DESC
    `, ids);
  }

  function searchPdfText(queryStr) {
    if (!queryStr || !queryStr.trim()) return {};
    const q = queryStr.trim().replace(/['"(){}[\]*:^~!@#$%&]/g, " ").trim();
    if (!q) return {};
    const hits = {};
    try {
      const rows = query(
        `SELECT paper_id, page_num, snippet(pdf_text, 2, '<mark>', '</mark>', '...', 40) AS snip
         FROM pdf_text WHERE pdf_text MATCH ?
         LIMIT 100`,
        [q + "*"]
      );
      rows.forEach(r => {
        if (!hits[r.paper_id]) hits[r.paper_id] = { page: r.page_num, snippet: r.snip };
      });
    } catch (e) {
      console.warn("[SCQ] PDF text search failed:", e.message);
    }
    return hits;
  }

  function hasPdfIndex() {
    return scalar("SELECT COUNT(*) FROM pdf_text") > 0;
  }

  // ─── Stats ───

  function getStats() {
    return {
      papers: scalar("SELECT COUNT(*) FROM papers"),
      read: scalar("SELECT COUNT(*) FROM read_status WHERE is_read = 1"),
      unread: scalar("SELECT COUNT(*) FROM read_status WHERE is_read = 0 OR is_read IS NULL"),
      figures: scalar("SELECT COUNT(*) FROM figures"),
      collections: scalar("SELECT COUNT(DISTINCT name) FROM collections"),
      pdfPages: scalar("SELECT COUNT(*) FROM pdf_text"),
    };
  }

  // ─── Persistence ───

  function _markDirty() {
    dirty = true;
    _debouncedCache();
    if (typeof window._scqOnDirty === "function") window._scqOnDirty(dirty);
  }

  let _cacheTimer = null;
  function _debouncedCache() {
    clearTimeout(_cacheTimer);
    _cacheTimer = setTimeout(() => _cacheToLocalStorage(), 3000);
  }

  function _cacheToLocalStorage() {
    try {
      const data = db.export();
      const binary = String.fromCharCode.apply(null, data);
      // Check if it fits (localStorage typically 5-10MB)
      if (binary.length < 4 * 1024 * 1024) {
        localStorage.setItem("scq-db-base64", btoa(binary));
      } else {
        console.warn("[SCQ] Database too large for localStorage cache (" +
                     (binary.length / 1048576).toFixed(1) + "MB)");
      }
    } catch (e) {
      console.warn("[SCQ] localStorage cache failed:", e.message);
    }
  }

  function saveToFile() {
    const data = db.export();
    const blob = new Blob([data], { type: "application/x-sqlite3" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = DB_FILE;
    a.click();
    URL.revokeObjectURL(a.href);
    dirty = false;
    lastSavedAt = Date.now();
    if (typeof window._scqOnDirty === "function") window._scqOnDirty(false);
  }

  function exportJSON() {
    const papers = getAllPapers();
    const result = { papers: {}, collections: getCollections(), exportedAt: new Date().toISOString() };
    papers.forEach(p => {
      result.papers[p.id] = {
        notes: p.note || "",
        read: !!p.is_read,
        priority: p.priority || 0,
        entryType: p.entry_type || "preprint",
        dateAdded: p.date_added,
        collections: getCollectionsForPaper(p.id),
        manualLinks: getLinkedPapers(p.id).map(l => l.id),
        highlights: getHighlights(p.id).map(h => ({ text: h.text, page: h.page, color: h.color }))
      };
    });
    return result;
  }

  function isDirty() { return dirty; }
  function getDB() { return db; }

  // ─── Helpers ───

  function _jsonParse(str, fallback) {
    try { return JSON.parse(str); } catch { return fallback; }
  }

  function formatRelativeTime(isoString) {
    if (!isoString) return "";
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    const days = Math.floor(hrs / 24);
    if (days < 30) return days + "d ago";
    return new Date(isoString).toLocaleDateString();
  }

  // ─── Merge / Import ───

  /**
   * Merge another .db file into the current database.
   * Conflict resolution mirrors tools/merge_database.py:
   *  - New papers: inserted wholesale
   *  - Existing papers: longer summary wins, tag union, newer note wins,
   *    blank fields filled from other, max priority, OR read status
   *  - Highlights, collections, links: union with dedup
   *
   * @param {Uint8Array} otherDbBytes — raw bytes of the other .db file
   * @returns {{ added: number, updated: number, skipped: number }}
   */
  function mergeFromDatabase(otherDbBytes) {
    const other = new SQL.Database(otherDbBytes);
    const stats = { added: 0, updated: 0, skipped: 0 };

    // Helper to query the other db
    function oQuery(sql, params = []) {
      const stmt = other.prepare(sql);
      stmt.bind(params);
      const rows = [];
      while (stmt.step()) rows.push(stmt.getAsObject());
      stmt.free();
      return rows;
    }
    function oScalar(sql, params = []) {
      const stmt = other.prepare(sql);
      stmt.bind(params);
      let val = null;
      if (stmt.step()) val = stmt.get()[0];
      stmt.free();
      return val;
    }

    // 1. Merge papers
    const otherPapers = oQuery("SELECT * FROM papers");
    for (const op of otherPapers) {
      const existing = queryOne("SELECT * FROM papers WHERE id = ?", [op.id]);

      if (!existing) {
        // New paper — insert it
        run(`INSERT INTO papers (id, title, authors, short_authors, year, journal, volume,
             pages, doi, arxiv_id, url, group_name, date_added, tags, summary,
             key_results, cite_bib, cite_txt, pdf_path, entry_type, created_at, updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, [
          op.id, op.title, op.authors, op.short_authors, op.year, op.journal,
          op.volume, op.pages, op.doi, op.arxiv_id, op.url, op.group_name,
          op.date_added, op.tags, op.summary, op.key_results, op.cite_bib,
          op.cite_txt, op.pdf_path, op.entry_type || 'preprint', op.created_at, op.updated_at
        ]);
        run("INSERT OR IGNORE INTO read_status (paper_id) VALUES (?)", [op.id]);
        stats.added++;
      } else {
        // Existing paper — merge fields
        let changed = false;

        // Longer summary wins
        const newSummary = (op.summary || "").length > (existing.summary || "").length
          ? op.summary : existing.summary;

        // Tag union
        const existingTags = _jsonParse(existing.tags, []);
        const otherTags = _jsonParse(op.tags, []);
        const mergedTags = [...new Set([...existingTags, ...otherTags])];

        // Key results union
        const existingKR = _jsonParse(existing.key_results, []);
        const otherKR = _jsonParse(op.key_results, []);
        const mergedKR = [...new Set([...existingKR, ...otherKR])];

        // Fill blank fields from other
        const fillFields = ["journal", "volume", "pages", "doi", "url", "group_name",
                            "cite_bib", "cite_txt", "pdf_path", "entry_type"];
        const updates = {};
        fillFields.forEach(f => {
          if ((!existing[f] || existing[f] === "") && op[f] && op[f] !== "") {
            updates[f] = op[f];
            changed = true;
          }
        });

        if (newSummary !== existing.summary) changed = true;
        if (JSON.stringify(mergedTags) !== JSON.stringify(existingTags)) changed = true;
        if (JSON.stringify(mergedKR) !== JSON.stringify(existingKR)) changed = true;

        if (changed) {
          // Build dynamic UPDATE
          let setClauses = ["summary = ?", "tags = ?", "key_results = ?", "updated_at = datetime('now')"];
          let params = [newSummary, JSON.stringify(mergedTags), JSON.stringify(mergedKR)];
          for (const [k, v] of Object.entries(updates)) {
            setClauses.push(k + " = ?");
            params.push(v);
          }
          params.push(op.id);
          run("UPDATE papers SET " + setClauses.join(", ") + " WHERE id = ?", params);
          stats.updated++;
        } else {
          stats.skipped++;
        }
      }
    }

    // 2. Merge read_status (OR logic for is_read, max priority)
    const otherRS = oQuery("SELECT * FROM read_status");
    for (const ors of otherRS) {
      const existing = queryOne("SELECT * FROM read_status WHERE paper_id = ?", [ors.paper_id]);
      if (!existing) {
        run("INSERT OR IGNORE INTO read_status (paper_id, is_read, priority) VALUES (?,?,?)",
            [ors.paper_id, ors.is_read, ors.priority]);
      } else {
        const newRead = (existing.is_read || ors.is_read) ? 1 : 0;
        const newPriority = Math.max(existing.priority || 0, ors.priority || 0);
        if (newRead !== existing.is_read || newPriority !== existing.priority) {
          run("UPDATE read_status SET is_read = ?, priority = ? WHERE paper_id = ?",
              [newRead, newPriority, ors.paper_id]);
        }
      }
    }

    // 3. Merge notes (newer wins by last_edited timestamp)
    const otherNotes = oQuery("SELECT * FROM notes");
    for (const on of otherNotes) {
      const existing = queryOne("SELECT * FROM notes WHERE paper_id = ?", [on.paper_id]);
      if (!existing) {
        run("INSERT INTO notes (paper_id, content, last_edited) VALUES (?,?,?)",
            [on.paper_id, on.content, on.last_edited]);
      } else {
        const existDate = existing.last_edited ? new Date(existing.last_edited).getTime() : 0;
        const otherDate = on.last_edited ? new Date(on.last_edited).getTime() : 0;
        if (otherDate > existDate && on.content) {
          run("UPDATE notes SET content = ?, last_edited = ? WHERE paper_id = ?",
              [on.content, on.last_edited, on.paper_id]);
        }
      }
    }

    // 4. Merge highlights (dedup by paper_id + text)
    const otherHL = oQuery("SELECT * FROM highlights");
    for (const oh of otherHL) {
      const existing = queryOne(
        "SELECT id FROM highlights WHERE paper_id = ? AND text = ?",
        [oh.paper_id, oh.text]);
      if (!existing) {
        run("INSERT INTO highlights (paper_id, text, page, color) VALUES (?,?,?,?)",
            [oh.paper_id, oh.text, oh.page, oh.color]);
      }
    }

    // 5. Merge figures (dedup by paper_id + figure_key)
    const otherFigs = oQuery("SELECT * FROM figures");
    for (const of_ of otherFigs) {
      const existing = queryOne(
        "SELECT id FROM figures WHERE paper_id = ? AND figure_key = ?",
        [of_.paper_id, of_.figure_key]);
      if (!existing) {
        run(`INSERT INTO figures (paper_id, figure_key, file_path, label, caption, sort_order)
             VALUES (?,?,?,?,?,?)`,
            [of_.paper_id, of_.figure_key, of_.file_path, of_.label, of_.caption, of_.sort_order]);
      }
    }

    // 6. Merge collections
    const otherColl = oQuery("SELECT * FROM collections");
    for (const oc of otherColl) {
      run("INSERT OR IGNORE INTO collections (name, paper_id) VALUES (?, ?)",
          [oc.name, oc.paper_id]);
    }

    // 7. Merge paper_links
    const otherLinks = oQuery("SELECT * FROM paper_links");
    for (const ol of otherLinks) {
      // Only insert if both papers exist in our db
      const hasA = queryOne("SELECT id FROM papers WHERE id = ?", [ol.paper_a]);
      const hasB = queryOne("SELECT id FROM papers WHERE id = ?", [ol.paper_b]);
      if (hasA && hasB) {
        run("INSERT OR IGNORE INTO paper_links (paper_a, paper_b) VALUES (?, ?)",
            [ol.paper_a, ol.paper_b]);
      }
    }

    // Rebuild FTS
    try { run("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')"); } catch(e) {}

    // Cache updated db
    _cacheToLocalStorage();

    other.close();
    return stats;
  }

  /**
   * Export a named collection as a standalone .db file (downloaded).
   * Includes all related data: papers, figures, notes, highlights, read_status.
   *
   * @param {string} collName — collection name to export
   * @returns {number} — number of papers exported
   */
  function exportCollectionDB(collName) {
    // Get paper IDs in this collection
    const collPapers = query("SELECT paper_id FROM collections WHERE name = ?", [collName]);
    if (collPapers.length === 0) return 0;

    const ids = collPapers.map(r => r.paper_id);
    const placeholders = ids.map(() => "?").join(",");

    // Create a fresh database with the same schema
    const expDb = new SQL.Database();
    expDb.run(`
      CREATE TABLE papers (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL,
        short_authors TEXT, year INTEGER, journal TEXT DEFAULT '',
        volume TEXT DEFAULT '', pages TEXT DEFAULT '', doi TEXT DEFAULT '',
        arxiv_id TEXT DEFAULT '', url TEXT DEFAULT '', group_name TEXT DEFAULT '',
        date_added TEXT DEFAULT '', tags TEXT DEFAULT '[]', summary TEXT DEFAULT '',
        key_results TEXT DEFAULT '[]', cite_bib TEXT DEFAULT '', cite_txt TEXT DEFAULT '',
        pdf_path TEXT DEFAULT '',
        entry_type TEXT DEFAULT 'preprint',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
      );
      CREATE TABLE figures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL, figure_key TEXT NOT NULL, file_path TEXT NOT NULL,
        label TEXT DEFAULT '', caption TEXT DEFAULT '', sort_order INTEGER DEFAULT 0,
        UNIQUE(paper_id, figure_key)
      );
      CREATE TABLE notes (
        paper_id TEXT PRIMARY KEY, content TEXT DEFAULT '', last_edited TEXT DEFAULT ''
      );
      CREATE TABLE highlights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL, text TEXT NOT NULL, page INTEGER DEFAULT NULL,
        color TEXT DEFAULT '#58a6ff', created_at TEXT DEFAULT (datetime('now'))
      );
      CREATE TABLE collections (
        name TEXT NOT NULL, paper_id TEXT NOT NULL, PRIMARY KEY (name, paper_id)
      );
      CREATE TABLE paper_links (
        paper_a TEXT NOT NULL, paper_b TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (paper_a, paper_b), CHECK (paper_a < paper_b)
      );
      CREATE TABLE read_status (
        paper_id TEXT PRIMARY KEY, is_read INTEGER DEFAULT 0, priority INTEGER DEFAULT 0
      );
    `);

    // Copy papers
    const papers = query("SELECT * FROM papers WHERE id IN (" + placeholders + ")", ids);
    for (const p of papers) {
      expDb.run(`INSERT INTO papers (id, title, authors, short_authors, year, journal, volume,
        pages, doi, arxiv_id, url, group_name, date_added, tags, summary,
        key_results, cite_bib, cite_txt, pdf_path, entry_type, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, [
        p.id, p.title, p.authors, p.short_authors, p.year, p.journal,
        p.volume, p.pages, p.doi, p.arxiv_id, p.url, p.group_name,
        p.date_added, p.tags, p.summary, p.key_results, p.cite_bib,
        p.cite_txt, p.pdf_path, p.entry_type || 'preprint', p.created_at, p.updated_at
      ]);
    }

    // Copy figures
    const figs = query("SELECT * FROM figures WHERE paper_id IN (" + placeholders + ")", ids);
    for (const f of figs) {
      expDb.run(`INSERT INTO figures (paper_id, figure_key, file_path, label, caption, sort_order)
        VALUES (?,?,?,?,?,?)`, [f.paper_id, f.figure_key, f.file_path, f.label, f.caption, f.sort_order]);
    }

    // Copy notes
    const notes = query("SELECT * FROM notes WHERE paper_id IN (" + placeholders + ")", ids);
    for (const n of notes) {
      expDb.run("INSERT INTO notes (paper_id, content, last_edited) VALUES (?,?,?)",
        [n.paper_id, n.content, n.last_edited]);
    }

    // Copy highlights
    const hls = query("SELECT * FROM highlights WHERE paper_id IN (" + placeholders + ")", ids);
    for (const h of hls) {
      expDb.run("INSERT INTO highlights (paper_id, text, page, color) VALUES (?,?,?,?)",
        [h.paper_id, h.text, h.page, h.color]);
    }

    // Copy read_status
    const rs = query("SELECT * FROM read_status WHERE paper_id IN (" + placeholders + ")", ids);
    for (const r of rs) {
      expDb.run("INSERT INTO read_status (paper_id, is_read, priority) VALUES (?,?,?)",
        [r.paper_id, r.is_read, r.priority]);
    }

    // Copy collections (only this collection)
    for (const id of ids) {
      expDb.run("INSERT INTO collections (name, paper_id) VALUES (?,?)", [collName, id]);
    }

    // Copy paper_links (only between papers in this collection)
    const links = query(`SELECT * FROM paper_links
      WHERE paper_a IN (${placeholders}) AND paper_b IN (${placeholders})`, [...ids, ...ids]);
    for (const l of links) {
      expDb.run("INSERT INTO paper_links (paper_a, paper_b) VALUES (?,?)", [l.paper_a, l.paper_b]);
    }

    // Download the exported db
    const data = expDb.export();
    const blob = new Blob([data], { type: "application/x-sqlite3" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = collName.replace(/[^a-zA-Z0-9_-]/g, "_") + ".db";
    a.click();
    URL.revokeObjectURL(a.href);
    expDb.close();

    return ids.length;
  }

  /**
   * Import a .db file — reads from a File object, merges, and returns stats.
   * Convenience wrapper for use with <input type="file">.
   *
   * @param {File} file — the .db File from a file input
   * @returns {Promise<{ added: number, updated: number, skipped: number }>}
   */
  async function importDatabaseFile(file) {
    const buf = await file.arrayBuffer();
    return mergeFromDatabase(new Uint8Array(buf));
  }

  // ─── Settings (key-value store) ───

  function getSetting(key) {
    try {
      const row = queryOne("SELECT value FROM settings WHERE key = ?", [key]);
      return row ? JSON.parse(row.value) : null;
    } catch (e) {
      return null;
    }
  }

  function setSetting(key, value) {
    run("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", [key, JSON.stringify(value)]);
  }

  function getAllSettings() {
    try {
      const rows = query("SELECT key, value FROM settings");
      const out = {};
      rows.forEach(r => { try { out[r.key] = JSON.parse(r.value); } catch {} });
      return out;
    } catch (e) {
      return {};
    }
  }

  // ─── Public API ───

  return {
    init, getDB, isDirty, saveToFile, exportJSON, formatRelativeTime,
    // Low-level
    run, exec, query, queryOne, scalar,
    // Papers
    getAllPapers, getPaper, addPaper, deletePaper, searchPapers,
    // Figures
    getFigures, addFigure,
    // Notes
    getNote, setNote,
    // Read status
    setReadStatus, setPriority,
    // Entry type
    setEntryType, getEntryTypes,
    // Highlights
    getHighlights, addHighlight, removeHighlight,
    // Collections
    getCollections, getCollectionPapers, getCollectionsForPaper,
    addToCollection, removeFromCollection, renameCollection, deleteCollection,
    // Links
    getLinkedPapers, linkPapers, unlinkPapers,
    // Tags
    getAllTags, renameTags, deleteTag,
    // Search
    searchPdfText, hasPdfIndex,
    // Stats
    getStats,
    // Merge / Collaboration
    mergeFromDatabase, exportCollectionDB, importDatabaseFile,
    // Settings
    getSetting, setSetting, getAllSettings,
  };
})();

// Expose SCQ as a property on the global object (window in browsers,
// globalThis in workers). The `const SCQ = ...` above only creates a
// binding in the global *lexical environment*, not a property on
// `window`/`globalThis` — so ES modules (which can't see lexical globals
// from regular scripts) couldn't reach it. The new src/ui/database/*.js
// modules need this to call SCQ.getDB() etc.
if (typeof window !== 'undefined') {
  window.SCQ = SCQ;
}