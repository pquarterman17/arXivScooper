/**
 * Search tab — query across configured sources, render results, stage
 * selections to the inbox.
 *
 * Extracted from paper_scraper.html boot block (lines ~384–916 pre-refactor)
 * as part of plan #9 Phase B. Exports nothing directly — every public
 * function is shimmed onto globalThis so the data-action registry,
 * the boot block's init(), and the saved-queries module can all
 * reach them by bare name.
 *
 * **Cross-module dependencies (read via globalThis bare-name fallthrough
 * at call time):**
 *   - state: searchResults, selectedIdxs, inbox, existingIds, activeSources,
 *            lastFetchTime (boot block declares all as `var`)
 *   - boot-block helpers: CFG (= SCRAPER_CONFIG), autoTag, esc,
 *            showStatusError
 *   - cors-fetch.js: corsFetch (shimmed onto globalThis at import)
 *   - inbox-persistence.js: saveInbox (shimmed onto globalThis at import)
 *   - tabs.js: updateInboxBadge (shimmed onto globalThis at import)
 *
 * Strict-mode note: modules are always strict, so bare assignments to
 * undeclared identifiers throw ReferenceError. The 4 reassignments to
 * cross-module state vars (searchResults, lastFetchTime) use explicit
 * `globalThis.<name> = ...` to write through to the boot block's slot.
 * Bare reads work because identifier resolution walks to globalThis
 * naturally.
 */

import { stripHtmlTags } from '../../services/crossref.js';

// ─── Sort helpers ───

function getArxivSortParams(sortValue) {
  switch (sortValue) {
    case 'relevance':  return 'sortBy=relevance&sortOrder=descending';
    case 'updated':    return 'sortBy=lastUpdatedDate&sortOrder=descending';
    case 'date-asc':   return 'sortBy=submittedDate&sortOrder=ascending';
    case 'date-desc':
    default:           return 'sortBy=submittedDate&sortOrder=descending';
  }
}

function applySortToResults(results, sortValue) {
  switch (sortValue) {
    case 'relevance':
    case 'updated':
      return results;  // server-side sort already applied
    case 'date-asc':
      return results.sort((a, b) => (a.published || '').localeCompare(b.published || ''));
    case 'author':
      return results.sort((a, b) => (a.shortAuthors || '').localeCompare(b.shortAuthors || ''));
    case 'date-desc':
    default:
      return results.sort((a, b) => (b.published || '').localeCompare(a.published || ''));
  }
}

// ─── Search across configured sources ───

async function doSearch() {
  const query = document.getElementById('search-input').value.trim();
  const status = document.getElementById('search-status');
  if (!query) {
    status.textContent = 'Enter a query above and click Search (or press Enter).';
    status.className = 'status';
    document.getElementById('search-input').focus();
    return;
  }
  const activeKeys = Object.keys(activeSources).filter(k => activeSources[k]);
  if (activeKeys.length === 0) {
    status.textContent = 'No sources enabled — toggle at least one source under the search bar.';
    status.className = 'status error';
    return;
  }

  const btn = document.getElementById('search-btn');
  btn.disabled = true;
  btn.textContent = 'Searching...';
  status.textContent = `Searching ${activeKeys.length} source(s) for "${query}"...`;
  status.className = 'status';

  globalThis.searchResults = [];
  selectedIdxs.clear();

  const sortValue = document.getElementById('sort-order').value;
  const promises = [];
  for (const [key, src] of Object.entries(CFG.sources)) {
    if (!activeSources[key]) continue;
    if (src.type === 'arxiv') promises.push(searchArxiv(query, sortValue));
    else if (src.type === 'arxiv-jr') promises.push(searchPhysRev(query, key, sortValue));
    else if (src.type === 'crossref') promises.push(searchCrossref(query, key, sortValue));
  }

  try {
    const results = await Promise.allSettled(promises);
    results.forEach(r => {
      if (r.status === 'fulfilled') searchResults.push(...r.value);
    });

    applySortToResults(searchResults, sortValue);

    const seen = new Set();
    globalThis.searchResults = searchResults.filter(r => {
      if (seen.has(r.id)) return false;
      seen.add(r.id);
      return true;
    });

    const yearFrom = parseInt(document.getElementById('year-from').value) || 0;
    const yearTo = parseInt(document.getElementById('year-to').value) || 9999;
    if (yearFrom > 0 || yearTo < 9999) {
      globalThis.searchResults = searchResults.filter(r => {
        const y = parseInt(r.year);
        return !isNaN(y) && y >= yearFrom && y <= yearTo;
      });
    }

    const errors = results.filter(r => r.status === 'rejected');
    if (errors.length > 0 && searchResults.length > 0) {
      status.textContent = `Found ${searchResults.length} papers (some sources had errors)`;
      status.className = 'status';
    } else if (errors.length > 0 && searchResults.length === 0) {
      showStatusError(status, 'All sources failed — check your connection or use start.bat for localhost.',
        errors.map(e => e.reason).join('\n'));
    } else {
      status.textContent = `Found ${searchResults.length} papers`;
      status.className = 'status success';
    }

    globalThis.lastFetchTime = new Date();
    renderSearchResults();
  } catch (err) {
    showStatusError(status, 'Search failed — check your connection.', err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Search';
  }
}

// ─── Per-source fetchers ───

async function searchArxiv(query, sortValue = 'date-desc') {
  const encoded = encodeURIComponent(query);
  const maxResults = CFG.autoFetch?.maxResultsPerQuery || 25;
  const sortParams = getArxivSortParams(sortValue);
  const url = `https://arxiv.org/api/query?search_query=all:${encoded}&start=0&max_results=${maxResults}&${sortParams}`;
  const resp = await corsFetch(url);
  if (!resp.ok) throw new Error(resp.status === 429 ? 'Rate limited — wait a moment and retry' : 'arXiv returned ' + resp.status);
  const xml = await resp.text();
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  const entries = doc.querySelectorAll('entry');
  const papers = [];

  entries.forEach(entry => {
    const idUrl = entry.querySelector('id')?.textContent || '';
    const arxivId = idUrl.replace('http://arxiv.org/abs/', '').replace(/v\d+$/, '');
    const title = (entry.querySelector('title')?.textContent || '').replace(/\s+/g, ' ').trim();
    const summary = (entry.querySelector('summary')?.textContent || '').replace(/\s+/g, ' ').trim();
    const published = entry.querySelector('published')?.textContent || '';
    const year = published ? new Date(published).getFullYear() : '';
    const authors = [...entry.querySelectorAll('author name')].map(n => n.textContent);
    const categories = [...entry.querySelectorAll('category')].map(c => c.getAttribute('term'));

    const shortAuthors = authors.length > 3
      ? authors[0].split(' ').pop() + ' et al.'
      : authors.map(a => a.split(' ').pop()).join(', ');

    papers.push({
      id: arxivId, title, summary, year, published,
      authors: authors.join(', '),
      shortAuthors,
      categories,
      source: 'arxiv',
      url: `https://arxiv.org/abs/${arxivId}`,
      tags: autoTag(title, summary)
    });
  });

  return papers;
}

async function searchPhysRev(query, journal, sortValue = 'date-desc') {
  const src = CFG.sources[journal];
  if (!src || !src.journalRef) throw new Error(`No config for source "${journal}"`);
  const maxResults = CFG.autoFetch?.maxResultsPerQuery || 25;
  const sortParams = getArxivSortParams(sortValue);

  const userQ = encodeURIComponent(query);
  const jrFilter = `jr:${src.journalRef}`;
  const url = `https://arxiv.org/api/query?search_query=all:${userQ}+AND+${jrFilter}&start=0&max_results=${maxResults}&${sortParams}`;

  const resp = await corsFetch(url);
  if (!resp.ok) throw new Error(resp.status === 429 ? 'Rate limited — wait a moment and retry' : `arXiv (${src.label}) returned ${resp.status}`);
  const xml = await resp.text();
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  const entries = doc.querySelectorAll('entry');
  const papers = [];

  entries.forEach(entry => {
    const idUrl = entry.querySelector('id')?.textContent || '';
    const arxivId = idUrl.replace('http://arxiv.org/abs/', '').replace(/v\d+$/, '');
    const title = (entry.querySelector('title')?.textContent || '').replace(/\s+/g, ' ').trim();
    const summary = (entry.querySelector('summary')?.textContent || '').replace(/\s+/g, ' ').trim();
    const published = entry.querySelector('published')?.textContent || '';
    const year = published ? new Date(published).getFullYear() : '';
    const authors = [...entry.querySelectorAll('author name')].map(n => n.textContent);
    const categories = [...entry.querySelectorAll('category')].map(c => c.getAttribute('term'));

    const doiEl = entry.querySelector('doi');
    const doi = doiEl ? doiEl.textContent.trim() : '';

    const jrEl = entry.querySelector('journal_ref');
    const journalRef = jrEl ? jrEl.textContent.trim() : '';

    const shortAuthors = authors.length > 3
      ? authors[0].split(' ').pop() + ' et al.'
      : authors.map(a => a.split(' ').pop()).join(', ');

    papers.push({
      id: arxivId,
      title, summary, year, published,
      authors: authors.join(', '),
      shortAuthors,
      categories,
      source: journal,
      url: `https://arxiv.org/abs/${arxivId}`,
      doi,
      journal: journalRef || src.journalName || '',
      tags: autoTag(title, summary)
    });
  });

  if (papers.length === 0) {
    console.log(`[Scraper] No arXiv results with ${src.label} journal-ref filter for "${query}"`);
  }

  return papers;
}

async function searchCrossref(query, sourceKey, sortValue = 'date-desc') {
  const src = CFG.sources[sourceKey];
  if (!src || !src.issn) throw new Error(`No ISSN config for source "${sourceKey}"`);
  const maxResults = CFG.autoFetch?.maxResultsPerQuery || 25;

  const params = new URLSearchParams({
    query: query,
    filter: `issn:${src.issn}`,
    rows: maxResults.toString(),
    sort: sortValue === 'relevance' ? 'relevance' : 'published',
    order: sortValue === 'date-asc' ? 'asc' : 'desc',
  });

  const url = `https://api.crossref.org/works?${params}`;
  const resp = await corsFetch(url);
  if (!resp.ok) throw new Error(`Crossref (${src.label}) returned ${resp.status}`);

  const data = await resp.json();
  const items = data?.message?.items || [];
  const papers = [];

  items.forEach(item => {
    const doi = item.DOI || '';
    const title = (item.title || [''])[0].replace(/\s+/g, ' ').trim();
    const abstract = stripHtmlTags(item.abstract || '').replace(/\s+/g, ' ').trim();
    const dateParts = item.published?.['date-parts']?.[0] || [];
    const year = dateParts[0] || '';
    const month = String(dateParts[1] || 1).padStart(2, '0');
    const day = String(dateParts[2] || 1).padStart(2, '0');
    const published = year ? `${year}-${month}-${day}` : '';

    const authors = (item.author || []).map(a => `${a.given || ''} ${a.family || ''}`.trim());
    const shortAuthors = authors.length > 3
      ? (authors[0].split(' ').pop() || 'Unknown') + ' et al.'
      : authors.map(a => a.split(' ').pop()).join(', ');

    const journal = (item['container-title'] || [''])[0];
    const volume = item.volume || '';
    const pages = item.page || item['article-number'] || '';

    papers.push({
      id: doi,
      title,
      summary: abstract,
      year,
      published,
      authors: authors.join(', '),
      shortAuthors,
      categories: [],
      source: sourceKey,
      url: `https://doi.org/${doi}`,
      doi,
      journal: journal || src.journalName || '',
      volume,
      pages,
      tags: autoTag(title, abstract),
      isCrossref: true,
    });
  });

  if (papers.length === 0) {
    console.log(`[Scraper] No Crossref results for "${query}" in ${src.label}`);
  }

  return papers;
}

// ─── Render results + selection actions ───

function renderSearchResults() {
  const container = document.getElementById('search-results');

  if (searchResults.length === 0) {
    const presetBtns = CFG.presets.map(p =>
      `<button class="preset-btn" data-action="usePreset" data-query="${esc(p.query)}" title="${esc(p.query)}">${esc(p.label)}</button>`
    ).join('');
    container.innerHTML = `<div class="empty-state">
      <h3>No results</h3>
      <p>Try a different query, adjust filters, or try a preset:</p>
      <div class="preset-hint">${presetBtns}</div>
    </div>`;
    updateBatchBar();
    return;
  }

  container.innerHTML = searchResults.map((r, i) => {
    const inDb = existingIds.has(r.id);
    const inInbox = inbox.some(p => p.id === r.id);
    const sel = selectedIdxs.has(i);
    return `
    <div class="paper-card ${inDb ? 'in-db' : ''} ${sel ? 'selected' : ''}" style="${sel ? 'border-color:var(--green);background:rgba(63,185,80,0.04)' : ''}">
      <div class="card-top">
        <div style="display:flex;gap:10px;align-items:flex-start;flex:1;min-width:0">
          ${!inDb && !inInbox ? `<input type="checkbox" ${sel ? 'checked' : ''} data-action="toggleSelect" data-idx="${i}" style="margin-top:3px;accent-color:var(--green);flex-shrink:0">` : ''}
          <div class="card-info" ${!inDb && !inInbox ? `data-action="toggleSelect" data-idx="${i}" style="cursor:pointer"` : ''}>
            <div class="card-title">${esc(r.title)}</div>
            <div class="card-meta">
              <span class="card-source ${r.source}">${(CFG.sources[r.source] || {}).label || r.source.toUpperCase()}</span>
              <span class="card-id">${esc(r.id)}</span>
              <span>${esc(r.shortAuthors)} (${r.year})</span>
              ${inDb ? '<span class="in-db-badge">In database</span>' : ''}
              ${inInbox ? '<span class="in-db-badge" style="color:var(--orange);border-color:rgba(210,153,34,0.3);background:rgba(210,153,34,0.1)">In inbox</span>' : ''}
            </div>
            <div class="card-abstract collapsed" id="abs-${i}">${esc(r.summary)}</div>
            <button class="toggle-abs" data-action="toggleSearchAbstract" data-idx="${i}">show/hide abstract</button>
            <div class="card-tags">
              ${r.tags.map(t => `<span class="card-tag auto">${esc(t)}</span>`).join('')}
              ${r.categories ? r.categories.slice(0, 3).map(c => `<span class="card-tag">${esc(c)}</span>`).join('') : ''}
            </div>
          </div>
        </div>
        <div class="card-actions">
          ${!inDb && !inInbox ? `<button class="btn btn-sm btn-green" data-action="stageOneStop" data-idx="${i}">+ Inbox</button>` : ''}
          <a href="${esc(r.url)}" target="_blank" class="btn btn-sm btn-outline" data-action="stopPropagation" style="text-decoration:none">&nearr;</a>
        </div>
      </div>
    </div>`;
  }).join('');

  updateBatchBar();
}

function toggleSelect(idx) {
  if (selectedIdxs.has(idx)) selectedIdxs.delete(idx);
  else selectedIdxs.add(idx);
  renderSearchResults();
}

function clearSelection() {
  selectedIdxs.clear();
  renderSearchResults();
}

function updateBatchBar() {
  const bar = document.getElementById('batch-bar');
  document.getElementById('batch-count').textContent = selectedIdxs.size;
  bar.classList.toggle('show', selectedIdxs.size > 0);
}

function stageOne(idx) {
  const paper = searchResults[idx];
  if (!inbox.some(p => p.id === paper.id)) {
    inbox.push({ ...paper, note: '', stagedAt: new Date().toISOString() });
    saveInbox();
    updateInboxBadge();
  }
  renderSearchResults();
}

function stageSelected() {
  [...selectedIdxs].forEach(idx => {
    const paper = searchResults[idx];
    if (!inbox.some(p => p.id === paper.id) && !existingIds.has(paper.id)) {
      inbox.push({ ...paper, note: '', stagedAt: new Date().toISOString() });
    }
  });
  selectedIdxs.clear();
  saveInbox();
  updateInboxBadge();
  renderSearchResults();
}

// ─── globalThis shims ───

globalThis.getArxivSortParams = getArxivSortParams;
globalThis.applySortToResults = applySortToResults;
globalThis.doSearch = doSearch;
globalThis.searchArxiv = searchArxiv;
globalThis.searchPhysRev = searchPhysRev;
globalThis.searchCrossref = searchCrossref;
globalThis.renderSearchResults = renderSearchResults;
globalThis.toggleSelect = toggleSelect;
globalThis.clearSelection = clearSelection;
globalThis.updateBatchBar = updateBatchBar;
globalThis.stageOne = stageOne;
globalThis.stageSelected = stageSelected;
