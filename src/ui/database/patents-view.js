/**
 * Patents view — the database-page browse surface for stored patents.
 *
 * A "Patents" main-tab that lists stored patents (newest first, filterable)
 * and expands each into a detail panel showing the plain-English summary
 * fields (plain_summary / protected_scope / prior_art_note) and the
 * independent claims — the legal heart of the patent.
 *
 * Data comes from the server endpoints via src/services/patents.js
 * (GET /api/patents/list + /api/patents/get), NOT the page's sql.js
 * snapshot: the server reads the canonical DB, so a patent added on the
 * scraper page shows up here without a reload.
 *
 * Tab registration: the database page's switchMainTab() lives in the
 * (frozen) inline boot block, so rather than edit it we WRAP the global —
 * showing/hiding #tab-patents and rendering on entry, delegating every
 * other tab to the original. Keeps the boot block untouched per
 * docs/architecture.md.
 *
 * Like the other database modules, public functions are shimmed onto
 * globalThis for the data-action registry + the boot block.
 */

import { listPatents, getPatent } from '../../services/patents.js';

function esc(s) {
  return String(s ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
}

// Full-record cache so re-expanding a card doesn't re-fetch.
const _detailCache = new Map();

// ─── Tab registration (wrap the boot-block global) ───

const _origSwitchMainTab = globalThis.switchMainTab;

function switchMainTabWithPatents(tab) {
  const panel = document.getElementById('tab-patents');
  if (panel) panel.style.display = tab === 'patents' ? '' : 'none';
  if (tab === 'patents') {
    document.querySelectorAll('.main-tab').forEach((b) => b.classList.remove('active'));
    document.getElementById('mtab-patents')?.classList.add('active');
    renderPatentsView();
    return;
  }
  // Delegate every other tab to the original implementation.
  if (typeof _origSwitchMainTab === 'function') _origSwitchMainTab(tab);
}

// ─── List rendering ───

async function renderPatentsView(query) {
  const container = document.getElementById('patents-list');
  if (!container) return;
  container.innerHTML = '<div class="empty-state"><p>Loading patents…</p></div>';
  try {
    const rows = await listPatents({ query: query || undefined });
    const countEl = document.getElementById('patents-count');
    if (countEl) countEl.textContent = String(rows.length);
    if (rows.length === 0) {
      container.innerHTML = `<div class="empty-state">
        <h3>No patents${query ? ' match that filter' : ' stored yet'}</h3>
        <p>Add patents from the scraper's <strong>Patents</strong> tab (keyless, by number),
        then summarize them with the <code>summarize-patent</code> skill.</p>
      </div>`;
      return;
    }
    container.innerHTML = rows.map((p) => _listCard(p)).join('');
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p>Could not load patents: ${esc(err.message)}</p>
      <p style="color:var(--text3)">Is the local server running?</p></div>`;
  }
}

function _listCard(p) {
  const cpc = Array.isArray(p.cpc_codes) ? p.cpc_codes.slice(0, 5) : [];
  const badge = p.has_summary
    ? '<span class="card-tag auto">summarized</span>'
    : '<span class="card-tag" style="color:var(--orange)">needs summary</span>';
  return `
    <div class="paper-card" id="pcard-${esc(p.number)}">
      <div class="card-top" data-action="togglePatentDetail" data-number="${esc(p.number)}" style="cursor:pointer">
        <div class="card-info">
          <div class="card-title">${esc(p.title || p.number)}</div>
          <div class="card-meta">
            <span class="card-id">${esc(p.number)}</span>
            <span>${esc(p.assignee || 'Unknown assignee')}</span>
            ${p.grant_date ? `<span>${esc(p.grant_date)}</span>` : ''}
            <span class="card-source">${esc((p.source || '').toUpperCase())}</span>
          </div>
          <div class="card-tags">
            ${badge}
            ${cpc.map((c) => `<span class="card-tag">${esc(c)}</span>`).join('')}
          </div>
        </div>
        <div class="card-actions">
          <a href="https://patents.google.com/patent/${encodeURIComponent(p.number)}" target="_blank"
             class="btn btn-sm btn-outline" data-action="stopPropagation" style="text-decoration:none">&nearr;</a>
        </div>
      </div>
      <div class="patent-detail" id="pdetail-${esc(p.number)}" style="display:none; padding:8px 4px 4px"></div>
    </div>`;
}

// ─── Detail (lazy) ───

async function togglePatentDetail(number) {
  const panel = document.getElementById(`pdetail-${number}`);
  if (!panel) return;
  if (panel.style.display !== 'none') {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  if (_detailCache.has(number)) {
    panel.innerHTML = _detailHtml(_detailCache.get(number));
    return;
  }
  panel.innerHTML = '<p style="color:var(--text3)">Loading…</p>';
  try {
    const rec = await getPatent(number);
    _detailCache.set(number, rec);
    panel.innerHTML = _detailHtml(rec);
  } catch (err) {
    panel.innerHTML = `<p style="color:var(--red)">Could not load: ${esc(err.message)}</p>`;
  }
}

function _detailHtml(p) {
  const indep = Array.isArray(p.independent_claims) ? p.independent_claims : [];
  const field = (label, val) =>
    `<div style="margin:6px 0"><strong>${label}:</strong> ${esc(val)}</div>`;
  let body = '';
  if (p.has_summary || p.plain_summary) {
    if (p.plain_summary) body += field('What it does', p.plain_summary);
    if (p.protected_scope) body += field('What’s protected', p.protected_scope);
    if (p.prior_art_note) body += field('Prior art', p.prior_art_note);
  } else {
    body += `<div style="color:var(--orange); margin:6px 0">
      Not yet summarized. Run the <code>summarize-patent</code> skill (or
      <code>scq patents show ${esc(p.number)}</code>) to add a plain-English summary.</div>`;
  }
  if (p.abstract) body += field('Abstract', p.abstract);
  if (indep.length) {
    body += `<div style="margin:8px 0 4px"><strong>Independent claims (${indep.length}):</strong></div>`;
    body += indep
      .map(
        (c, i) =>
          `<div style="margin:4px 0; padding-left:10px; border-left:2px solid var(--border)">
             <span style="color:var(--text3)">[${i + 1}]</span> ${esc(c)}</div>`,
      )
      .join('');
  }
  return body || '<p style="color:var(--text3)">No detail available.</p>';
}

// ─── Filter box (debounced) ───

let _filterTimer = null;
function _initFilter() {
  const filter = document.getElementById('patents-filter');
  if (!filter || filter._wired) return;
  filter._wired = true;
  filter.addEventListener('input', () => {
    clearTimeout(_filterTimer);
    _filterTimer = setTimeout(() => renderPatentsView(filter.value.trim()), 250);
  });
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initFilter);
  } else {
    _initFilter();
  }
}

function refreshPatentsView() {
  return renderPatentsView((document.getElementById('patents-filter')?.value || '').trim());
}

// The switchMainTab wrap MUST be a global reassignment — it has to capture
// the boot block's original (read above at import) and replace the global
// the data-action registry calls. The other functions are exposed via the
// BRIDGE object in main.js (the database page's bridge convention), so they
// are only exported here, not shimmed onto globalThis.
globalThis.switchMainTab = switchMainTabWithPatents;

export { renderPatentsView, togglePatentDetail, refreshPatentsView, switchMainTabWithPatents };
