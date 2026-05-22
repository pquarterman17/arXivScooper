/**
 * Sync the in-memory sql.js DB back to disk via serve.py's
 * /api/save-db endpoint. POSTs the raw SQLite bytes; the server
 * does an atomic write to data/arxiv_scooper.db (or wherever
 * scq.config.paths resolves db_path to).
 *
 * Pure logic — no DOM. UI wiring lives in src/ui/database/save-to-disk.js.
 *
 * Returns the parsed { ok, bytes, path, savedAt } payload on success.
 * Throws an Error with a useful message on any failure (network, HTTP
 * non-200, schema mismatch).
 */

const ENDPOINT = '/api/save-db';

/**
 * @param {Uint8Array} bytes — db.export() output
 * @param {object} [opts]
 * @param {function} [opts.fetch] — fetch impl, defaults to globalThis.fetch
 * @returns {Promise<{ ok: true, bytes: number, path: string, savedAt: string }>}
 */
export async function saveDbToServer(bytes, opts = {}) {
  if (!(bytes instanceof Uint8Array)) {
    throw new TypeError('[database-sync] expected Uint8Array (call db.exportBytes() first)');
  }
  if (bytes.length === 0) {
    throw new Error('[database-sync] empty DB; refusing to save');
  }
  const fetchFn = opts.fetch ?? globalThis.fetch?.bind(globalThis);
  if (!fetchFn) throw new Error('[database-sync] no fetch available');

  const resp = await fetchFn(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: bytes,
  });

  let payload = null;
  try { payload = await resp.json(); } catch { /* may be empty / malformed */ }

  if (!resp.ok) {
    const reason = payload?.error || `HTTP ${resp.status}`;
    throw new Error(`[database-sync] save failed: ${reason}`);
  }
  if (!payload || payload.ok !== true) {
    throw new Error('[database-sync] server response missing ok=true');
  }
  return payload;
}
