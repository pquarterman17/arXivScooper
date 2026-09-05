// @vitest-environment jsdom

/**
 * Regression tests for the data-action / data-input dispatch in
 * src/ui/database/main.js.
 *
 * Plan #8 swept all 16 dynamic onclicks in paper_database.html's boot-block
 * template strings into `data-action="..."` attributes. The dispatch logic
 * lives in main.js's ACTIONS registry. These tests verify each new action
 * routes to the right `window.<fn>?.()` shim with the right parsed args.
 *
 * Imports main.js once (which installs document-level listeners) and then
 * dispatches synthetic clicks against jsdom-built elements.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

beforeEach(async () => {
  document.body.innerHTML = '';
  // Reset window globals used by the trampolines
  for (const key of [
    'readingMarkRead', 'switchMainTab', 'render', 'expandedId',
    'citeToggleSelect', 'citeQuickCopy',
    'toggleAbstract', 'inboxRemoveTag', 'inboxSetPriority',
    'inboxImportOne', 'inboxSkipOne', 'inboxTagKeypress',
  ]) {
    delete window[key];
  }
});

let mainImported = false;
async function loadMain() {
  if (mainImported) return;
  // Stub the legacy globals that main.js's bootstrap install* helpers read
  // at import time (suggestions-banner reads SCRAPER_CONFIG.sources, etc.).
  // The action-dispatch logic itself doesn't depend on these — but the
  // top-level install calls do. Stub minimally to satisfy them.
  globalThis.SCRAPER_CONFIG = globalThis.SCRAPER_CONFIG ?? { sources: {} };
  globalThis.SCQ = globalThis.SCQ ?? {
    init: () => Promise.resolve(),
    getSetting: () => null,
    setSetting: () => {},
  };
  // The module installs document-level listeners on import; the listeners
  // close over its ACTIONS registry. Importing once per suite is enough.
  await import('../../../ui/database/main.js');
  mainImported = true;
}

function clickWith(attrs, parent) {
  const el = document.createElement('button');
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  (parent ?? document.body).appendChild(el);
  el.click();
  return el;
}

describe('database main.js: new dynamic-template actions', () => {
  it('readingMarkRead reads data-id and forwards to window.readingMarkRead', async () => {
    await loadMain();
    const fn = vi.fn();
    window.readingMarkRead = fn;
    clickWith({ 'data-action': 'readingMarkRead', 'data-id': '2401.12345' });
    expect(fn).toHaveBeenCalledWith('2401.12345');
  });

  it('readingViewFullEntry switches tab + sets expandedId + calls render', async () => {
    await loadMain();
    const switchTab = vi.fn();
    const render = vi.fn();
    window.switchMainTab = switchTab;
    window.render = render;
    window.expandedId = null;
    clickWith({ 'data-action': 'readingViewFullEntry', 'data-id': '2603.99001' });
    expect(switchTab).toHaveBeenCalledWith('library');
    expect(window.expandedId).toBe('2603.99001');
    expect(render).toHaveBeenCalled();
  });

  it('citeToggleSelect forwards the data-id', async () => {
    await loadMain();
    const fn = vi.fn();
    window.citeToggleSelect = fn;
    clickWith({ 'data-action': 'citeToggleSelect', 'data-id': '2401.99999' });
    expect(fn).toHaveBeenCalledWith('2401.99999');
  });

  it('citeQuickCopy forwards id + fmt + element', async () => {
    await loadMain();
    const fn = vi.fn();
    window.citeQuickCopy = fn;
    const el = clickWith({
      'data-action': 'citeQuickCopy',
      'data-id': '2401.12345',
      'data-fmt': 'bib',
    });
    expect(fn).toHaveBeenCalledWith('2401.12345', 'bib', el);
  });

  it('inbox actions parse data-idx as a Number', async () => {
    await loadMain();
    const importer = vi.fn();
    const skipper = vi.fn();
    window.inboxImportOne = importer;
    window.inboxSkipOne = skipper;
    clickWith({ 'data-action': 'inboxImportOne', 'data-idx': '5' });
    clickWith({ 'data-action': 'inboxSkipOne', 'data-idx': '7' });
    expect(importer).toHaveBeenCalledWith(5);
    expect(skipper).toHaveBeenCalledWith(7);
  });

  it('inboxSetPriority parses idx + rating', async () => {
    await loadMain();
    const fn = vi.fn();
    window.inboxSetPriority = fn;
    clickWith({
      'data-action': 'inboxSetPriority',
      'data-idx': '3',
      'data-rating': '2',
    });
    expect(fn).toHaveBeenCalledWith(3, 2);
  });

  it('toggleAbstract parses idx as Number', async () => {
    await loadMain();
    const fn = vi.fn();
    window.toggleAbstract = fn;
    clickWith({ 'data-action': 'toggleAbstract', 'data-idx': '12' });
    expect(fn).toHaveBeenCalledWith(12);
  });

  it('inboxRemoveTag parses both idx + tidx', async () => {
    await loadMain();
    const fn = vi.fn();
    window.inboxRemoveTag = fn;
    clickWith({ 'data-action': 'inboxRemoveTag', 'data-idx': '4', 'data-tidx': '1' });
    expect(fn).toHaveBeenCalledWith(4, 1);
  });

  it('stopPropagation action exists and dispatches without errors', async () => {
    await loadMain();
    // `closest('[data-action]')` already resolves to the nearest ancestor,
    // so child data-action handlers naturally win over their parents'
    // data-action handlers — that's the actual "stop propagation" effect
    // in this dispatch model. The stopPropagation action itself is a
    // no-op marker that documents intent for elements that may end up
    // inside a clickable parent later.
    expect(() => clickWith({ 'data-action': 'stopPropagation' })).not.toThrow();
  });

  it('citeToggleSelectStop wins over a parent data-action (closest semantics)', async () => {
    await loadMain();
    const parentFn = vi.fn();
    const childFn = vi.fn();
    window.citeToggleSelect = childFn;
    // Simulate the cite tab DOM: parent row has citeToggleSelect; nested
    // checkbox has citeToggleSelectStop. Clicking the checkbox should fire
    // ONLY the checkbox's action (because closest() finds it first).
    const parent = document.createElement('div');
    parent.setAttribute('data-action', 'citeToggleSelect');
    parent.setAttribute('data-id', 'parent-id');
    document.body.appendChild(parent);
    // Override the trampoline so we can tell which target was clicked
    window.citeToggleSelect = (id) => (id === 'parent-id' ? parentFn(id) : childFn(id));
    clickWith(
      { 'data-action': 'citeToggleSelectStop', 'data-id': 'child-id' },
      parent,
    );
    expect(childFn).toHaveBeenCalledWith('child-id');
    expect(parentFn).not.toHaveBeenCalled();
  });

  it('inboxTagKeypressOnEnter dispatches on Enter key only', async () => {
    await loadMain();
    const fn = vi.fn();
    window.inboxTagKeypress = fn;
    const el = document.createElement('input');
    el.setAttribute('data-action', 'inboxTagKeypressOnEnter');
    el.setAttribute('data-idx', '2');
    document.body.appendChild(el);

    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
    expect(fn).not.toHaveBeenCalled();

    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(fn).toHaveBeenCalledTimes(1);
    // First arg is the event object, second is the parsed idx
    expect(fn.mock.calls[0][1]).toBe(2);
  });
});
