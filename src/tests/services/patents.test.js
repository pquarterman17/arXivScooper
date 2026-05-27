import { describe, it, expect, vi } from 'vitest';
import {
  normalizePatentNumber,
  addPatent,
  listPatents,
  getPatent,
  searchPatentsView,
  parsePatentsViewSearch,
} from '../../services/patents.js';

// A minimal Response-like stub.
function res(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body };
}

describe('normalizePatentNumber', () => {
  it.each([
    ['US10,374,134 B2', 'US10374134B2'],
    ['US 10374134B2', 'US10374134B2'],
    ['10374134', 'US10374134'],
    ['EP1234567A1', 'EP1234567A1'],
    ['https://patents.google.com/patent/US10374134B2/en', 'US10374134B2'],
  ])('normalizes %s → %s', (input, expected) => {
    expect(normalizePatentNumber(input)).toBe(expected);
  });

  it.each([[''], [null], [undefined], [42], ['no digits here']])(
    'returns null for invalid input: %s',
    (input) => {
      expect(normalizePatentNumber(input)).toBeNull();
    },
  );
});

describe('addPatent', () => {
  it('POSTs number+source and returns the patent record', async () => {
    const fetch = vi.fn(async (url, init) => {
      expect(url).toBe('/api/patents/add');
      expect(JSON.parse(init.body)).toEqual({ number: 'US10374134B2', source: 'google' });
      return res({ ok: true, patent: { number: 'US10374134B2', assignee: 'IBM' } });
    });
    const rec = await addPatent('US10374134B2', 'google', { fetch });
    expect(rec.assignee).toBe('IBM');
  });

  it('defaults source to google', async () => {
    const fetch = vi.fn(async (_url, init) => {
      expect(JSON.parse(init.body).source).toBe('google');
      return res({ ok: true, patent: {} });
    });
    await addPatent('US1', undefined, { fetch });
    expect(fetch).toHaveBeenCalled();
  });

  it('throws with the server error message on failure', async () => {
    const fetch = vi.fn(async () => res({ ok: false, error: 'no such patent' }, { ok: false, status: 404 }));
    await expect(addPatent('US0', 'google', { fetch })).rejects.toThrow('no such patent');
  });
});

describe('listPatents', () => {
  it('builds the query string and returns rows', async () => {
    const fetch = vi.fn(async (url) => {
      expect(url).toBe('/api/patents/list?q=tantalum&limit=10');
      return res({ ok: true, patents: [{ number: 'US10374134B2' }] });
    });
    const rows = await listPatents({ query: 'tantalum', limit: 10, fetch });
    expect(rows).toHaveLength(1);
  });

  it('omits the query string when no filter is given', async () => {
    const fetch = vi.fn(async (url) => {
      expect(url).toBe('/api/patents/list');
      return res({ ok: true, patents: [] });
    });
    await listPatents({ fetch });
  });
});

describe('getPatent', () => {
  it('fetches the full record by number', async () => {
    const fetch = vi.fn(async (url) => {
      expect(url).toBe('/api/patents/get?number=US10374134B2');
      return res({ ok: true, patent: { number: 'US10374134B2', claims: [], plain_summary: '' } });
    });
    const rec = await getPatent('US10374134B2', { fetch });
    expect(rec.number).toBe('US10374134B2');
  });

  it('throws on a missing patent', async () => {
    const fetch = vi.fn(async () => res({ ok: false, error: 'No stored patent US999' }, { ok: false, status: 404 }));
    await expect(getPatent('US999', { fetch })).rejects.toThrow('No stored patent');
  });
});

describe('searchPatentsView (dormant path)', () => {
  it('throws a set-your-key message on HTTP 503', async () => {
    const fetch = vi.fn(async () =>
      res({ error: 'PatentsView API key not set. Run: scq config set-secret patentsview_api_key' }, { ok: false, status: 503 }),
    );
    await expect(searchPatentsView('transmon', { fetch })).rejects.toThrow(/API key not set/);
  });

  it('parses results when the key is present', async () => {
    const fetch = vi.fn(async () =>
      res({ patents: [{ patent_id: '10374134', patent_title: 'Qubit', patent_date: '2019-08-06', assignees: [{ assignee_organization: 'IBM' }] }] }),
    );
    const rows = await searchPatentsView('qubit', { fetch });
    expect(rows[0]).toEqual({ number: '10374134', title: 'Qubit', assignee: 'IBM', grant_date: '2019-08-06' });
  });

  it('rejects an empty query before fetching', async () => {
    const fetch = vi.fn();
    await expect(searchPatentsView('  ', { fetch })).rejects.toThrow('Enter a search query');
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('parsePatentsViewSearch', () => {
  it('handles an empty/missing payload', () => {
    expect(parsePatentsViewSearch({})).toEqual([]);
    expect(parsePatentsViewSearch(null)).toEqual([]);
  });
});
