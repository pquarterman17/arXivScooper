// @vitest-environment jsdom

/**
 * Plan #8 — local-proxy extraction regression.
 *
 * Tests cover:
 *   - URL translation rules (arxiv.org variants -> /api/arxiv?...)
 *   - localhost detection
 *   - arxivFetch behaviour: prefers proxy on localhost, direct elsewhere,
 *     falls back to direct on proxy network error or non-OK response
 *   - Window shims, including the two-underscore aliases from the
 *     pre-extraction boot block
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

let _origFetch;
beforeEach(() => {
  for (const k of ['arxivFetch', '_isLocalhost', '_toLocalProxy']) {
    delete globalThis[k];
  }
  _origFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = _origFetch;
});

async function load() {
  return await import('../../../ui/database/local-proxy.js?v=' + Math.random());
}

describe('toLocalProxy', () => {
  it('rewrites arxiv.org/api/query to /api/arxiv with the same query string', async () => {
    const { toLocalProxy } = await load();
    expect(toLocalProxy('http://arxiv.org/api/query?id_list=2401.00001&max_results=1'))
      .toBe('/api/arxiv?id_list=2401.00001&max_results=1');
  });

  it('rewrites the export.arxiv.org subdomain too', async () => {
    const { toLocalProxy } = await load();
    expect(toLocalProxy('https://export.arxiv.org/api/query?search_query=cat:quant-ph'))
      .toBe('/api/arxiv?search_query=cat:quant-ph');
  });

  it('returns null for non-arxiv URLs', async () => {
    const { toLocalProxy } = await load();
    expect(toLocalProxy('https://api.crossref.org/works/10.1/x')).toBe(null);
    expect(toLocalProxy('https://example.com/foo')).toBe(null);
  });

  it('returns null for an empty / non-string input', async () => {
    const { toLocalProxy } = await load();
    expect(toLocalProxy('')).toBe(null);
    expect(toLocalProxy(null)).toBe(null);
    expect(toLocalProxy(undefined)).toBe(null);
  });
});

describe('isLocalhost', () => {
  it('returns true for localhost', async () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('http://localhost:8080/'),
    });
    const { isLocalhost } = await load();
    expect(isLocalhost()).toBe(true);
  });

  it('returns true for 127.0.0.1', async () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('http://127.0.0.1:8080/'),
    });
    const { isLocalhost } = await load();
    expect(isLocalhost()).toBe(true);
  });

  it('returns false for other hostnames', async () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('https://pquarterman17.github.io/'),
    });
    const { isLocalhost } = await load();
    expect(isLocalhost()).toBe(false);
  });
});

describe('arxivFetch', () => {
  it('uses local proxy on localhost when the proxy returns OK', async () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('http://localhost:8080/'),
    });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, _from: 'proxy' });
    globalThis.fetch = fetchMock;

    const { arxivFetch } = await load();
    const resp = await arxivFetch('http://arxiv.org/api/query?id_list=2401.00001');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/arxiv?id_list=2401.00001');
    expect(resp._from).toBe('proxy');
  });

  it('falls back to direct fetch when the proxy returns non-OK', async () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('http://localhost:8080/'),
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, _from: 'proxy' })
      .mockResolvedValueOnce({ ok: true, _from: 'direct' });
    globalThis.fetch = fetchMock;

    const { arxivFetch } = await load();
    const resp = await arxivFetch('http://arxiv.org/api/query?id_list=x');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/arxiv?id_list=x');
    expect(fetchMock.mock.calls[1][0]).toBe('http://arxiv.org/api/query?id_list=x');
    expect(resp._from).toBe('direct');
  });

  it('falls back to direct fetch when the proxy throws', async () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('http://localhost:8080/'),
    });
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('ECONNREFUSED'))
      .mockResolvedValueOnce({ ok: true, _from: 'direct-after-throw' });
    globalThis.fetch = fetchMock;

    const { arxivFetch } = await load();
    const resp = await arxivFetch('http://arxiv.org/api/query?id_list=x');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(resp._from).toBe('direct-after-throw');
  });

  it('skips the proxy entirely when not localhost', async () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('https://pquarterman17.github.io/'),
    });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, _from: 'direct' });
    globalThis.fetch = fetchMock;

    const { arxivFetch } = await load();
    const resp = await arxivFetch('http://arxiv.org/api/query?id_list=x');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('http://arxiv.org/api/query?id_list=x');
    expect(resp._from).toBe('direct');
  });

  it('uses direct fetch for non-arxiv URLs even on localhost', async () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('http://localhost:8080/'),
    });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    globalThis.fetch = fetchMock;

    const { arxivFetch } = await load();
    await arxivFetch('https://api.crossref.org/works/10.1/x');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('https://api.crossref.org/works/10.1/x');
  });
});

describe('window shims', () => {
  it('exposes arxivFetch + the two-underscore legacy aliases', async () => {
    await load();
    expect(typeof globalThis.arxivFetch).toBe('function');
    expect(typeof globalThis._isLocalhost).toBe('function');
    expect(typeof globalThis._toLocalProxy).toBe('function');
  });
});
