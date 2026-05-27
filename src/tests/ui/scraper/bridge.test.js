/**
 * Architect finding #1 (scraper variant): lock the page bridge.
 *
 * Unlike the database side, the scraper's bridge is per-module — each
 * scraper module appends `globalThis.<name> = <name>` at the bottom
 * to expose its public API to the still-inline boot block. The union
 * of those exposures is the page's bridge.
 *
 * This spec walks src/ui/scraper/*.js, collects every `globalThis.X`
 * write, and asserts the set matches EXPECTED_SCRAPER_BRIDGE below.
 * Adding/removing a globalThis assignment without updating this list
 * fails the spec — same forced-choice contract as the database test.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const scraperDir = resolve(here, '../../../ui/scraper');

const EXPECTED_SCRAPER_BRIDGE = [
  // cors-fetch
  'CORS_PROXIES', 'corsFetch', 'isLocalhost', 'toLocalProxy',
  // connection-test
  'runConnectionTest',
  // doi-lookup
  'doDoiLookup', 'renderDoiResult', 'stageDoiPaper',
  // inbox-persistence
  'loadInbox', 'saveInbox',
  // inbox-render
  'renderInbox', 'approveOne', 'approveAll', 'dismissOne',
  'clearInbox', 'updateInboxNote',
  // quick-search
  'quickDoSearch', 'quickRenderResults', 'quickToggleSelect',
  'quickSelectAll', 'quickSelectNone', 'quickUpdateSelectionCount',
  'quickToggleAbstract', 'quickExportSelected', 'quickDownloadExport',
  'quickSetQuery',
  // saved-queries
  'loadSavedQueries', 'saveSavedQueries', 'renderSavedQueries',
  'openAddQueryModal', 'closeModal', 'confirmSaveQuery',
  'removeSavedQuery', 'runSavedQuery', 'runAllSavedQueries',
  'saveCurrentSearch',
  // search-tab
  'getArxivSortParams', 'applySortToResults', 'doSearch',
  'searchArxiv', 'searchPhysRev', 'searchCrossref',
  'renderSearchResults', 'toggleSelect', 'clearSelection',
  'updateBatchBar', 'stageOne', 'stageSelected',
  // state
  'initState',
  // tabs
  'switchTab', 'updateInboxBadge', 'updateStats',
  // patents-tab
  'addPatent', 'addPatentNumber', 'renderPatentsList', 'searchPatents',
  'refreshPatents',
];

function listScraperFiles() {
  return readdirSync(scraperDir)
    .filter((f) => f.endsWith('.js') && f !== 'main.js')
    .map((f) => join(scraperDir, f));
}

function extractGlobalThisNames(src) {
  const names = new Set();
  const re = /^globalThis\.([A-Za-z_$][\w$]*)\s*=/gm;
  let m;
  while ((m = re.exec(src)) !== null) names.add(m[1]);
  return [...names];
}

function collectAllNames() {
  const all = new Set();
  for (const path of listScraperFiles()) {
    const src = readFileSync(path, 'utf-8');
    for (const name of extractGlobalThisNames(src)) all.add(name);
  }
  return [...all];
}

describe('scraper page BRIDGE — frozen list', () => {
  const observed = collectAllNames();

  it('exposes exactly the expected globalThis.<name> set', () => {
    const extra = observed.filter((k) => !EXPECTED_SCRAPER_BRIDGE.includes(k));
    const missing = EXPECTED_SCRAPER_BRIDGE.filter((k) => !observed.includes(k));
    if (extra.length || missing.length) {
      const lines = [];
      if (extra.length)   lines.push(`  Added without updating list: ${extra.join(', ')}`);
      if (missing.length) lines.push(`  Removed but still in list:   ${missing.join(', ')}`);
      lines.push('');
      lines.push('Update EXPECTED_SCRAPER_BRIDGE in this file in the same commit.');
      throw new Error('Scraper bridge drift:\n' + lines.join('\n'));
    }
    expect(observed.sort()).toEqual([...EXPECTED_SCRAPER_BRIDGE].sort());
  });

  it('every module-extracted scraper file at least imports/uses globalThis', () => {
    // Sanity: confirms the per-module pattern is consistent. If a new
    // module ships with no globalThis assignments, that's almost
    // certainly a bug (the boot block won't be able to call into it).
    // Excludes `index.js` if anyone ever adds one.
    const noBridgeNeeded = new Set(); // empty for now — add module names if a module is intentionally pure
    for (const path of listScraperFiles()) {
      const file = path.split(/[\\/]/).pop();
      if (noBridgeNeeded.has(file)) continue;
      const src = readFileSync(path, 'utf-8');
      const names = extractGlobalThisNames(src);
      // state.js intentionally only writes through globalThis (no functions exposed yet).
      // Treat any non-empty count as evidence of the pattern.
      if (names.length === 0 && !/globalThis\./m.test(src)) {
        throw new Error(`${file} has no globalThis usage — likely missing the bridge pattern`);
      }
    }
  });
});
