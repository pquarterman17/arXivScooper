// Side-effect imports: each module shims its public API onto globalThis
// so legacy boot-block call sites + the ACTIONS registry below resolve.
// Keep this list alphabetised; it doubles as a manifest of what's been
// extracted from the boot block under plan #9 Phase B.
import './state.js';              // cross-module state manifest
import './cors-fetch.js';
import './connection-test.js';
import './inbox-persistence.js';
import './tabs.js';
import './search-tab.js';
import './doi-lookup.js';
import './inbox-render.js';
import './quick-search.js';
import './saved-queries.js';
import './patents-tab.js';
import { bootstrapSearchConfig } from '../../core/search-config-bridge.js';

/**
 * Entry point for the modular paper-scraper UI (plan #9 — companion to #8).
 *
 * **Strangler-fig migration in progress.** The legacy `paper_scraper.html`
 * is ~1600 lines of inline JS + 18 inline `onclick=` attributes (down from
 * 39) after the static-handler sweep. As features peel out of the inline
 * `<script>` block into modules under `src/ui/scraper/`, this file imports
 * them, re-exposes their public API on `window` so the still-inline boot
 * block keeps working, and dispatches `data-action` / `data-change` events
 * on the static markup through delegated listeners.
 *
 * **Loading order:** this script tag is `type="module"`, deferred until
 * after the document is parsed *and* after legacy `<script>` tags run.
 *   1. CDN scripts (sql.js) — synchronous
 *   2. `scraper_config.js`, `db_utils.js` — synchronous, sets globals
 *   3. The big inline `<script>` block — synchronous, defines all the
 *      legacy functions on the global scope
 *   4. THIS module — installs the delegated listeners + ACTION registry
 *   5. `init()` from the inline block fires last (it lives at the bottom)
 *
 * Convention: each ACTIONS entry is a thin trampoline to the legacy global
 * function (`window.foo?.()`). Optional chaining means if a function isn't
 * yet defined when the user clicks, no crash. As modules migrate, replace
 * the trampoline with a direct import.
 */

// Each handler receives `(el, event)` where `el` is the closest element
// carrying the data-action attr (NOT necessarily event.target, which can be
// a child).
const ACTIONS = {
  // ─ Tabs (legacy `switchTab` is generic; data-tab carries the target id)
  switchScraperTab: (el) => window.switchTab?.(el.dataset.tab),

  // ─ Search tab
  doSearch: () => window.doSearch?.(),
  saveCurrentSearch: () => window.saveCurrentSearch?.(),
  clearDateFilter: () => window.clearDateFilter?.(),
  stageSelected: () => window.stageSelected?.(),
  clearSelection: () => window.clearSelection?.(),

  // ─ Quick Search tab
  quickDoSearch: () => window.quickDoSearch?.(),
  quickSelectAll: () => window.quickSelectAll?.(),
  quickSelectNone: () => window.quickSelectNone?.(),
  quickExportSelected: () => window.quickExportSelected?.(),

  // ─ DOI Lookup tab
  doDoiLookup: () => window.doDoiLookup?.(),

  // ─ Patents tab
  addPatent: () => window.addPatent?.(),
  addPatentNumber: (el) => window.addPatentNumber?.(el.dataset.number),
  searchPatents: () => window.searchPatents?.(),
  refreshPatents: () => window.refreshPatents?.(),

  // ─ Inbox tab
  approveAll: () => window.approveAll?.(),
  clearInbox: () => window.clearInbox?.(),

  // ─ Saved queries panel
  openAddQueryModal: () => window.openAddQueryModal?.(),
  runAllSavedQueries: () => window.runAllSavedQueries?.(),
  closeScraperModal: () => window.closeModal?.(),
  confirmSaveQuery: () => window.confirmSaveQuery?.(),

  // ─ Connection test (in the header status badge)
  runConnectionTest: (_el, e) => {
    e.preventDefault();
    window.runConnectionTest?.();
  },

  // ─ Keyboard: Enter inside the DOI input box triggers lookup
  // (registered via the keydown delegate below, not the click delegate)
  doDoiLookupOnEnter: undefined,

  // ─── Dynamic-template handlers ───
  // Each render function in the boot block now emits `data-action` /
  // `data-idx` attributes instead of inline `onclick="foo(${i})"`. The
  // boot-block functions still live in the page; we trampoline through
  // `window.<fn>?.()` until each tab gets its own module.
  stopPropagation: (_el, e) => e.stopPropagation(),

  // Search tab — empty-state preset, results card, etc.
  usePreset: (el) => {
    const input = document.getElementById('search-input');
    if (input) input.value = el.dataset.query || '';
    window.doSearch?.();
  },
  toggleSelect: (el) => window.toggleSelect?.(Number(el.dataset.idx)),
  toggleSearchAbstract: (el, e) => {
    e.stopPropagation();
    document.getElementById(`abs-${el.dataset.idx}`)?.classList.toggle('collapsed');
  },
  stageOneStop: (el, e) => {
    e.stopPropagation();
    window.stageOne?.(Number(el.dataset.idx));
  },

  // DOI lookup tab
  stageDoiPaper: () => window.stageDoiPaper?.(),

  // Inbox tab
  toggleInboxAbstract: (el) => {
    document.getElementById(`inbox-abs-${el.dataset.idx}`)?.classList.toggle('collapsed');
  },
  approveOne: (el) => window.approveOne?.(Number(el.dataset.idx)),
  dismissOne: (el) => window.dismissOne?.(Number(el.dataset.idx)),

  // Quick Search tab
  quickToggleSelect: (el) => window.quickToggleSelect?.(Number(el.dataset.idx)),
  quickToggleSelectStop: (el, e) => {
    e.stopPropagation();
    window.quickToggleSelect?.(Number(el.dataset.idx));
  },
  quickToggleAbstractStop: (el, e) => {
    e.stopPropagation();
    window.quickToggleAbstract?.(Number(el.dataset.idx));
  },
  copyQuickExportJson: (el) => {
    const json = document.getElementById('quick-export-json')?.textContent ?? '';
    navigator.clipboard.writeText(json);
    const original = el.textContent;
    el.textContent = 'Copied!';
    setTimeout(() => { el.textContent = original; }, 1500);
  },
  quickDownloadExport: () => window.quickDownloadExport?.(),

  // Saved queries panel
  runSavedQuery: (el) => window.runSavedQuery?.(Number(el.dataset.idx)),
  removeSavedQueryStop: (el, e) => {
    e.stopPropagation();
    window.removeSavedQuery?.(Number(el.dataset.idx));
  },

  // Status error: collapsible details
  toggleErrorDetails: (el, e) => {
    e.preventDefault();
    document.getElementById(el.dataset.target)?.classList.toggle('open');
  },
};

const CHANGES = {};

// `data-input` delegate — input-event variant of CHANGES (input fires per
// keystroke; change fires on blur). Only used by the inbox-note textarea
// for now: `data-input="updateInboxNote" data-idx="${i}"` calls
// window.updateInboxNote(i, el.value) on every keystroke.
const INPUTS = {
  updateInboxNote: (el) => window.updateInboxNote?.(Number(el.dataset.idx), el.value),
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

document.addEventListener('input', (e) => {
  const el = e.target.closest('[data-input]');
  if (!el) return;
  const fn = INPUTS[el.dataset.input];
  if (fn) fn(el, e);
});

// Keydown delegate. Currently only the DOI input cares about Enter, but the
// pattern scales: action name `<thing>OnEnter` → call window.<thing>().
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  const el = e.target.closest('[data-action$="OnEnter"]');
  if (!el) return;
  const action = el.dataset.action;
  // Strip the "OnEnter" suffix to get the real action name
  const fnName = action.slice(0, -'OnEnter'.length);
  const fn = window[fnName];
  if (typeof fn === 'function') fn();
});

// Plan #9 last bullet + architect-finding-#2: bridge user_config search
// overrides onto SCRAPER_CONFIG, then re-fire every UI surface that
// captured config at boot.
//
// The contract (per docs/architecture.md "Config-subscribe rule"):
// any UI surface that reads merged config either re-renders on
// `config:<domain>:changed` (or via a bridge onReady), or is documented
// as a boot-time snapshot that requires reload.
//
// These four onReady callbacks satisfy the rule for the scraper page:
//   rebuildActiveSources  — derived state from CFG.sources
//   injectSourceStyles    — CSS injected for per-source badge colors
//   initSourceToggles     — DOM toggle buttons for each source
//   initPresetsSearch     — DOM preset buttons (uses CFG.presets)
// All four are idempotent — clear-and-rebuild — so re-running on bridge
// resolution is safe.
bootstrapSearchConfig([
  () => globalThis.rebuildActiveSources?.(),
  () => globalThis.injectSourceStyles?.(),
  () => globalThis.initSourceToggles?.(),
  () => globalThis.initPresetsSearch?.(),
]);
