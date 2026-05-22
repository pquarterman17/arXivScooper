/**
 * "Save to disk" — UI wiring for the new /api/save-db endpoint.
 *
 * Two affordances:
 *   1. The sync indicator becomes clickable when dirty (orange). Click
 *      it and the in-memory DB gets POSTed to the server. On success the
 *      indicator flips to green via the legacy `_scqOnDirty` callback.
 *   2. A "Save to disk" button in the More menu does the same thing,
 *      so users who don't notice the clickable indicator have a path.
 *
 * The legacy "Save database" button (which downloads the .db file)
 * stays — useful as a manual backup. This module adds, doesn't replace.
 *
 * The legacy `SCQ` IIFE owns the in-memory DB and the dirty flag. We
 * call `SCQ.getDB().export()` to get bytes, post via the service, then
 * fire SCQ's existing dirty-clearing path (`_cacheToLocalStorage` already
 * runs, but the dirty flag is set inside the IIFE — we cheat by calling
 * `SCQ.saveToFile` semantics manually: set `lastSavedAt` and fire
 * `_scqOnDirty(false)`).
 *
 * Once core/db replaces SCQ entirely (later in #8), this module will
 * call core/db.exportBytes() + core/db.save() instead.
 */

import { saveDbToServer } from '../../services/database-sync.js';

const INDICATOR_ID = 'sync-indicator';

let _busy = false;

/**
 * Pull bytes out of the legacy SCQ IIFE, POST them, mark clean.
 * Returns the server's response payload on success.
 */
export async function saveToDisk() {
  if (_busy) return null;
  if (!globalThis.SCQ || typeof globalThis.SCQ.getDB !== 'function') {
    // Most likely cause: db_utils.js didn't expose SCQ on window. Modules
    // can't see top-level `const` bindings from regular <script> tags —
    // db_utils.js has an explicit `window.SCQ = SCQ` at the bottom for
    // exactly this reason. If you're seeing this error, check that
    // db_utils.js loaded (DevTools Network tab) and that no error
    // happened before the `window.SCQ = SCQ` line at the end of it.
    throw new Error('[save-to-disk] window.SCQ not found — did db_utils.js load?');
  }
  _busy = true;
  _markIndicatorBusy();
  try {
    const db = globalThis.SCQ.getDB();
    if (!db) throw new Error('[save-to-disk] no in-memory DB to save');
    const bytes = db.export();
    const result = await saveDbToServer(bytes);
    _markCleanInLegacy();
    return result;
  } catch (e) {
    _markIndicatorError(e.message);
    throw e;
  } finally {
    _busy = false;
  }
}

/**
 * Make the sync indicator clickable. On click, save. Idempotent — calling
 * twice doesn't double-bind.
 */
let _clickInstalled = false;
export function installClickToSave() {
  if (_clickInstalled) return;
  _clickInstalled = true;
  // Defer until DOM is parsed (this module loads via type=module which is
  // deferred, so the element should exist). If not yet, retry on the next
  // microtask via DOMContentLoaded as a safety belt.
  const wire = () => {
    const el = document.getElementById(INDICATOR_ID);
    if (!el) return false;
    el.style.cursor = 'pointer';
    el.title = (el.title || '') + '\n(click to save to data/arxiv_scooper.db)';
    el.addEventListener('click', async (ev) => {
      ev.preventDefault();
      try {
        await saveToDisk();
      } catch (e) {
        console.error('[save-to-disk] failed:', e);
      }
    });
    return true;
  };
  if (!wire()) {
    document.addEventListener('DOMContentLoaded', wire, { once: true });
  }
}

// ─── internals ───

function _markIndicatorBusy() {
  const el = document.getElementById(INDICATOR_ID);
  if (!el) return;
  el.textContent = 'saving…';
  el.style.color = 'var(--text2)';
}

function _markIndicatorError(msg) {
  const el = document.getElementById(INDICATOR_ID);
  if (!el) return;
  el.textContent = 'save failed';
  el.style.color = 'var(--red)';
  el.title = `Save to disk failed: ${msg}\nClick to retry.`;
}

function _markCleanInLegacy() {
  // Legacy SCQ.saveToFile flips dirty=false and fires _scqOnDirty(false).
  // We mimic the same so the indicator goes green via the existing pathway.
  // SCQ doesn't expose a public "mark clean" — we go through window._scqOnDirty.
  if (typeof globalThis._scqOnDirty === 'function') {
    globalThis._scqOnDirty(false);
  } else {
    // Fallback: directly update the indicator if the legacy callback
    // somehow isn't wired (shouldn't happen post-init).
    if (typeof globalThis.updateSyncIndicator === 'function') {
      globalThis.updateSyncIndicator(true);
    }
  }
}
