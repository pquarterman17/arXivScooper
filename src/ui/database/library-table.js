/**
 * Library view rendering — sidebar + main content (cards / table / cite).
 * Plan #8 strangler-fig migration.
 *
 *   - renderSidebar(): collections list + per-collection export buttons.
 *   - render(): the top-level redraw. Drives the type-filter bar, tag bar,
 *     and the active main view (cards / table / cite).
 *
 * Reads/writes legacy state through `globalThis` so it interoperates with
 * the boot block and other migrated modules:
 *   reads:  PAPERS, FIGS, SCRAPER_CONFIG, activeCollection, typeFilter,
 *           selectedTags, currentView, expandedId, openDropdownId
 *   writes: pdfSearchHits = {} (reset at the top of every render)
 *
 * Cross-module helpers are looked up via `globalThis` (rather than imported)
 * so we don't introduce import cycles with main.js, which already imports
 * library-table.js to expose render() / renderSidebar() back onto window.
 */

function _g(name) { return globalThis[name]; }

// Escape for use inside an HTML double-quoted attribute value (title="..." etc).
// Replaces the four characters that can break out of an attribute or the
// surrounding HTML: & < > ".
function _attr(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Escape for use inside a *single-quoted* JS string literal embedded in an
// inline onclick="...('${value}')" attribute. Backslashes first, then the
// quote, then a sentinel for </script> for paranoia.
function _js(s) {
  return String(s == null ? '' : s)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/<\/script/gi, '<\\/script');
}

// Escape PCDATA / RCDATA — for inserting user text between tags or inside
// <textarea>. </textarea> is the only thing that closes a textarea, so we
// must encode < at minimum; encode & as well to keep it idempotent.
function _text(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function renderSidebar() {
  const SCQ = globalThis.SCQ;
  const PAPERS = _g('PAPERS') || [];
  const activeCollection = _g('activeCollection');
  const getCollectionNames = _g('getCollectionNames');

  const names = typeof getCollectionNames === 'function' ? getCollectionNames() : [];
  let html = `<div class="sidebar-header">Library</div>
    <div class="sidebar-item ${activeCollection === null ? 'active' : ''}" onclick="setActiveCollection(null)">
      <span class="item-icon">&#128218;</span> All Papers
      <span class="item-count">${PAPERS.length}</span>
    </div>`;

  if (names.length > 0) {
    html += `<div class="sidebar-divider"></div><div class="sidebar-header">Collections</div>`;
    html += names.map(n => {
      const count = SCQ.getCollectionPapers(n).length;
      return `<div class="sidebar-item ${activeCollection === n ? 'active' : ''}" onclick="setActiveCollection('${_js(n)}')">
        <span class="item-icon">&#128193;</span>
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${n}</span>
        <span class="item-count">${count}</span>
      </div>`;
    }).join("");
  }

  html += `<div class="sidebar-divider"></div>
    <button class="add-collection-btn" onclick="showNewCollectionModal()">+ New collection</button>`;

  if (activeCollection) {
    const safeCollName = _js(activeCollection);
    html += `<button class="sidebar-export-btn" onclick="exportCollectionBib('${safeCollName}')">
      &#128229; Export "${activeCollection}" as .bib
    </button>`;
    html += `<button class="sidebar-export-btn" onclick="exportCollectionAsDB('${safeCollName}')" style="border-color:var(--accent);color:var(--accent)">
      &#128230; Share "${activeCollection}" as .db
    </button>`;
    html += `<button class="sidebar-export-btn" onclick="exportCollectionPackage('${safeCollName}')" style="border-color:var(--green);color:var(--green)">
      &#128230; Share "${activeCollection}" as package
    </button>`;
  }

  document.getElementById("sidebar").innerHTML = html;
}

export function render() {
  const SCQ = globalThis.SCQ;
  const PAPERS = _g('PAPERS') || [];
  const FIGS = _g('FIGS') || {};
  const SCRAPER_CONFIG = _g('SCRAPER_CONFIG');
  const activeCollection = _g('activeCollection');
  const typeFilter = _g('typeFilter');
  const selectedTags = _g('selectedTags');
  const currentView = _g('currentView');
  const expandedId = _g('expandedId');
  const openDropdownId = _g('openDropdownId');

  const getFiltered = _g('getFiltered');
  const getAllTags = _g('getAllTags');
  const getRelatedPapers = _g('getRelatedPapers');
  const renderHighlights = _g('renderHighlights');
  const renderStars = _g('renderStars');
  const renderCollectionDropdown = _g('renderCollectionDropdown');
  const sortPapers = _g('sortPapers');
  const sortedClass = _g('sortedClass');
  const sortArrow = _g('sortArrow');
  const getPdfPath = _g('getPdfPath');

  renderSidebar();
  // Show export package button only when a collection is active
  const exportPkgBtn = document.getElementById('export-pkg-btn');
  if (exportPkgBtn) {
    exportPkgBtn.style.display = activeCollection ? 'block' : 'none';
  }
  // Reset PDF search-hit map at the top of every render. Reassign on globalThis
  // so subsequent renders see the cleared object (reading via _g returns the
  // same reference each call).
  globalThis.pdfSearchHits = {};
  const pdfSearchHits = globalThis.pdfSearchHits;
  const filtered = getFiltered();
  document.getElementById("paper-count").textContent = PAPERS.length;
  const collLabel = activeCollection ? (" in \"" + activeCollection + "\"") : "";
  document.getElementById("footer-info").textContent = filtered.length + " of " + PAPERS.length + " papers shown" + collLabel;

  // Type filter bar (only show types that exist in the DB + "All")
  const _typeCounts = {};
  PAPERS.forEach(p => { const t = p.entryType || 'preprint'; _typeCounts[t] = (_typeCounts[t] || 0) + 1; });
  const _etCfg = (SCRAPER_CONFIG && SCRAPER_CONFIG.entryTypes) ? SCRAPER_CONFIG.entryTypes : {};
  let typeBarHtml = `<button class="type-filter-btn ${typeFilter === 'all' ? 'active' : ''}" onclick="setTypeFilter('all')">All</button>`;
  for (const [key, cnt] of Object.entries(_typeCounts).sort((a,b) => b[1] - a[1])) {
    const cfg = _etCfg[key] || {};
    const label = cfg.label || key;
    const activeStyle = typeFilter === key ? ` style="border-color:${cfg.color || 'var(--green)'};background:${cfg.color || 'var(--green)'}22;color:${cfg.color || 'var(--green)'}"` : '';
    typeBarHtml += `<button class="type-filter-btn ${typeFilter === key ? 'active' : ''}"${activeStyle} onclick="setTypeFilter('${key}')">${label} <span style="opacity:0.6">${cnt}</span></button>`;
  }
  document.getElementById('type-filter-bar').innerHTML = typeBarHtml;

  // Tags
  const allTags = getAllTags();
  let tagHtml = allTags.map(t =>
    `<button class="tag-btn ${selectedTags.has(t) ? 'active' : ''}" onclick="toggleTag('${_js(t)}')">${_text(t)}</button>`
  ).join("");
  if (selectedTags.size > 0) tagHtml += `<button class="tag-btn clear" onclick="clearTags()">clear</button>`;
  tagHtml += `<span class="tag-mgmt-bar"><button class="tag-mgmt-btn" onclick="showTagManagerModal()" title="Rename, merge, or delete tags">manage tags</button></span>`;
  document.getElementById("tag-bar").innerHTML = tagHtml;

  const content = document.getElementById("content");

  if (currentView === "cards") {
    content.innerHTML = filtered.map(p => {
      const open = expandedId === p.id;
      const short = p.authors.split(",").slice(0,3).join(",") + (p.authors.split(",").length > 3 ? " et al." : "");
      let html = `<div class="paper ${open ? 'expanded' : ''}">
        <div class="paper-header" onclick="togglePaper('${p.id}')">
          <svg class="chevron ${open ? 'open' : ''}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="m9 18 6-6-6-6"/></svg>
          <div style="flex:1;min-width:0">
            <div class="paper-title">${p.title}</div>
            <div class="paper-meta">${short} — ${p.year}</div>
            <div class="paper-badges">
              <span class="badge badge-arxiv">${p.id}</span>
              ${(() => { const _tc = (_etCfg[p.entryType] || {}); return p.entryType && p.entryType !== 'preprint' ? `<span class="badge badge-type" style="background:${_tc.color || '#888'}22;color:${_tc.color || '#888'}">${_tc.label || p.entryType}</span>` : ''; })()}
              <span class="badge badge-group">${p.group}</span>
              ${pdfSearchHits[p.id] ? `<span class="badge" style="background:rgba(210,153,34,0.15);color:var(--orange)" title="${_attr('p.' + pdfSearchHits[p.id].page + ': ' + pdfSearchHits[p.id].snippet)}">PDF match p.${pdfSearchHits[p.id].page}</span>` : ''}
            </div>
          </div>
          <div class="star-rating" onclick="event.stopPropagation()">${renderStars(p.id)}</div>
          <label class="read-toggle" onclick="event.stopPropagation()">
            <input type="checkbox" ${p._read ? 'checked' : ''} onchange="toggleReadStatus('${p.id}', event)">
            <span class="read-status-label ${p._read ? 'is-read' : ''}">${p._read ? 'Read' : 'Unread'}</span>
          </label>
        </div>`;

      if (open) {
        const hlData = SCQ.getHighlights(p.id);
        html += `<div class="paper-body">
          <div class="section">
            <div class="section-label">Summary</div>
            <div>${p.summary}</div>
          </div>
          <div class="section">
            <div class="section-label">Key Results</div>
            ${p.keyResults.map(r => `<div class="result-item"><span class="result-bullet">›</span><span class="result-text">${r}</span></div>`).join("")}
          </div>
          <div class="section">
            <div class="section-label">Figures</div>
            <div class="fig-grid">
              ${p.figures.map(f => FIGS[f.key] ? `
                <div class="fig-card" onclick="event.stopPropagation(); openLightbox('${_js(f.key)}', '${_js(f.label + ': ' + f.desc)}')">
                  <img src="${FIGS[f.key]}" alt="${_attr(f.label)}" loading="lazy">
                  <div class="fig-label"><strong>${_text(f.label)}</strong><span>${_text(f.desc)}</span></div>
                </div>` : `
                <div class="fig-card" style="opacity:0.5">
                  <div style="padding:20px;text-align:center;color:var(--text3);font-size:11px">No image</div>
                  <div class="fig-label"><strong>${_text(f.label)}</strong><span>${_text(f.desc)}</span></div>
                </div>`
              ).join("")}
            </div>
          </div>
          <div class="section">
            <div class="section-label">My Notes</div>
            <textarea class="notes-area" placeholder="Type your notes here... (auto-saves)"
              oninput="updateNotes('${_js(p.id)}', this.value)">${_text(p._note || "")}</textarea>
            <div style="display:flex;align-items:center;gap:8px">
              <div class="notes-saved" id="notes-saved-${p.id}">✓ Saved</div>
              ${p._lastEdited ? `<span id="note-ts-${p.id}" style="font-size:10px;color:var(--text3)">Last edited: ${SCQ.formatRelativeTime(p._lastEdited)}</span>` : ''}
            </div>
          </div>
          <div class="section">
            ${renderHighlights(p.id, hlData)}
          </div>
          ${(() => {
            const rel = getRelatedPapers(p);
            if (rel.length === 0) return '';
            return `<div class="section">
              <div class="section-label">Related Papers</div>
              <div class="related-papers">
                ${rel.map(r => `<span class="related-chip ${r.reasons[0] === 'linked manually' ? 'manual-link' : ''}" onclick="event.stopPropagation(); expandedId='${_js(r.paper.id)}'; render();" title="${_attr(r.reasons.join(', '))}">
                  ${_text(r.paper.shortAuthors)} (${_text(r.paper.year)}) <span class="related-reason">${_text(r.reasons[0])}</span>
                </span>`).join("")}
              </div>
            </div>`;
          })()}
          <div style="margin-bottom:12px">
            ${p.tags.map(t => `<span class="tag-pill">${t}</span>`).join("")}
          </div>
          <div class="action-btns">
            <button class="action-btn" id="copy-bib-${p.id}" onclick="event.stopPropagation(); copyText(PAPERS.find(x=>x.id==='${p.id}').citeBib, 'copy-bib-${p.id}')">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              Copy .bib
            </button>
            <button class="action-btn" id="copy-txt-${p.id}" onclick="event.stopPropagation(); copyText(PAPERS.find(x=>x.id==='${p.id}').citeTxt, 'copy-txt-${p.id}')">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              Copy plain text
            </button>
            <div class="collection-assign">
              <button class="action-btn" id="coll-btn-${p.id}" onclick="event.stopPropagation(); toggleCollectionDropdown('${p.id}', event)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                Collections
              </button>
              ${openDropdownId === p.id ? renderCollectionDropdown(p.id) : ''}
            </div>
            ${p.url && (p.entryType === 'website' || p.entryType === 'release') ? `<a class="action-btn" href="${p.url}" target="_blank" style="text-decoration:none">Open link ↗</a>` : `<a class="action-btn" href="https://arxiv.org/abs/${p.id}" target="_blank" style="text-decoration:none">arXiv ↗</a>`}
            <button class="pdf-link-btn" onclick="event.stopPropagation(); openPdfViewer('${p.id}')" title="Open PDF in side panel (${getPdfPath(p.id)})">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
              PDF
            </button>
            <button class="link-papers-btn" onclick="event.stopPropagation(); showLinkPaperModal('${p.id}')" title="Manually link related papers">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
              Link
            </button>
          </div>
        </div>`;
      }
      html += `</div>`;
      return html;
    }).join("");

  } else if (currentView === "table") {
    const sorted = sortPapers(filtered);
    content.innerHTML = `<div class="table-wrap"><table>
      <thead><tr>
        <th style="width:44px" class="${sortedClass('_read')}" onclick="toggleSort('_read')">Read${sortArrow('_read')}</th>
        <th style="width:70px" class="${sortedClass('_priority')}" onclick="toggleSort('_priority')">&#9733;${sortArrow('_priority')}</th>
        <th class="${sortedClass('shortAuthors')}" onclick="toggleSort('shortAuthors')">Author${sortArrow('shortAuthors')}</th>
        <th class="${sortedClass('title')}" onclick="toggleSort('title')">Title${sortArrow('title')}</th>
        <th class="${sortedClass('group')}" onclick="toggleSort('group')">Group${sortArrow('group')}</th>
        <th class="${sortedClass('entryType')}" onclick="toggleSort('entryType')">Type${sortArrow('entryType')}</th>
        <th class="${sortedClass('year')}" onclick="toggleSort('year')">Year${sortArrow('year')}</th>
        <th class="${sortedClass('dateAdded')}" onclick="toggleSort('dateAdded')">Added${sortArrow('dateAdded')}</th>
        <th style="width:60px">Cite</th>
        <th style="width:40px">PDF</th>
      </tr></thead>
      <tbody>${sorted.map(p => `<tr>
        <td style="text-align:center"><label class="read-toggle" style="justify-content:center;padding:0"><input type="checkbox" ${p._read ? 'checked' : ''} onchange="toggleReadStatus('${p.id}')"></label></td>
        <td><div class="star-rating" style="justify-content:center">${renderStars(p.id)}</div></td>
        <td style="color:var(--text2);white-space:nowrap">${p.shortAuthors}</td>
        <td style="font-weight:500;max-width:280px">${p.title}</td>
        <td style="color:var(--text2);white-space:nowrap">${p.group}</td>
        <td>${(() => { const _tc = (_etCfg[p.entryType] || {}); return `<span class="badge badge-type" style="background:${_tc.color || '#58a6ff'}22;color:${_tc.color || '#58a6ff'}">${_tc.label || p.entryType || 'Preprint'}</span>`; })()}</td>
        <td style="color:var(--text2)">${p.year}</td>
        <td style="color:var(--text3);font-size:11px;white-space:nowrap">${p.dateAdded || ''}</td>
        <td><button class="word-cite-btn" id="wcite-${p.id}" onclick="copyForWord('${p.id}', 'wcite-${p.id}')" title="Copy citation for Word">&#128203; Word</button></td>
        <td style="text-align:center"><a href="${getPdfPath(p.id)}" target="_blank" style="text-decoration:none;font-size:14px" title="Open PDF">&#128196;</a></td>
      </tr>`).join("")}</tbody>
    </table></div>`;

  } else if (currentView === "cite") {
    content.innerHTML = `
      <div class="cite-export-btns">
        <button class="export-btn primary" id="export-all-bib" onclick="copyText(PAPERS.map(p=>p.citeBib).join('\\n\\n'), 'export-all-bib')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Copy all .bib
        </button>
        <button class="export-btn secondary" id="export-all-txt" onclick="copyText(PAPERS.map((p,i)=>'['+(i+1)+'] '+p.citeTxt).join('\\n\\n'), 'export-all-txt')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Copy all plain text
        </button>
        <button class="export-btn secondary" id="export-word" onclick="copyAllForWord('export-word')" title="Copies numbered plain-text citations — paste directly into Word">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="4" y="2" width="16" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/><line x1="8" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="13" y2="14"/></svg>
          Copy all for Word
        </button>
      </div>
      ${filtered.map((p,i) => `<div class="cite-card">
        <div class="cite-title">[${i+1}] ${p.title}</div>
        <div class="cite-text">[${i+1}] ${p.citeTxt}</div>
        <div class="action-btns" style="margin-top:6px">
          <button class="action-btn" id="cite-bib-${p.id}" onclick="copyText(PAPERS.find(x=>x.id==='${p.id}').citeBib, 'cite-bib-${p.id}')">.bib</button>
          <button class="action-btn" id="cite-txt-${p.id}" onclick="copyText('['+(${i}+1)+'] '+PAPERS.find(x=>x.id==='${p.id}').citeTxt, 'cite-txt-${p.id}')">text</button>
        </div>
      </div>`).join("")}`;
  }
}
