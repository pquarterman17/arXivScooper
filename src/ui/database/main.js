/**
 * Entry point for the new modular paper-database UI (plan item #8).
 *
 * **Strangler-fig migration in progress.** The legacy `paper_database.html`
 * is ~4700 lines of inline JS + CSS. Rather than a big-bang rewrite, we
 * progressively peel features into modules under `src/ui/database/`. This
 * file is the bridge: it imports each migrated feature and re-exposes its
 * public API on `window` so existing inline callers (and `onclick=`
 * attributes in the HTML) keep working.
 *
 * As more features migrate, the legacy boot block in paper_database.html
 * shrinks. Eventually that block is replaced entirely with a `boot()`
 * function exported from this file, and the `window.*` shims are dropped.
 *
 * **Loading order matters:** this script tag is `type="module"`, which
 * defers execution until after the document is parsed *and* after legacy
 * `<script>` tags run. So:
 *   1. CDN scripts (sql.js, d3) — synchronous during parse
 *   2. Legacy `db_utils.js`, `scraper_config.js` — synchronous
 *   3. The big inline `<script>` block — synchronous, sets up SCQ.init()
 *   4. THIS module — runs after parse; window shims installed
 *   5. SCQ.init().then() callback fires async after sql.js + DB load
 * The SCQ.init().then() callback is where legacy code calls our shimmed
 * functions. By the time it runs, our shims are in place.
 *
 * No new boot logic here yet — the legacy block still owns initialization.
 */

import './escape-html.js';  // side-effect: shims globalThis.escapeHtml
import './local-proxy.js';   // side-effect: shims globalThis.arxivFetch
import { updateSyncIndicator } from './sync-indicator.js';
import { getPdfPath } from './pdf-path.js';
import { closeMoreMenu, installMoreMenuOutsideClick } from './more-menu.js';
import { saveToDisk, installClickToSave } from './save-to-disk.js';
import { toggleSort, sortPapers, sortArrow, sortedClass } from './sort.js';
import { copyForWord, copyAllForWord } from './citation-copy.js';
import { openPdfViewer, closePdfViewer, openPdfExternal } from './pdf-viewer.js';
import { addHighlight, removeHighlightById, renderHighlights } from './highlights.js';
import { showAnalytics, closeAnalytics } from './analytics.js';
import {
  exportJSON,
  importFile,
  mergeFile,
  exportCollectionAsDB,
  exportCollectionBib,
  exportCollectionPackage,
} from './export-import.js';
import { showAddWebsiteModal, fetchWebsiteMeta, submitAddWebsite } from './add-website-modal.js';
import { installDragDropImport, findArxivId, findDOI } from './drag-drop-import.js';
import { getRelatedPapers } from './related-papers.js';
import {
  getCollectionNames,
  isPaperInCollection,
  togglePaperCollection,
  setActiveCollection,
  showNewCollectionModal,
  createCollection,
  deleteCollectionUI,
  closeModal,
  toggleCollectionDropdown,
  renderCollectionDropdown,
} from './collections-ui.js';
import { showLinkPaperModal, toggleManualLink } from './manual-link.js';
import {
  toggleReadStatus,
  setStarRating,
  renderStars,
  setReadFilter,
  setPriorityFilter,
  setTypeFilter,
} from './read-priority.js';
import {
  getAllTags,
  getFiltered,
  togglePdfSearch,
  copyText,
  openLightbox,
  closeLightbox,
} from './helpers.js';
import { showTagManagerModal, promptRenameTag, promptMergeTag, doDeleteTag } from './tag-manager.js';
import {
  installSourceStyles,
  loadSuggestions,
  renderSuggestions,
  toggleSuggestions,
  sugAdd,
  sugIgnore,
  dismissAllSuggestions,
  autoFetchOnLoad,
} from './suggestions-banner.js';
import { loadPapersFromDB } from './init.js';
import { togglePaper, toggleTag, clearTags, updateNotes } from './events.js';
import { syncToSharedFolder, mergeSharedFile } from './collaboration.js';
import { render, renderSidebar } from './library-table.js';
import { renderPatentsView, togglePatentDetail, refreshPatentsView } from './patents-view.js';
import { showSettings as showSettingsV2, closeSettings as closeSettingsV2 } from '../settings/main.js';
import { bootstrapSearchConfig } from '../../core/search-config-bridge.js';

// Plan #9 last bullet: apply user_config/search-sources.json overrides to
// the legacy SCRAPER_CONFIG global so the database page's suggestions
// banner, library-table source badges, and add-website-modal entry-type
// reads pick up user customisations the same way the scraper page does.
bootstrapSearchConfig();

// ─── Page bridge ───
//
// `BRIDGE` is the public surface this page exposes via `window.<name>` —
// it's the contract between the still-inline boot block in
// paper_database.html (which calls these as bare globals) and the
// module-extracted implementations.
//
// Adding a new module function that the boot block needs to reach? Add
// the entry here. Removing one? Same. The frozen-list test at
// `src/tests/ui/database/bridge.test.js` will fail if you skip a step.
//
// Asymmetric name note: legacy markup calls `_syncToSharedFolder` with a
// leading underscore, but the module exports `syncToSharedFolder`
// without one. The bridge translates.
const BRIDGE = {
  // Sync indicator + paths
  updateSyncIndicator, getPdfPath, closeMoreMenu, saveToDisk,
  // Sort + Cite-tab clipboard helpers
  toggleSort, sortPapers, sortArrow, sortedClass, copyForWord, copyAllForWord,
  // PDF viewer
  openPdfViewer, closePdfViewer, openPdfExternal,
  // Annotations
  addHighlight, removeHighlightById, renderHighlights,
  // Analytics overlay
  showAnalytics, closeAnalytics,
  // Export / import / merge
  exportJSON, importFile, mergeFile,
  exportCollectionAsDB, exportCollectionBib, exportCollectionPackage,
  // Add-website modal
  showAddWebsiteModal, fetchWebsiteMeta, submitAddWebsite, findArxivId, findDOI,
  // Related-paper finder
  getRelatedPapers,
  // Collections
  getCollectionNames, isPaperInCollection, togglePaperCollection,
  setActiveCollection, showNewCollectionModal, createCollection,
  deleteCollectionUI, closeModal, toggleCollectionDropdown, renderCollectionDropdown,
  // Manual paper-paper linking
  showLinkPaperModal, toggleManualLink,
  // Read-status / priority
  toggleReadStatus, setStarRating, renderStars,
  setReadFilter, setPriorityFilter, setTypeFilter,
  // Library-table helpers
  getAllTags, getFiltered, togglePdfSearch, copyText,
  openLightbox, closeLightbox,
  // Tag manager
  showTagManagerModal, promptRenameTag, promptMergeTag, doDeleteTag,
  // Suggestions banner — used by loadPapersFromDB + inline onclicks
  loadSuggestions, renderSuggestions, autoFetchOnLoad,
  toggleSuggestions, sugAdd, sugIgnore, dismissAllSuggestions,
  // Library load + per-row events
  loadPapersFromDB, togglePaper, toggleTag, clearTags, updateNotes,
  // Library-view rendering — boot block calls render() / renderSidebar()
  render, renderSidebar,
  // Patents view (database-page browse). switchMainTab is wrapped inside
  // patents-view.js itself (it must capture the boot-block original).
  renderPatentsView, togglePatentDetail, refreshPatentsView,
  // Collaboration helpers — note the legacy `_syncToSharedFolder` alias
  _syncToSharedFolder: syncToSharedFolder,
  mergeSharedFile,
};
Object.assign(window, BRIDGE);
// Exposed for the bridge-test spec to introspect at runtime.
window.__SCQ_DATABASE_BRIDGE__ = BRIDGE;

// ─── Event delegation registries ───
// Static markup in `paper_database.html` uses `data-action="..."` on
// elements that need a click handler, and `data-change="..."` for change
// events. The two delegated listeners installed below dispatch into the
// registries, which in turn call the imported module function or — for
// handlers still living in the legacy boot block — `window.<name>(...)`.
//
// Why this exists: until #8 fully closes (boot block + dynamic template
// strings migrated), inline `onclick="foo()"` attributes in the static
// HTML were the *only* reason some functions had to be on `window`.
// Replacing them with data-action makes the static markup
// framework-agnostic and clarifies which window shims remain because of
// legacy *dynamic* HTML (rendered by template strings) versus static.
//
// Each handler receives `(el, event)` where `el` is the closest element
// carrying the `data-action` / `data-change` attribute (NOT necessarily
// `event.target`, which can be a child).
//
// Convention for arguments:
//   - Single-value actions read from a typed data-attribute named after
//     the parameter (e.g. `data-tab="library"` for switchMainTab).
//   - Some attributes (data-readfilter, data-pf) are reused because
//     CSS / JS already query them for styling.

const ACTIONS = {
  // ─ Top toolbar
  showAddWebsiteModal: () => showAddWebsiteModal(),
  showSettingsV2: () => showSettingsV2(),
  closeSettingsV2: () => closeSettingsV2(),
  closeSettingsV2IfBackdrop: (el, e) => {
    if (e.target === el) closeSettingsV2();
  },
  toggleMoreMenu: (el) => el.nextElementSibling.classList.toggle('open'),
  // ─ "More" menu items (each closes the menu after running)
  menuSaveToDisk: () => {
    saveToDisk().catch((e) => alert('Save failed: ' + e.message));
    closeMoreMenu();
  },
  menuDownloadDb: () => { window.SCQ.saveToFile(); closeMoreMenu(); },
  menuExportJson: () => { exportJSON(); closeMoreMenu(); },
  menuImportDb: () => {
    document.getElementById('import-db-file').click();
    closeMoreMenu();
  },
  menuMergeDb: () => {
    document.getElementById('merge-db-file').click();
    closeMoreMenu();
  },
  menuShowAnalytics: () => { showAnalytics(); closeMoreMenu(); },
  menuExportCollectionPackage: () => {
    exportCollectionPackage(window.activeCollection || 'all');
    closeMoreMenu();
  },
  menuImportPackage: () => {
    document.getElementById('import-package-file').click();
    closeMoreMenu();
  },
  menuOpenBatchImport: () => { window.openBatchImport?.(); closeMoreMenu(); },
  menuShowSettingsV2: () => { showSettingsV2(); closeMoreMenu(); },
  closeMoreMenu: () => closeMoreMenu(),
  // ─ Suggestions banner
  toggleSuggestions: () => toggleSuggestions(),
  dismissAllSuggestions: (_el, e) => {
    e.stopPropagation();
    dismissAllSuggestions();
  },
  // ─ Tabs
  switchMainTab: (el) => window.switchMainTab?.(el.dataset.tab),
  // ─ Filters (active-state styling reads existing data-readfilter / data-pf)
  setReadFilter: (el) => setReadFilter(el.dataset.readfilter),
  setPriorityFilter: (el) => setPriorityFilter(el.dataset.pf),
  // ─ Cite tab
  citeSetFormat: (el) => window.citeSetFormat?.(el.dataset.fmt),
  citeClearSelection: () => window.citeClearSelection?.(),
  citeCopySelected: () => window.citeCopySelected?.(),
  // ─ Graph tab
  renderGraph: () => window.renderGraph?.(),
  // ─ Patents tab
  togglePatentDetail: (el) => window.togglePatentDetail?.(el.dataset.number),
  refreshPatentsView: () => window.refreshPatentsView?.(),
  // ─ Inbox tab
  inboxImportFile: () => window.inboxImportFile?.(),
  inboxImportAll: () => window.inboxImportAll?.(),
  inboxImportStarred: () => window.inboxImportStarred?.(),
  inboxClear: () => window.inboxClear?.(),
  // ─ Overlays — close only when the backdrop itself is clicked
  closeAnalyticsIfBackdrop: (el, e) => {
    if (e.target === el) closeAnalytics();
  },
  closeAnalytics: () => closeAnalytics(),
  closeLightbox: () => closeLightbox(),
  closePdfViewer: () => closePdfViewer(),
  // ─ Batch import modal
  closeBatchImport: () => window.closeBatchImport?.(),
  pickBatchFiles: () => window.pickBatchFiles?.(),
  pickBatchFolder: () => window.pickBatchFolder?.(),

  // ─ Dynamic-template handlers (rendered from boot-block tabs).
  // These replace the inline `onclick="foo(${idx})"` patterns in render
  // functions; the boot-block functions stay in place, called via window.
  // Dataset numbers come back as strings; parse where the underlying
  // function expects an integer.
  stopPropagation: (_el, e) => e.stopPropagation(),
  readingMarkRead: (el) => window.readingMarkRead?.(el.dataset.id),
  readingViewFullEntry: (el) => {
    const id = el.dataset.id;
    window.switchMainTab?.('library');
    window.expandedId = id;
    window.render?.();
  },
  citeToggleSelect: (el) => window.citeToggleSelect?.(el.dataset.id),
  citeToggleSelectStop: (el, e) => {
    e.stopPropagation();
    window.citeToggleSelect?.(el.dataset.id);
  },
  citeQuickCopy: (el) => window.citeQuickCopy?.(el.dataset.id, el.dataset.fmt, el),
  toggleAbstract: (el) => window.toggleAbstract?.(Number(el.dataset.idx)),
  inboxRemoveTag: (el) => window.inboxRemoveTag?.(
    Number(el.dataset.idx), Number(el.dataset.tidx),
  ),
  inboxSetPriority: (el) => window.inboxSetPriority?.(
    Number(el.dataset.idx), Number(el.dataset.rating),
  ),
  inboxImportOne: (el) => window.inboxImportOne?.(Number(el.dataset.idx)),
  inboxSkipOne: (el) => window.inboxSkipOne?.(Number(el.dataset.idx)),
};

const CHANGES = {
  importFile: (_el, e) => importFile(e),
  mergeFile: (_el, e) => mergeFile(e),
  togglePdfSearch: (el) => togglePdfSearch(el.checked),
  inboxFileSelected: (_el, e) => window.inboxFileSelected?.(e),
  handleBatchFiles: (el) => window.handleBatchFiles?.(el.files),
};

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const fn = ACTIONS[el.dataset.action];
  if (fn) fn(el, e);
});

document.addEventListener('change', (e) => {
  const el = e.target.closest('[data-change]');
  if (!el) return;
  const fn = CHANGES[el.dataset.change];
  if (fn) fn(el, e);
});

// Keydown delegate for ``data-action="<thing>OnEnter"`` — strips the suffix
// and calls window.<thing>(event, ...dataset-args). Currently only the inbox
// tag input uses this for Enter-to-add.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  const el = e.target.closest('[data-action$="OnEnter"]');
  if (!el) return;
  const fnName = el.dataset.action.slice(0, -'OnEnter'.length);
  const fn = window[fnName];
  if (typeof fn !== 'function') return;
  // The boot-block inboxTagKeypress reads (event, idx); pass both.
  fn(e, Number(el.dataset.idx));
});

// ─── One-time DOM wiring ───
// Features that need a global listener at boot install it here, idempotently.
installMoreMenuOutsideClick();
installClickToSave();
installDragDropImport();
installSourceStyles();
