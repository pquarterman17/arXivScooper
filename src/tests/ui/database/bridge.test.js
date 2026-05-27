/**
 * Architect finding #1: lock the page bridge.
 *
 * `BRIDGE` in src/ui/database/main.js is the contract between the
 * still-inline boot block in paper_database.html (which calls bare
 * globals) and the module-extracted implementations. Without a frozen
 * list, adding a module function and forgetting to bridge it produces
 * a confusing runtime `undefined is not a function` somewhere deep in
 * a render loop. Renaming a function and missing the boot-block call
 * site produces the same failure mode.
 *
 * This spec parses src/ui/database/main.js and asserts:
 *   1. The BRIDGE keys match a checked-in expected set.
 *   2. Adding/removing a key requires updating this list (forced choice).
 *   3. The asymmetric `_syncToSharedFolder` alias is still in place
 *      (legacy markup uses the underscored name).
 *
 * If you intentionally add or remove a bridge entry, update
 * EXPECTED_BRIDGE_KEYS below in the same commit.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const mainPath = resolve(here, '../../../ui/database/main.js');
const mainSrc = readFileSync(mainPath, 'utf-8');

const EXPECTED_BRIDGE_KEYS = [
  // Sync indicator + paths
  'updateSyncIndicator', 'getPdfPath', 'closeMoreMenu', 'saveToDisk',
  // Sort + Cite-tab clipboard helpers
  'toggleSort', 'sortPapers', 'sortArrow', 'sortedClass',
  'copyForWord', 'copyAllForWord',
  // PDF viewer
  'openPdfViewer', 'closePdfViewer', 'openPdfExternal',
  // Annotations
  'addHighlight', 'removeHighlightById', 'renderHighlights',
  // Analytics overlay
  'showAnalytics', 'closeAnalytics',
  // Export / import / merge
  'exportJSON', 'importFile', 'mergeFile',
  'exportCollectionAsDB', 'exportCollectionBib', 'exportCollectionPackage',
  // Add-website modal
  'showAddWebsiteModal', 'fetchWebsiteMeta', 'submitAddWebsite',
  'findArxivId', 'findDOI',
  // Related-paper finder
  'getRelatedPapers',
  // Collections
  'getCollectionNames', 'isPaperInCollection', 'togglePaperCollection',
  'setActiveCollection', 'showNewCollectionModal', 'createCollection',
  'deleteCollectionUI', 'closeModal', 'toggleCollectionDropdown',
  'renderCollectionDropdown',
  // Manual paper-paper linking
  'showLinkPaperModal', 'toggleManualLink',
  // Read-status / priority
  'toggleReadStatus', 'setStarRating', 'renderStars',
  'setReadFilter', 'setPriorityFilter', 'setTypeFilter',
  // Library-table helpers
  'getAllTags', 'getFiltered', 'togglePdfSearch', 'copyText',
  'openLightbox', 'closeLightbox',
  // Tag manager
  'showTagManagerModal', 'promptRenameTag', 'promptMergeTag', 'doDeleteTag',
  // Suggestions banner
  'loadSuggestions', 'renderSuggestions', 'autoFetchOnLoad',
  'toggleSuggestions', 'sugAdd', 'sugIgnore', 'dismissAllSuggestions',
  // Library load + per-row events
  'loadPapersFromDB', 'togglePaper', 'toggleTag', 'clearTags', 'updateNotes',
  // Library-view rendering
  'render', 'renderSidebar',
  // Patents view (switchMainTab is wrapped inside patents-view.js itself)
  'renderPatentsView', 'togglePatentDetail', 'refreshPatentsView',
  // Collaboration helpers (legacy underscored alias for syncToSharedFolder)
  '_syncToSharedFolder', 'mergeSharedFile',
];

/** Extract identifiers from the BRIDGE = {...} block by simple regex. */
function extractBridgeKeys(src) {
  // Strip block comments before walking braces — a `/* } */` inside the
  // BRIDGE body would throw off the depth counter.
  const cleaned = src.replace(/\/\*[\s\S]*?\*\//g, '');
  const start = cleaned.indexOf('const BRIDGE = {');
  if (start < 0) throw new Error('BRIDGE declaration not found');
  // Walk to the matching close brace by counting depth.
  let depth = 0;
  let end = -1;
  for (let i = start; i < cleaned.length; i++) {
    if (cleaned[i] === '{') depth++;
    else if (cleaned[i] === '}') {
      depth--;
      if (depth === 0) { end = i; break; }
    }
  }
  if (end < 0) throw new Error('BRIDGE close brace not found');
  const body = cleaned.slice(start, end);
  const keys = [];
  // Strip comments line-by-line, then split the joined body on commas
  // so multi-shorthand-per-line entries (e.g. `a, b, c,`) all count.
  // Each comma-separated piece is either `name` (shorthand) or
  // `name: <expr>` (with renamed value); take the part before any colon.
  const stripped = body
    .split('\n')
    .map((raw) => raw.replace(/\/\/.*$/, ''))
    .join(' ');
  // Drop the opening `{` (the `const BRIDGE = {` marker).
  const inner = stripped.replace(/^[^{]*\{/, '');
  for (const piece of inner.split(',')) {
    const before = piece.split(':')[0].trim();
    const m = before.match(/^([A-Za-z_$][\w$]*)$/);
    if (m) keys.push(m[1]);
  }
  return keys;
}

describe('database page BRIDGE — frozen list', () => {
  const bridgeKeys = extractBridgeKeys(mainSrc);

  it('exposes exactly the expected keys (no drift)', () => {
    const extra = bridgeKeys.filter((k) => !EXPECTED_BRIDGE_KEYS.includes(k));
    const missing = EXPECTED_BRIDGE_KEYS.filter((k) => !bridgeKeys.includes(k));
    if (extra.length || missing.length) {
      const lines = [];
      if (extra.length)   lines.push(`  Added without updating list: ${extra.join(', ')}`);
      if (missing.length) lines.push(`  Removed but still in list:   ${missing.join(', ')}`);
      lines.push('');
      lines.push('Either way, update EXPECTED_BRIDGE_KEYS in this file in the same commit.');
      throw new Error('BRIDGE drift:\n' + lines.join('\n'));
    }
    expect(bridgeKeys.sort()).toEqual([...EXPECTED_BRIDGE_KEYS].sort());
  });

  it('preserves the asymmetric _syncToSharedFolder alias', () => {
    // Legacy markup calls `_syncToSharedFolder(...)` with a leading
    // underscore, but the module exports `syncToSharedFolder` without
    // one. The BRIDGE has to translate.
    expect(mainSrc).toMatch(/_syncToSharedFolder\s*:\s*syncToSharedFolder/);
  });

  it('publishes via Object.assign + a debug handle', () => {
    expect(mainSrc).toMatch(/Object\.assign\s*\(\s*window\s*,\s*BRIDGE\s*\)/);
    expect(mainSrc).toMatch(/__SCQ_DATABASE_BRIDGE__/);
  });

  it('extractBridgeKeys survives block comments containing braces', () => {
    const synthetic = [
      'const BRIDGE = {',
      '  /* toggle } visibility */',
      '  alpha,',
      '  beta: someFn,',
      '};',
    ].join('\n');
    const keys = extractBridgeKeys(synthetic);
    expect(keys).toEqual(['alpha', 'beta']);
  });
});
