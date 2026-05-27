/**
 * Patents tab — fetch-by-number (keyless via Google), browse stored
 * patents, and a dormant PatentsView keyword search.
 *
 * Talks to the local server through src/services/patents.js (which calls
 * the /api/patents/{add,list} endpoints + the PatentsView proxy). Like the
 * other scraper modules, it exports nothing directly — public functions are
 * shimmed onto globalThis so the data-action registry in scraper/main.js
 * and the keydown ("...OnEnter") delegate can reach them by bare name.
 *
 * The browser can't run the Python ingest or reach patent sites (CORS), so
 * all fetching + storing happens server-side; this module is purely UI.
 */

import { addPatent as svcAdd, listPatents, searchPatentsView } from '../../services/patents.js';

function esc(s) {
  return String(s ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
}

function setStatus(id, text, kind = '') {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = text;
    el.className = `status${kind ? ' ' + kind : ''}`;
  }
}

// ─── Fetch-by-number + add ───

async function addPatent() {
  const input = document.getElementById('patent-input');
  const number = (input?.value || '').trim();
  if (!number) {
    setStatus('patent-status', 'Enter a patent number or Google Patents URL.');
    input?.focus();
    return;
  }
  const source = document.getElementById('patent-source')?.value || 'google';
  const btn = document.getElementById('patent-add-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Fetching...'; }
  setStatus('patent-status', `Fetching ${number} from ${source}...`);
  try {
    const rec = await svcAdd(number, source);
    setStatus(
      'patent-status',
      `Added ${rec.number}: ${rec.title || '(untitled)'} — ${rec.assignee || 'unknown assignee'}. `
        + 'Use the summarize-patent skill to add a plain-English summary.',
      'success',
    );
    if (input) input.value = '';
    await renderPatentsList();
  } catch (err) {
    setStatus('patent-status', `Could not add patent: ${err.message}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Fetch & Add'; }
  }
}

/** Add a specific number (used by the keyword-search result buttons). */
async function addPatentNumber(number) {
  const input = document.getElementById('patent-input');
  if (input) input.value = number;
  await addPatent();
}

// ─── Browse stored patents ───

async function renderPatentsList(query) {
  const container = document.getElementById('patent-list');
  if (!container) return;
  try {
    const rows = await listPatents({ query: query || undefined });
    if (rows.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>No patents stored yet. Fetch one above.</p></div>';
      return;
    }
    container.innerHTML = rows.map((p) => _storedCard(p)).join('');
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p>Could not load patents: ${esc(err.message)}</p></div>`;
  }
}

function _storedCard(p) {
  const cpc = Array.isArray(p.cpc_codes) ? p.cpc_codes.slice(0, 4) : [];
  const summaryBadge = p.has_summary
    ? '<span class="card-tag auto">summarized</span>'
    : '<span class="card-tag" style="color:var(--orange)">needs summary</span>';
  const url = `https://patents.google.com/patent/${encodeURIComponent(p.number)}`;
  return `
    <div class="paper-card">
      <div class="card-top">
        <div class="card-info">
          <div class="card-title">${esc(p.title || p.number)}</div>
          <div class="card-meta">
            <span class="card-id">${esc(p.number)}</span>
            <span>${esc(p.assignee || 'Unknown assignee')}</span>
            ${p.grant_date ? `<span>${esc(p.grant_date)}</span>` : ''}
            <span class="card-source">${esc((p.source || '').toUpperCase())}</span>
          </div>
          <div class="card-tags">
            ${summaryBadge}
            ${cpc.map((c) => `<span class="card-tag">${esc(c)}</span>`).join('')}
          </div>
        </div>
        <div class="card-actions">
          <a href="${esc(url)}" target="_blank" class="btn btn-sm btn-outline" data-action="stopPropagation" style="text-decoration:none">&nearr;</a>
        </div>
      </div>
    </div>`;
}

// ─── PatentsView keyword search (dormant until a key is stored) ───

async function searchPatents() {
  const query = (document.getElementById('patent-search-input')?.value || '').trim();
  if (!query) {
    setStatus('patent-search-status', 'Enter a keyword query.');
    return;
  }
  const btn = document.getElementById('patent-search-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Searching...'; }
  setStatus('patent-search-status', `Searching PatentsView for "${query}"...`);
  const container = document.getElementById('patent-search-results');
  try {
    const rows = await searchPatentsView(query);
    if (rows.length === 0) {
      setStatus('patent-search-status', 'No results.', '');
      if (container) container.innerHTML = '';
    } else {
      setStatus('patent-search-status', `Found ${rows.length} patents`, 'success');
      if (container) container.innerHTML = rows.map((r) => _searchCard(r)).join('');
    }
  } catch (err) {
    // The common case: no API key stored yet → the dormant path.
    setStatus('patent-search-status', err.message, 'error');
    if (container) container.innerHTML = '';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Search'; }
  }
}

function _searchCard(r) {
  return `
    <div class="paper-card">
      <div class="card-top">
        <div class="card-info">
          <div class="card-title">${esc(r.title || r.number)}</div>
          <div class="card-meta">
            <span class="card-id">${esc(r.number)}</span>
            <span>${esc(r.assignee || 'Unknown assignee')}</span>
            ${r.grant_date ? `<span>${esc(r.grant_date)}</span>` : ''}
          </div>
        </div>
        <div class="card-actions">
          <button class="btn btn-sm btn-green" data-action="addPatentNumber" data-number="${esc(r.number)}">+ Add</button>
        </div>
      </div>
    </div>`;
}

// ─── Filter box (debounced) ───

let _filterTimer = null;
function _initFilter() {
  const filter = document.getElementById('patent-filter');
  if (!filter || filter._wired) return;
  filter._wired = true;
  filter.addEventListener('input', () => {
    clearTimeout(_filterTimer);
    _filterTimer = setTimeout(() => renderPatentsList(filter.value.trim()), 250);
  });
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initFilter);
  } else {
    _initFilter();
  }
}

// ─── globalThis shims ───

globalThis.addPatent = addPatent;
globalThis.addPatentNumber = addPatentNumber;
globalThis.renderPatentsList = renderPatentsList;
globalThis.searchPatents = searchPatents;
globalThis.refreshPatents = () => renderPatentsList(
  (document.getElementById('patent-filter')?.value || '').trim(),
);

export { addPatent, addPatentNumber, renderPatentsList, searchPatents };
