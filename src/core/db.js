// @ts-check
/**
 * Low-level sql.js layer (ES module).
 *
 * This module is intentionally narrow: connection management and primitive
 * query helpers. NO paper-specific SQL. Paper CRUD, tags, notes, merge,
 * settings, etc. live in `src/services/*.js` and call into here.
 *
 * Loading model: the browser pages preload sql-wasm via a CDN <script> tag,
 * which exposes `initSqlJs` on globalThis. For tests/node, callers can pass
 * `init({ initSqlJs })` explicitly.
 *
 * Persistence model:
 *  - The canonical DB file is `data/arxiv_scooper.db`, served by serve.py and
 *    rewritten by the Python tools in `scq/`.
 *  - In the browser the DB is loaded into memory via sql.js - writes here
 *    are cache-only (localStorage) until the Settings UI (plan item #11)
 *    adds a write-back endpoint.
 *  - `exportBytes()` returns the raw Uint8Array for any layer that needs to
 *    POST or trigger a file download. Triggering a download is a UI concern,
 *    so it does NOT live here.
 *
 * No DOM. Safe to import in node.
 */

import bus from './events.js';

const DB_PATH = 'data/arxiv_scooper.db';
const CACHE_KEY = 'scq-db-base64';
const CACHE_LIMIT_BYTES = 4 * 1024 * 1024;
const CACHE_DEBOUNCE_MS = 3000;

let _db = null;
let _SQL = null;
let _dirty = false;
let _lastSavedAt = null;
let _cacheTimer = null;

/**
 * Initialize sql.js and load the database.
 *
 * @param {object} [opts]
 * @param {function} [opts.initSqlJs] - sql.js loader. Defaults to globalThis.initSqlJs.
 * @param {string}   [opts.dbPath]    - relative path to the .db file. Defaults to 'data/arxiv_scooper.db'.
 * @param {string}   [opts.wasmBaseUrl] - where sql.js can fetch the .wasm. Defaults to a CDN.
 * @param {function} [opts.fetch]     - fetch impl, defaults to globalThis.fetch.
 * @param {object}   [opts.storage]   - localStorage-shaped object for cache; default is globalThis.localStorage.
 * @returns {Promise<object>} the underlying sql.js Database
 */
export async function init(opts = {}) {
  const initSqlJs = opts.initSqlJs ?? globalThis.initSqlJs;
  if (typeof initSqlJs !== 'function') {
    throw new Error(
      '[core/db] initSqlJs not found. Preload sql-wasm.js via <script> or pass it as opts.initSqlJs.',
    );
  }

  const dbPath = opts.dbPath ?? DB_PATH;
  const wasmBaseUrl = opts.wasmBaseUrl
    ?? 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0/';
  const fetchFn = opts.fetch ?? globalThis.fetch?.bind(globalThis);
  const storage = opts.storage ?? globalThis.localStorage;

  _SQL = await initSqlJs({
    locateFile: (f) => wasmBaseUrl + f,
  });

  let loaded = false;

  // 1. HTTP fetch (canonical)
  if (fetchFn) {
    try {
      const resp = await fetchFn(dbPath + '?' + Date.now());
      if (resp.ok) {
        const buf = await resp.arrayBuffer();
        _db = new _SQL.Database(new Uint8Array(buf));
        loaded = true;
        bus.emit('db:loaded', { source: 'http', bytes: buf.byteLength });
      } else {
        console.warn(`[core/db] ${dbPath} returned HTTP ${resp.status}`);
      }
    } catch (e) {
      console.warn('[core/db] HTTP fetch failed; will try cache:', e.message);
    }
  }

  // 2. localStorage cache (offline fallback)
  if (!loaded && storage) {
    const cached = storage.getItem(CACHE_KEY);
    if (cached) {
      try {
        _db = new _SQL.Database(_b64ToBytes(cached));
        loaded = true;
        bus.emit('db:loaded', { source: 'cache' });
      } catch (e) {
        console.warn('[core/db] cache corrupt:', e.message);
      }
    }
  }

  if (!loaded) {
    // Don't silently create an empty DB — that masks "is the server up?"
    // failures. Callers that want fresh-DB behavior can do so explicitly
    // via createEmpty().
    throw new Error(
      `[core/db] Could not load ${dbPath}. Is serve.py running? `
      + 'Run `python -m scq.db init` to create a fresh DB on disk.',
    );
  }

  _scheduleCache(storage);
  _lastSavedAt = Date.now();
  _dirty = false;

  return _db;
}

/** Build an empty in-memory DB. Caller is responsible for running migrations. */
export function createEmpty(opts = {}) {
  const initSqlJs = opts.initSqlJs ?? globalThis.initSqlJs;
  if (!_SQL && typeof initSqlJs === 'function') {
    return initSqlJs().then((SQL) => {
      _SQL = SQL;
      _db = new _SQL.Database();
      bus.emit('db:loaded', { source: 'empty' });
      return _db;
    });
  }
  if (!_SQL) throw new Error('[core/db] sql.js not initialized; call init() first');
  _db = new _SQL.Database();
  bus.emit('db:loaded', { source: 'empty' });
  return _db;
}

/** Load DB from raw bytes (e.g. uploaded file). Replaces the current DB. */
export async function loadFromBytes(bytes, opts = {}) {
  const initSqlJs = opts.initSqlJs ?? globalThis.initSqlJs;
  if (!_SQL) {
    if (typeof initSqlJs !== 'function') throw new Error('[core/db] sql.js not loaded');
    _SQL = await initSqlJs();
  }
  _db = new _SQL.Database(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
  _markDirty(opts.storage);
  bus.emit('db:loaded', { source: 'bytes', bytes: bytes.byteLength ?? bytes.length });
  return _db;
}

/** Run a SELECT, return array of plain objects (one per row). */
export function query(sql, params = []) {
  _ensureDb();
  const stmt = _db.prepare(sql);
  stmt.bind(params);
  const rows = [];
  while (stmt.step()) rows.push(stmt.getAsObject());
  stmt.free();
  return rows;
}

/** Run a SELECT, return the first row (or null). */
export function queryOne(sql, params = []) {
  const rows = query(sql, params);
  return rows.length > 0 ? rows[0] : null;
}

/** Run a SELECT, return a single scalar value (first column of first row). */
export function scalar(sql, params = []) {
  _ensureDb();
  const stmt = _db.prepare(sql);
  stmt.bind(params);
  let val = null;
  if (stmt.step()) val = stmt.get()[0];
  stmt.free();
  return val;
}

/** Run an INSERT/UPDATE/DELETE/etc. Marks the DB dirty. */
export function run(sql, params = []) {
  _ensureDb();
  _db.run(sql, params);
  _markDirty();
}

/** Pass-through to sql.js exec (multiple statements OK; no dirty marking). */
export function exec(sql, params = []) {
  _ensureDb();
  return _db.exec(sql, params);
}

/** Force-flush the localStorage cache now (debounced writes also happen). */
export function save(opts = {}) {
  const storage = opts.storage ?? globalThis.localStorage;
  _writeCache(storage);
  _lastSavedAt = Date.now();
  _dirty = false;
  bus.emit('db:saved', { at: _lastSavedAt });
}

/** Export the current DB as a Uint8Array. Caller decides what to do with it. */
export function exportBytes() {
  _ensureDb();
  return _db.export();
}

export function isDirty() { return _dirty; }
export function getDB() { return _db; }
export function lastSavedAt() { return _lastSavedAt; }

/**
 * Return the loaded sql.js module so services can construct *additional*
 * in-memory DBs (for merge/import + collection export, where the legacy
 * code did `new SQL.Database(otherBytes)`). Throws if init() / createEmpty()
 * / loadFromBytes() hasn't run yet.
 */
export function getSQL() {
  if (!_SQL) throw new Error('[core/db] sql.js not loaded; call init() / createEmpty() first');
  return _SQL;
}

/** Test helper: drop the in-memory DB so a fresh init() can run. */
export function _reset() {
  if (_db) {
    try { _db.close(); } catch (_) { /* sql.js may already be closed */ }
  }
  _db = null;
  _SQL = null;
  _dirty = false;
  _lastSavedAt = null;
  clearTimeout(_cacheTimer);
  _cacheTimer = null;
}

// ─── internals ───

function _ensureDb() {
  if (!_db) throw new Error('[core/db] not initialized; await init() first');
}

function _markDirty(storage) {
  _dirty = true;
  bus.emit('db:dirty', true);
  _scheduleCache(storage ?? globalThis.localStorage);
}

function _scheduleCache(storage) {
  if (!storage) return;
  clearTimeout(_cacheTimer);
  _cacheTimer = setTimeout(() => _writeCache(storage), CACHE_DEBOUNCE_MS);
}

function _writeCache(storage) {
  if (!storage || !_db) return;
  try {
    const bytes = _db.export();
    if (bytes.length > CACHE_LIMIT_BYTES) {
      console.warn(
        `[core/db] DB ${(bytes.length / 1048576).toFixed(1)}MB exceeds cache limit; skipping localStorage backup`,
      );
      return;
    }
    storage.setItem(CACHE_KEY, _bytesToB64(bytes));
  } catch (e) {
    console.warn('[core/db] cache write failed:', e.message);
  }
}

function _bytesToB64(bytes) {
  // Chunk to avoid call-stack overflow on large arrays
  const chunkSize = 0x8000;
  let binary = '';
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function _b64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
