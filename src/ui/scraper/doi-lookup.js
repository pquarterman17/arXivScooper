/**
 * DOI lookup tab — fetch a single paper by DOI / APS URL via CrossRef.
 *
 * Extracted from paper_scraper.html boot block (lines 677–820 pre-extraction)
 * as part of plan #9 Phase B. Three functions, all shimmed onto globalThis.
 *
 * **Cross-module deps (read at call time via globalThis bare-name fallthrough):**
 *   - state: existingIds, inbox
 *   - boot-block helpers: CFG, autoTag, esc
 *   - cors-fetch.js: corsFetch
 *   - inbox-persistence.js: saveInbox
 *   - tabs.js: updateInboxBadge
 *
 * The looked-up paper is parked on `globalThis._doiLookupPaper` between
 * the doDoiLookup render and the user's "+ Inbox" click — single-slot,
 * no concurrency since the UI is single-tab.
 */

import { stripHtmlTags } from '../../services/crossref.js';

async function doDoiLookup() {
  const input = document.getElementById('doi-input').value.trim();
  const status = document.getElementById('doi-status');
  if (!input) {
    status.textContent = 'Enter a DOI or APS URL above and click Lookup (or press Enter).';
    status.className = 'status';
    document.getElementById('doi-input').focus();
    return;
  }

  const btn = document.getElementById('doi-btn');
  btn.disabled = true;
  btn.textContent = 'Looking up...';
  status.textContent = 'Fetching metadata...';
  status.className = 'status';

  try {
    let doi = input;
    const apsMatch = input.match(/journals\.aps\.org\/\w+\/abstract\/(10\.\d{4,}\/\S+)/);
    if (apsMatch) doi = apsMatch[1];
    const doiOrgMatch = input.match(/doi\.org\/(10\.\d{4,}\/\S+)/);
    if (doiOrgMatch) doi = doiOrgMatch[1];
    const rawDoiMatch = doi.match(/(10\.\d{4,}\/\S+)/);
    if (rawDoiMatch) doi = rawDoiMatch[1];

    const url = `https://api.crossref.org/works/${doi}`;
    const resp = await corsFetch(url);
    if (!resp.ok) throw new Error(resp.status === 404 ? 'DOI not found' : `Crossref returned ${resp.status}`);

    const data = await resp.json();
    const item = data?.message;
    if (!item) throw new Error('No data returned for this DOI');

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

    const issns = item.ISSN || [];
    let sourceKey = 'crossref';
    for (const [key, src] of Object.entries(CFG.sources)) {
      if (src.issn && issns.includes(src.issn)) { sourceKey = key; break; }
    }

    const paper = {
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
      journal: journal || '',
      volume,
      pages,
      tags: autoTag(title, abstract),
      isCrossref: true,
    };

    if (existingIds.has(doi)) {
      status.textContent = 'This paper is already in your database.';
      status.className = 'status';
      renderDoiResult(paper, true);
    } else {
      status.textContent = 'Paper found — review and add to inbox:';
      status.className = 'status success';
      renderDoiResult(paper, false);
    }
  } catch (err) {
    status.textContent = `Lookup failed: ${err.message}`;
    status.className = 'status error';
    document.getElementById('doi-result').innerHTML = '';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Lookup';
  }
}

function renderDoiResult(paper, inDb) {
  const container = document.getElementById('doi-result');
  container.innerHTML = `
    <div class="paper-card ${inDb ? 'in-db' : ''}" style="margin-top:12px">
      <div class="card-top">
        <div class="card-info">
          <div class="card-title">${esc(paper.title)}</div>
          <div class="card-meta">
            <span class="card-source ${paper.source}">${(CFG.sources[paper.source] || {}).label || paper.journal || 'Published'}</span>
            <span class="card-id">${esc(paper.doi)}</span>
            <span>${esc(paper.shortAuthors)} (${paper.year})</span>
            ${paper.journal ? `<span>${esc(paper.journal)} ${paper.volume ? esc(paper.volume) : ''}${paper.pages ? ', ' + esc(paper.pages) : ''}</span>` : ''}
            ${inDb ? '<span class="in-db-badge">In database</span>' : ''}
          </div>
          ${paper.summary ? `<div class="card-abstract">${esc(paper.summary)}</div>` : ''}
          <div class="card-tags">
            ${paper.tags.map(t => `<span class="card-tag auto">${esc(t)}</span>`).join('')}
          </div>
        </div>
        <div class="card-actions">
          ${!inDb ? `<button class="btn btn-sm btn-green" data-action="stageDoiPaper">+ Inbox</button>` : ''}
          <a href="${esc(paper.url)}" target="_blank" class="btn btn-sm btn-outline" style="text-decoration:none">&nearr;</a>
        </div>
      </div>
    </div>`;
  globalThis._doiLookupPaper = paper;
}

function stageDoiPaper() {
  const paper = globalThis._doiLookupPaper;
  if (!paper) return;
  if (!inbox.some(p => p.id === paper.id)) {
    inbox.push({ ...paper, note: '', stagedAt: new Date().toISOString() });
    saveInbox();
    updateInboxBadge();
  }
  document.getElementById('doi-status').textContent = 'Added to inbox — switch to the Inbox tab to review.';
  document.getElementById('doi-status').className = 'status success';
  document.getElementById('doi-result').innerHTML = '';
}

globalThis.doDoiLookup = doDoiLookup;
globalThis.renderDoiResult = renderDoiResult;
globalThis.stageDoiPaper = stageDoiPaper;
