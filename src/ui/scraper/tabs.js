/**
 * Tab switching + status badges for the scraper UI.
 *
 * Extracted from paper_scraper.html boot block (lines 1520–1562 pre-refactor)
 * as part of plan #9 Phase B. Three small DOM-mutation functions:
 *
 *   - switchTab(tab)        — show/hide the four panels, mark the active
 *                             tab, focus the right input on Quick / DOI,
 *                             trigger renderInbox() when entering Inbox.
 *   - updateInboxBadge()    — show/hide the orange dot on the Inbox tab,
 *                             update the inbox-count stat.
 *   - updateStats()         — refresh the DB-paper-count + last-fetch-time
 *                             stat lines.
 *
 * **State coupling (cross-module reads):**
 *   - globalThis.inbox          — extracted to inbox-persistence.js (Phase B 1/4)
 *   - globalThis.dbReady        — boot block declares as `var`
 *   - globalThis.lastFetchTime  — boot block declares as `var`
 *   - globalThis.SCQ            — set by db_utils.js (legacy IIFE)
 *   - globalThis.renderInbox    — still inline in the boot block
 *
 * No state writes — this module is a pure DOM mutator.
 */

export function switchTab(tab) {
  const panels = ['search', 'inbox', 'quick', 'doi', 'patents'];
  for (const t of panels) {
    const panel = document.getElementById(`panel-${t}`);
    const btn = document.getElementById(`tab-${t}`);
    if (panel) panel.style.display = (t === tab) ? 'block' : 'none';
    if (btn) btn.classList.toggle('active', t === tab);
  }

  if (tab === 'inbox') globalThis.renderInbox?.();
  if (tab === 'patents') globalThis.renderPatentsList?.();
  if (tab === 'quick') {
    setTimeout(() => document.getElementById('quick-search-input')?.focus(), 100);
  }
  if (tab === 'doi') {
    setTimeout(() => document.getElementById('doi-input')?.focus(), 100);
  }
}

export function updateInboxBadge() {
  const badge = document.getElementById('inbox-badge');
  const inbox = globalThis.inbox ?? [];
  if (badge) {
    if (inbox.length > 0) {
      badge.style.display = 'inline-block';
      badge.textContent = String(inbox.length);
    } else {
      badge.style.display = 'none';
    }
  }
  const stat = document.getElementById('stat-inbox');
  if (stat) stat.textContent = String(inbox.length);
}

export function updateStats() {
  if (globalThis.dbReady && globalThis.SCQ) {
    const stats = globalThis.SCQ.getStats();
    const el = document.getElementById('stat-db');
    if (el) el.textContent = String(stats.papers);
  }
  const lastEl = document.getElementById('stat-last');
  if (lastEl) {
    lastEl.textContent = globalThis.lastFetchTime
      ? globalThis.lastFetchTime.toLocaleTimeString()
      : 'never';
  }
}

// Window shims for the boot block + ACTIONS dispatch.
globalThis.switchTab = switchTab;
globalThis.updateInboxBadge = updateInboxBadge;
globalThis.updateStats = updateStats;
