// @vitest-environment jsdom

/**
 * UI tests for the database-page Patents view. Builds the minimal DOM,
 * stubs globalThis.fetch (the service reads it), and — importantly — sets
 * globalThis.switchMainTab BEFORE import so the module captures it as the
 * "original" to delegate non-patents tabs to.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

let origSwitch;

beforeEach(() => {
  document.body.innerHTML = '';
  for (const k of ['renderPatentsView', 'togglePatentDetail', 'refreshPatentsView', 'switchMainTab', 'fetch']) {
    delete globalThis[k];
  }
  origSwitch = vi.fn();
  globalThis.switchMainTab = origSwitch; // captured by the module at import
});

async function load() {
  return import('../../../ui/database/patents-view.js?v=' + Math.random());
}

function buildPanel() {
  document.body.innerHTML = `
    <button class="main-tab active" id="mtab-library"></button>
    <button class="main-tab" id="mtab-patents"></button>
    <div id="tab-library"></div>
    <div id="tab-patents" style="display:none"></div>
    <span id="patents-count"></span>
    <input id="patents-filter">
    <div id="patents-list"></div>`;
}

function jsonRes(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body };
}

describe('patents view: switchMainTab wrap', () => {
  it('shows the patents panel and renders on entry', async () => {
    buildPanel();
    const mod = await load();
    globalThis.fetch = vi.fn(async () => jsonRes({ ok: true, patents: [] }));
    await mod.switchMainTabWithPatents('patents');
    expect(document.getElementById('tab-patents').style.display).toBe('');
    expect(document.getElementById('mtab-patents').classList.contains('active')).toBe(true);
  });

  it('delegates non-patents tabs to the original switchMainTab', async () => {
    buildPanel();
    const mod = await load();
    mod.switchMainTabWithPatents('library');
    expect(origSwitch).toHaveBeenCalledWith('library');
    expect(document.getElementById('tab-patents').style.display).toBe('none');
  });
});

describe('patents view: renderPatentsView', () => {
  it('renders cards and a count', async () => {
    buildPanel();
    const mod = await load();
    globalThis.fetch = vi.fn(async () =>
      jsonRes({ ok: true, patents: [{ number: 'US10374134B2', title: 'Qubit', assignee: 'IBM', has_summary: true, cpc_codes: ['H10N60/12'] }] }));
    await mod.renderPatentsView();
    const html = document.getElementById('patents-list').innerHTML;
    expect(html).toContain('Qubit');
    expect(html).toContain('summarized');
    expect(html).toContain('H10N60/12');
    expect(document.getElementById('patents-count').textContent).toBe('1');
  });

  it('shows an empty state when nothing is stored', async () => {
    buildPanel();
    const mod = await load();
    globalThis.fetch = vi.fn(async () => jsonRes({ ok: true, patents: [] }));
    await mod.renderPatentsView();
    expect(document.getElementById('patents-list').innerHTML).toContain('No patents');
  });
});

describe('patents view: togglePatentDetail', () => {
  it('lazy-loads the full record and shows summary + claims', async () => {
    buildPanel();
    document.getElementById('patents-list').innerHTML =
      '<div id="pdetail-US10374134B2" style="display:none"></div>';
    const mod = await load();
    globalThis.fetch = vi.fn(async (url) => {
      expect(url).toContain('/api/patents/get?number=US10374134B2');
      return jsonRes({ ok: true, patent: {
        number: 'US10374134B2', has_summary: true,
        plain_summary: 'A tantalum transmon.',
        protected_scope: 'Covers tantalum capacitor pads.',
        prior_art_note: 'Builds on niobium qubits.',
        independent_claims: ['A superconducting qubit comprising a tantalum pad.'],
      } });
    });
    await mod.togglePatentDetail('US10374134B2');
    const detail = document.getElementById('pdetail-US10374134B2');
    expect(detail.style.display).toBe('block');
    expect(detail.innerHTML).toContain('A tantalum transmon.');
    expect(detail.innerHTML).toContain('Covers tantalum capacitor pads.');
    expect(detail.innerHTML).toContain('Independent claims (1)');
  });

  it('collapses on a second toggle', async () => {
    buildPanel();
    document.getElementById('patents-list').innerHTML =
      '<div id="pdetail-US1" style="display:block">x</div>';
    const mod = await load();
    globalThis.fetch = vi.fn();
    await mod.togglePatentDetail('US1');
    expect(document.getElementById('pdetail-US1').style.display).toBe('none');
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
