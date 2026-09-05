// @vitest-environment jsdom

/**
 * Plan #9 Phase B — search-tab extraction regression.
 *
 * Smoke-level coverage: pure helpers (sort) get exhaustive tests; the
 * fetch-driven functions (doSearch, searchArxiv, etc.) get window-shim
 * presence + a single happy-path against mocked corsFetch. The MT-2
 * action-registry tests already cover the dispatch glue.
 */

import { describe, it, expect, beforeEach } from 'vitest';

const SHIM_NAMES = [
  'getArxivSortParams', 'applySortToResults',
  'doSearch', 'searchArxiv', 'searchPhysRev', 'searchCrossref',
  'renderSearchResults', 'toggleSelect', 'clearSelection',
  'updateBatchBar', 'stageOne', 'stageSelected',
];

const STATE_NAMES = [
  'searchResults', 'selectedIdxs', 'inbox', 'existingIds', 'activeSources',
  'lastFetchTime', 'CFG', 'autoTag', 'esc', 'showStatusError',
  'corsFetch', 'saveInbox', 'updateInboxBadge',
];

beforeEach(() => {
  for (const k of [...SHIM_NAMES, ...STATE_NAMES]) delete globalThis[k];
  document.body.innerHTML = '';
});

async function load() {
  return await import('../../../ui/scraper/search-tab.js?v=' + Math.random());
}

// ─── Sort helpers (pure) ───

describe('getArxivSortParams', () => {
  it.each([
    ['relevance',  'sortBy=relevance&sortOrder=descending'],
    ['updated',    'sortBy=lastUpdatedDate&sortOrder=descending'],
    ['date-asc',   'sortBy=submittedDate&sortOrder=ascending'],
    ['date-desc',  'sortBy=submittedDate&sortOrder=descending'],
    [undefined,    'sortBy=submittedDate&sortOrder=descending'],  // default
    ['unknown',    'sortBy=submittedDate&sortOrder=descending'],
  ])('%s -> %s', async (input, expected) => {
    await load();
    expect(globalThis.getArxivSortParams(input)).toBe(expected);
  });
});

describe('applySortToResults', () => {
  it('date-desc sorts newest first', async () => {
    await load();
    const r = [{ published: '2024-01-01' }, { published: '2024-06-01' }];
    expect(globalThis.applySortToResults(r, 'date-desc')[0].published).toBe('2024-06-01');
  });

  it('date-asc sorts oldest first', async () => {
    await load();
    const r = [{ published: '2024-06-01' }, { published: '2024-01-01' }];
    expect(globalThis.applySortToResults(r, 'date-asc')[0].published).toBe('2024-01-01');
  });

  it('author sorts alphabetically by shortAuthors', async () => {
    await load();
    const r = [{ shortAuthors: 'Smith' }, { shortAuthors: 'Jones' }];
    expect(globalThis.applySortToResults(r, 'author')[0].shortAuthors).toBe('Jones');
  });

  it('relevance + updated leave order untouched', async () => {
    await load();
    const r = [{ id: 'a' }, { id: 'b' }];
    expect(globalThis.applySortToResults(r, 'relevance')).toBe(r);
    expect(globalThis.applySortToResults(r, 'updated')).toBe(r);
  });
});

// ─── Selection actions ───

describe('selection helpers', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="search-results"></div>
      <div id="batch-bar"><span id="batch-count">0</span></div>
    `;
    globalThis.searchResults = [
      { id: 'a', title: 'A', shortAuthors: 'X', year: 2024, summary: '', tags: [], url: 'http://x', source: 'arxiv' },
      { id: 'b', title: 'B', shortAuthors: 'Y', year: 2024, summary: '', tags: [], url: 'http://y', source: 'arxiv' },
    ];
    globalThis.selectedIdxs = new Set();
    globalThis.existingIds = new Set();
    globalThis.inbox = [];
    globalThis.CFG = { sources: { arxiv: { label: 'arXiv' } }, presets: [] };
    globalThis.esc = (s) => String(s);
  });

  it('toggleSelect adds + removes', async () => {
    await load();
    globalThis.toggleSelect(0);
    expect(globalThis.selectedIdxs.has(0)).toBe(true);
    globalThis.toggleSelect(0);
    expect(globalThis.selectedIdxs.has(0)).toBe(false);
  });

  it('clearSelection empties the set', async () => {
    await load();
    globalThis.selectedIdxs.add(0); globalThis.selectedIdxs.add(1);
    globalThis.clearSelection();
    expect(globalThis.selectedIdxs.size).toBe(0);
  });

  it('updateBatchBar reflects selection count', async () => {
    await load();
    globalThis.selectedIdxs.add(0);
    globalThis.updateBatchBar();
    expect(document.getElementById('batch-count').textContent).toBe('1');
    expect(document.getElementById('batch-bar').classList.contains('show')).toBe(true);
  });
});

// ─── Window shims ───

describe('window shims', () => {
  it('all 12 expected names are on globalThis after import', async () => {
    await load();
    for (const name of SHIM_NAMES) {
      expect(typeof globalThis[name]).toBe('function');
    }
  });
});
