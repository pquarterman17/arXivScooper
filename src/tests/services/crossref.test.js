import { describe, it, expect, vi } from 'vitest';
import { lookupByDoi, searchByQuery, parseItem } from '../../services/crossref.js';

const SAMPLE_ITEM = {
  DOI: '10.1103/PhysRevB.42.123',
  title: ['Coherent transmon\n   qubits'],
  abstract: '<p>An <em>important</em> result\nabout coherence.</p>',
  published: { 'date-parts': [[2024, 5, 17]] },
  author: [
    { given: 'Alice', family: 'Smith' },
    { given: 'Bob', family: 'Jones' },
  ],
  'container-title': ['Phys. Rev. B'],
  volume: '42',
  page: '123',
};

function fakeFetch(payload, { status = 200, ok = true } = {}) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => payload,
  });
}

describe('parseItem', () => {
  it('normalizes whitespace in title and strips HTML from abstract', () => {
    const p = parseItem(SAMPLE_ITEM);
    expect(p.title).toBe('Coherent transmon qubits');
    expect(p.summary).toBe('An important result about coherence.');
  });

  it('builds id, doi, and url from the DOI', () => {
    const p = parseItem(SAMPLE_ITEM);
    expect(p.id).toBe('10.1103/PhysRevB.42.123');
    expect(p.doi).toBe('10.1103/PhysRevB.42.123');
    expect(p.url).toBe('https://doi.org/10.1103/PhysRevB.42.123');
  });

  it('builds shortAuthors with "et al." past 3 authors', () => {
    const many = {
      ...SAMPLE_ITEM,
      author: [
        { given: 'A', family: 'First' },
        { given: 'B', family: 'Second' },
        { given: 'C', family: 'Third' },
        { given: 'D', family: 'Fourth' },
      ],
    };
    expect(parseItem(many).shortAuthors).toBe('First et al.');
  });

  it('emits ISO published date from CrossRef date-parts', () => {
    expect(parseItem(SAMPLE_ITEM).published).toBe('2024-05-17');
  });

  it('falls back to article-number when page is missing', () => {
    const item = { ...SAMPLE_ITEM, page: undefined, 'article-number': 'L010101' };
    expect(parseItem(item).pages).toBe('L010101');
  });

  it('uses sourceCfg.journalName when container-title is missing', () => {
    const item = { ...SAMPLE_ITEM, 'container-title': [] };
    expect(parseItem(item, 'prl', { journalName: 'PRL' }).journal).toBe('PRL');
  });

  it('tags via auto-tag rules when provided', () => {
    const rules = { rules: [{ tag: 'transmon', patterns: ['transmon'] }] };
    expect(parseItem(SAMPLE_ITEM, 'crossref', {}, rules).tags).toEqual(['transmon']);
  });

  it('flags isCrossref: true', () => {
    expect(parseItem(SAMPLE_ITEM).isCrossref).toBe(true);
  });
});

describe('lookupByDoi', () => {
  it('hits /works/<doi> and returns parsed paper', async () => {
    const fetch = fakeFetch({ message: SAMPLE_ITEM });
    const paper = await lookupByDoi('10.1103/PhysRevB.42.123', { fetch });

    expect(fetch).toHaveBeenCalledWith(
      'https://api.crossref.org/works/10.1103%2FPhysRevB.42.123'
    );
    expect(paper.title).toBe('Coherent transmon qubits');
  });

  it('throws "DOI not found" on 404', async () => {
    const fetch = fakeFetch({}, { ok: false, status: 404 });
    await expect(lookupByDoi('10.1/missing', { fetch })).rejects.toThrow('DOI not found');
  });

  it('throws "Crossref returned <status>" on non-200', async () => {
    const fetch = fakeFetch({}, { ok: false, status: 500 });
    await expect(lookupByDoi('10.1/x', { fetch })).rejects.toThrow('Crossref returned 500');
  });

  it('throws when message is missing', async () => {
    const fetch = fakeFetch({}); // no .message
    await expect(lookupByDoi('10.1/x', { fetch })).rejects.toThrow('No data returned');
  });

  it('rejects empty doi', async () => {
    await expect(lookupByDoi('', { fetch: vi.fn() })).rejects.toThrow('doi is required');
  });
});

describe('searchByQuery', () => {
  it('builds the right /works query string', async () => {
    const fetch = fakeFetch({ message: { items: [SAMPLE_ITEM] } });
    await searchByQuery('coherence', { fetch });

    const url = fetch.mock.calls[0][0];
    expect(url).toMatch(/^https:\/\/api\.crossref\.org\/works\?/);
    expect(url).toContain('query=coherence');
    expect(url).toContain('rows=25');
    expect(url).toContain('sort=published');
    expect(url).toContain('order=desc');
  });

  it('adds an issn filter when sourceCfg.issn is set', async () => {
    const fetch = fakeFetch({ message: { items: [] } });
    await searchByQuery('x', { fetch, sourceCfg: { issn: '0031-9007' } });
    const url = fetch.mock.calls[0][0];
    expect(url).toContain('filter=issn');
    expect(decodeURIComponent(url)).toContain('issn:0031-9007');
  });

  it('respects sort=relevance', async () => {
    const fetch = fakeFetch({ message: { items: [] } });
    await searchByQuery('x', { fetch, sort: 'relevance' });
    expect(fetch.mock.calls[0][0]).toContain('sort=relevance');
  });

  it('respects sort=date-asc', async () => {
    const fetch = fakeFetch({ message: { items: [] } });
    await searchByQuery('x', { fetch, sort: 'date-asc' });
    expect(fetch.mock.calls[0][0]).toContain('order=asc');
  });

  it('returns parsed papers in the same order', async () => {
    const fetch = fakeFetch({ message: { items: [SAMPLE_ITEM, { ...SAMPLE_ITEM, DOI: '10.1/y' }] } });
    const papers = await searchByQuery('x', { fetch });
    expect(papers).toHaveLength(2);
    expect(papers[1].id).toBe('10.1/y');
  });

  it('returns [] when message.items is missing', async () => {
    const fetch = fakeFetch({ message: {} });
    expect(await searchByQuery('x', { fetch })).toEqual([]);
  });

  it('rejects empty query', async () => {
    await expect(searchByQuery('', { fetch: vi.fn() })).rejects.toThrow('query is required');
  });
});

describe('stripHtmlTags', () => {
  it('removes JATS/HTML tags from abstracts', async () => {
    const { stripHtmlTags } = await import('../../services/crossref.js');
    expect(stripHtmlTags('<jats:p>Hello <i>world</i></jats:p>')).toBe('Hello world');
  });

  it('does not leave a tag behind after nested or partial constructs', async () => {
    const { stripHtmlTags } = await import('../../services/crossref.js');
    // A single-pass replace would leave `<script>` behind here.
    expect(stripHtmlTags('<<script>script>alert(1)</script>')).not.toMatch(/<script/i);
    expect(stripHtmlTags('<<script>script>alert(1)</script>')).not.toContain('<');
    expect(stripHtmlTags('<scr<b>ipt>x')).not.toContain('<');
    expect(stripHtmlTags('')).toBe('');
    expect(stripHtmlTags(null)).toBe('');
  });
});
