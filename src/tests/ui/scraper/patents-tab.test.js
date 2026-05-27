// @vitest-environment jsdom

/**
 * UI tests for the scraper Patents tab. Builds the minimal DOM matching
 * the production markup and stubs globalThis.fetch (the service reads it),
 * then exercises the window-shimmed handlers.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

beforeEach(() => {
  document.body.innerHTML = '';
  for (const k of ['addPatent', 'addPatentNumber', 'renderPatentsList', 'searchPatents', 'refreshPatents', 'fetch']) {
    delete globalThis[k];
  }
});

async function load() {
  return import('../../../ui/scraper/patents-tab.js?v=' + Math.random());
}

function buildPanel() {
  document.body.innerHTML = `
    <input id="patent-input">
    <select id="patent-source"><option value="google">g</option><option value="patentsview">p</option></select>
    <button id="patent-add-btn"></button>
    <div id="patent-status"></div>
    <input id="patent-search-input">
    <button id="patent-search-btn"></button>
    <div id="patent-search-status"></div>
    <div id="patent-search-results"></div>
    <input id="patent-filter">
    <div id="patent-list"></div>`;
}

function jsonRes(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body };
}

describe('patents tab: addPatent', () => {
  it('fetches via the service, shows success, refreshes the list', async () => {
    buildPanel();
    const mod = await load();
    document.getElementById('patent-input').value = 'US10374134B2';

    globalThis.fetch = vi.fn(async (url) => {
      if (url === '/api/patents/add') {
        return jsonRes({ ok: true, patent: { number: 'US10374134B2', title: 'Qubit', assignee: 'IBM' } });
      }
      // the follow-up list refresh
      return jsonRes({ ok: true, patents: [{ number: 'US10374134B2', title: 'Qubit', assignee: 'IBM', has_summary: false, cpc_codes: [] }] });
    });

    await mod.addPatent();
    expect(document.getElementById('patent-status').className).toContain('success');
    expect(document.getElementById('patent-status').textContent).toContain('US10374134B2');
    expect(document.getElementById('patent-input').value).toBe(''); // cleared
    expect(document.getElementById('patent-list').innerHTML).toContain('Qubit');
    expect(document.getElementById('patent-list').innerHTML).toContain('needs summary');
  });

  it('shows an error status when the add fails', async () => {
    buildPanel();
    const mod = await load();
    document.getElementById('patent-input').value = 'US0';
    globalThis.fetch = vi.fn(async () => jsonRes({ ok: false, error: 'no such patent' }, { ok: false, status: 404 }));
    await mod.addPatent();
    expect(document.getElementById('patent-status').className).toContain('error');
    expect(document.getElementById('patent-status').textContent).toContain('no such patent');
  });

  it('refuses an empty input without fetching', async () => {
    buildPanel();
    const mod = await load();
    globalThis.fetch = vi.fn();
    await mod.addPatent();
    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(document.getElementById('patent-status').textContent).toContain('Enter a patent number');
  });
});

describe('patents tab: searchPatents (dormant path)', () => {
  it('surfaces the set-your-key message on 503', async () => {
    buildPanel();
    const mod = await load();
    document.getElementById('patent-search-input').value = 'transmon';
    globalThis.fetch = vi.fn(async () => jsonRes({ error: 'PatentsView API key not set. Run: scq config set-secret patentsview_api_key' }, { ok: false, status: 503 }));
    await mod.searchPatents();
    const status = document.getElementById('patent-search-status');
    expect(status.className).toContain('error');
    expect(status.textContent).toContain('API key not set');
  });

  it('renders result cards with an Add button when the key works', async () => {
    buildPanel();
    const mod = await load();
    document.getElementById('patent-search-input').value = 'qubit';
    globalThis.fetch = vi.fn(async () => jsonRes({ patents: [{ patent_id: '10374134', patent_title: 'Qubit', patent_date: '2019-08-06', assignees: [{ assignee_organization: 'IBM' }] }] }));
    await mod.searchPatents();
    const results = document.getElementById('patent-search-results').innerHTML;
    expect(results).toContain('Qubit');
    expect(results).toContain('data-action="addPatentNumber"');
    expect(results).toContain('data-number="10374134"');
  });
});

describe('patents tab: renderPatentsList', () => {
  it('shows an empty state when nothing is stored', async () => {
    buildPanel();
    const mod = await load();
    globalThis.fetch = vi.fn(async () => jsonRes({ ok: true, patents: [] }));
    await mod.renderPatentsList();
    expect(document.getElementById('patent-list').innerHTML).toContain('No patents stored');
  });
});
