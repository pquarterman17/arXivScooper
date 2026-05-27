/**
 * Patents service — pure logic, no DOM.
 *
 * Talks to the local server's patent endpoints (added in scq/server.py):
 *   - POST /api/patents/add   {number, source} → fetch via provider + store
 *   - GET  /api/patents/list  ?q=&limit=        → stored patents
 *   - GET  /api/patents/patent/?q=...           → PatentsView keyword search
 *                                                  (dormant until an API key
 *                                                   is stored; proxy 503s)
 *
 * The browser can't run the Python ingest or reach patent sites (CORS), so
 * fetching + storing happens server-side; this service is a thin typed
 * wrapper. `fetch` is injectable so node/vitest can exercise it without a
 * server (mirrors services/arxiv.js).
 *
 * Usage:
 *   import { addPatent, listPatents, searchPatentsView } from '../services/patents.js';
 *   const rec = await addPatent('US10374134B2', 'google');
 *   const rows = await listPatents({ query: 'tantalum' });
 */

/**
 * Light client-side normalization of a patent number/URL into the canonical
 * "<COUNTRY><DIGITS><KIND>" form. The server does authoritative parsing; this
 * just tidies obvious input (Google Patents URLs, separators) for display and
 * so the inbox/dedupe key is stable. Returns null if no digits are found.
 */
export function normalizePatentNumber(raw) {
  if (typeof raw !== 'string') return null;
  let s = raw.trim();
  if (!s) return null;
  // Pull the number out of a Google Patents URL if pasted whole.
  const urlMatch = s.match(/patents\.google\.com\/patent\/([A-Z]{2}[\dA-Z]+)/i);
  if (urlMatch) s = urlMatch[1];
  const m = s.match(/^([A-Za-z]{2})?\s*([\d,\s/]+)\s*([A-Za-z]\d?)?$/);
  if (!m) return null;
  const country = (m[1] || 'US').toUpperCase();
  const digits = (m[2] || '').replace(/[,\s/]/g, '');
  const kind = (m[3] || '').toUpperCase();
  if (!digits) return null;
  return `${country}${digits}${kind}`;
}

/**
 * Add a patent: server fetches it via the chosen provider and stores it.
 *
 * @param {string} number — patent number or Google Patents URL
 * @param {'google'|'patentsview'} [source]
 * @param {object} [opts]
 * @param {function} [opts.fetch] — fetch impl (default globalThis.fetch)
 * @returns {Promise<object>} the stored patent record
 * @throws {Error} with the server's error message on failure
 */
export async function addPatent(number, source = 'google', opts = {}) {
  const fetchFn = opts.fetch ?? globalThis.fetch?.bind(globalThis);
  if (!fetchFn) throw new Error('[services/patents] no fetch available');
  const resp = await fetchFn('/api/patents/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ number, source }),
  });
  const data = await _json(resp);
  if (!resp.ok || !data.ok) {
    throw new Error(data.error || `add failed (HTTP ${resp.status})`);
  }
  return data.patent;
}

/**
 * List stored patents (newest first), optionally filtered by FTS query.
 *
 * @param {object} [opts]
 * @param {string} [opts.query] — FTS match over title/abstract/claims/summary
 * @param {number} [opts.limit]
 * @param {function} [opts.fetch]
 * @returns {Promise<Array<object>>}
 */
export async function listPatents(opts = {}) {
  const fetchFn = opts.fetch ?? globalThis.fetch?.bind(globalThis);
  if (!fetchFn) throw new Error('[services/patents] no fetch available');
  const params = new URLSearchParams();
  if (opts.query) params.set('q', opts.query);
  if (opts.limit) params.set('limit', String(opts.limit));
  const qs = params.toString();
  const resp = await fetchFn(`/api/patents/list${qs ? `?${qs}` : ''}`);
  const data = await _json(resp);
  if (!resp.ok || !data.ok) {
    throw new Error(data.error || `list failed (HTTP ${resp.status})`);
  }
  return data.patents ?? [];
}

/**
 * Fetch one stored patent's full record (claims + summary fields).
 *
 * @param {string} number
 * @param {object} [opts]
 * @param {function} [opts.fetch]
 * @returns {Promise<object>} the full patent record
 */
export async function getPatent(number, opts = {}) {
  const fetchFn = opts.fetch ?? globalThis.fetch?.bind(globalThis);
  if (!fetchFn) throw new Error('[services/patents] no fetch available');
  const resp = await fetchFn(`/api/patents/get?number=${encodeURIComponent(number)}`);
  const data = await _json(resp);
  if (!resp.ok || !data.ok) {
    throw new Error(data.error || `get failed (HTTP ${resp.status})`);
  }
  return data.patent;
}

/**
 * Keyword-search PatentsView via the /api/patents proxy. This is the
 * "wired but dormant" path: the proxy injects the API key server-side and
 * returns HTTP 503 until a key is stored, in which case we throw a clear
 * "set your key" error the UI can surface.
 *
 * @param {string} query
 * @param {object} [opts]
 * @param {function} [opts.fetch]
 * @param {number} [opts.maxResults]
 * @returns {Promise<Array<{number:string,title:string,assignee:string,grant_date:string}>>}
 */
export async function searchPatentsView(query, opts = {}) {
  const fetchFn = opts.fetch ?? globalThis.fetch?.bind(globalThis);
  if (!fetchFn) throw new Error('[services/patents] no fetch available');
  if (!query || !query.trim()) throw new Error('Enter a search query');
  const size = Math.max(1, Math.min(100, opts.maxResults ?? 25));
  const q = JSON.stringify({ _text_any: { patent_title: query.trim() } });
  const f = JSON.stringify([
    'patent_id',
    'patent_title',
    'patent_date',
    'assignees.assignee_organization',
  ]);
  const o = JSON.stringify({ size });
  const url = `/api/patents/patent/?q=${encodeURIComponent(q)}&f=${encodeURIComponent(f)}&o=${encodeURIComponent(o)}`;
  const resp = await fetchFn(url);
  if (resp.status === 503) {
    const data = await _json(resp);
    throw new Error(
      data.error || 'PatentsView API key not set. Run: scq config set-secret patentsview_api_key',
    );
  }
  if (!resp.ok) throw new Error(`PatentsView search failed (HTTP ${resp.status})`);
  const data = await _json(resp);
  return parsePatentsViewSearch(data);
}

/**
 * Shape a PatentsView /patent/ search payload into lightweight result rows.
 * Pure — exported for testing.
 */
export function parsePatentsViewSearch(payload) {
  const patents = payload?.patents ?? [];
  return patents.map((p) => ({
    number: p.patent_id || '',
    title: (p.patent_title || '').trim(),
    assignee: (p.assignees?.[0]?.assignee_organization || '').trim(),
    grant_date: p.patent_date || '',
  }));
}

// ─── internals ───

async function _json(resp) {
  try {
    return await resp.json();
  } catch {
    return {};
  }
}
