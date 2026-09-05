/**
 * Add Website / Link modal (plan #8 strangler-fig migration).
 *
 * Three entry points are reachable from inline onclick attributes:
 *   - showAddWebsiteModal — opens the modal
 *   - fetchWebsiteMeta    — populates fields from a URL (DOI / arXiv detection)
 *   - submitAddWebsite    — validates + persists, then closes
 *
 * `lookupDOI`, `generateDOIBibTeX`, and `generateDOIPlainText` are
 * module-internal CrossRef helpers. They produce structured metadata +
 * Physical Review-style citations.
 *
 * External legacy globals still consumed via `globalThis`:
 *   - SCRAPER_CONFIG   (scraper_config.js — entryTypes, tag dictionary)
 *   - arxivFetch       (db_utils.js — proxy-aware fetch)
 *   - closeModal, loadPapersFromDB, render (legacy boot block)
 *   - SCQ              (sql.js wrapper, db_utils.js)
 */

import { lookupByDoi } from '../../services/crossref.js';

function _scq() { return globalThis.SCQ; }
function _call(name, ...args) {
  const fn = globalThis[name];
  if (typeof fn === 'function') return fn(...args);
}

// Proxy-aware fetch: try the local /api/crossref/<doi> proxy first, then
// fall back to api.crossref.org. The crossref service accepts an injected
// fetch via opts.fetch, so we hand it this wrapper.
async function _proxyFetch(url) {
  const m = url.match(/api\.crossref\.org\/works\/(.+)/);
  if (m) {
    try {
      const proxied = await fetch(`${window.location.origin}/api/crossref/${m[1]}`);
      if (proxied.ok || proxied.status === 404) return proxied;
    } catch { /* fall through */ }
  }
  return fetch(url);
}

export function showAddWebsiteModal() {
  const root = document.getElementById('modal-root');
  const cfg = globalThis.SCRAPER_CONFIG;
  const entryTypes = (cfg && cfg.entryTypes) ? cfg.entryTypes : {};
  const typeOptions = Object.entries(entryTypes).map(([k, v]) =>
    `<option value="${k}" ${k === 'website' ? 'selected' : ''}>${v.label || k}</option>`
  ).join('');

  root.innerHTML = `<div class="modal-overlay" onclick="closeModal()">
    <div class="modal wide" onclick="event.stopPropagation()">
      <h3>Add Link / Website</h3>
      <label class="field-label">URL</label>
      <div style="display:flex;gap:6px;margin-bottom:4px">
        <input type="text" id="awm-url" placeholder="https://..." style="margin-bottom:0;flex:1">
        <button class="modal-btn primary" onclick="fetchWebsiteMeta()" style="white-space:nowrap">Fetch info</button>
      </div>
      <div class="fetch-status" id="awm-status"></div>
      <label class="field-label">Title</label>
      <input type="text" id="awm-title" placeholder="Page title">
      <label class="field-label">Authors / Source</label>
      <input type="text" id="awm-authors" placeholder="Author or site name">
      <label class="field-label">Description / Summary</label>
      <textarea id="awm-summary" placeholder="Brief description or notes"></textarea>
      <label class="field-label">Type</label>
      <select id="awm-type">${typeOptions}</select>
      <label class="field-label">Tags (comma-separated)</label>
      <input type="text" id="awm-tags" placeholder="e.g. tantalum, fabrication">
      <label class="field-label">Notes</label>
      <textarea id="awm-notes" placeholder="Your personal notes (optional)" style="min-height:40px"></textarea>
      <div class="modal-btns">
        <button class="modal-btn" onclick="closeModal()">Cancel</button>
        <button class="modal-btn primary" onclick="submitAddWebsite()">Add to database</button>
      </div>
    </div>
  </div>`;
  document.getElementById('awm-url').focus();
}

function _autoTag(text) {
  const cfg = globalThis.SCRAPER_CONFIG;
  if (!cfg || !cfg.tags) return [];
  const lower = text.toLowerCase();
  const matched = [];
  for (const [tag, keywords] of Object.entries(cfg.tags)) {
    if (keywords.some(kw => lower.includes(kw.toLowerCase()))) matched.push(tag);
  }
  return matched;
}

function _setVal(id, value) { document.getElementById(id).value = value; }
function _setStatus(text) { document.getElementById('awm-status').textContent = text; }

export function fetchWebsiteMeta() {
  const url = document.getElementById('awm-url').value.trim();
  if (!url) return;
  _setStatus('Fetching page metadata...');

  const doiMatch = url.match(/(?:doi\.org\/|^)(10\.\S+\/\S+)/) || url.match(/^(10\.\S+\/\S+)$/);
  if (doiMatch) {
    _fetchFromDoi(doiMatch[1]);
    return;
  }

  const arxivMatch = url.match(/arxiv\.org\/(?:abs|pdf)\/(\d{4}\.\d{4,5})/);
  if (arxivMatch) {
    _fetchFromArxiv(arxivMatch[1]);
    return;
  }

  _setStatus('Auto-fetch not available for this URL (CORS). Please fill in details manually.');
  try {
    const domain = new URL(url).hostname.replace('www.', '');
    if (!document.getElementById('awm-authors').value) {
      _setVal('awm-authors', domain);
    }
  } catch (e) { /* ignore invalid URL */ }
}

function _fetchFromDoi(doi) {
  _setStatus('Detected DOI, fetching from CrossRef...');
  lookupByDoi(doi, { fetch: _proxyFetch })
    .then(paper => {
      _setVal('awm-title', paper.title);
      _setVal('awm-authors', paper.authors);
      _setVal('awm-summary',
        (paper.journal || '') +
        (paper.volume ? ` Vol. ${paper.volume}` : '') +
        (paper.pages ? `, pp. ${paper.pages}` : '')
      );
      _setVal('awm-type', 'published');
      const tags = _autoTag(paper.title);
      if (tags.length > 0) _setVal('awm-tags', tags.join(', '));
      _setStatus('Populated from CrossRef API.');
    })
    .catch(e => _setStatus('DOI fetch failed: ' + e.message));
}

function _fetchFromArxiv(arxivId) {
  _setStatus('Detected arXiv paper, fetching from API...');
  globalThis.arxivFetch(`https://arxiv.org/api/query?id_list=${arxivId}`)
    .then(r => r.text())
    .then(xml => {
      const doc = new DOMParser().parseFromString(xml, 'text/xml');
      const entry = doc.querySelector('entry');
      if (!entry) { _setStatus('Could not parse arXiv response.'); return; }
      const title = (entry.querySelector('title')?.textContent || '').replace(/\s+/g, ' ').trim();
      const authors = [...entry.querySelectorAll('author name')].map(n => n.textContent).join(', ');
      const summary = (entry.querySelector('summary')?.textContent || '').replace(/\s+/g, ' ').trim();
      _setVal('awm-title', title);
      _setVal('awm-authors', authors);
      _setVal('awm-summary', summary.substring(0, 500));
      _setVal('awm-type', 'preprint');
      const tags = _autoTag(title + ' ' + summary);
      if (tags.length > 0) _setVal('awm-tags', tags.join(', '));
      _setStatus('Populated from arXiv API.');
    })
    .catch(e => _setStatus('arXiv fetch failed: ' + e.message));
}

// lookupDOI / generateDOIBibTeX / generateDOIPlainText migrated to
// src/services/{crossref,doi}.js (plan #7); _fetchFromDoi calls them
// via the injected _proxyFetch defined above.

export function submitAddWebsite() {
  const url = document.getElementById('awm-url').value.trim();
  const title = document.getElementById('awm-title').value.trim();
  if (!url) { alert('URL is required.'); return; }
  if (!title) { alert('Title is required.'); return; }

  const authors = document.getElementById('awm-authors').value.trim() || 'Unknown';
  const summary = document.getElementById('awm-summary').value.trim();
  const entryType = document.getElementById('awm-type').value;
  const tagsStr = document.getElementById('awm-tags').value.trim();
  const notes = document.getElementById('awm-notes').value.trim();
  const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(Boolean) : [];

  const id = 'web-' + url.replace(/[^a-zA-Z0-9]/g, '').substring(0, 40) + '-' + Date.now().toString(36);
  const dateAdded = new Date().toISOString().slice(0, 10);
  const shortAuthors = authors.split(',')[0].trim();
  const year = new Date().getFullYear();

  const arxivMatch = url.match(/arxiv\.org\/(?:abs|pdf)\/(\d{4}\.\d{4,5})/);
  const arxivId = arxivMatch ? arxivMatch[1] : '';
  const paperId = arxivId || id;

  _scq().addPaper({
    id: paperId, title, authors, shortAuthors,
    year, journal: '', doi: '', arxiv_id: arxivId,
    url, group_name: '', dateAdded,
    tags, summary, keyResults: [],
    citeBib: '', citeTxt: '', pdf_path: '',
    entry_type: entryType,
  });

  if (notes) _scq().setNote(paperId, notes);

  _call('closeModal');
  _call('loadPapersFromDB');
  _call('render');
}
