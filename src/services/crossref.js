/**
 * CrossRef client (DOM-free).
 *
 * Two entry points:
 *   - lookupByDoi(doi, opts)        — fetch a single paper by DOI
 *   - searchByQuery(query, opts)    — issue a /works query, optionally
 *                                     filtered by ISSN
 *
 * Both return paper objects in the shape downstream UI expects:
 *   { id, title, summary, year, published, authors, shortAuthors,
 *     categories, source, url, doi, journal, volume, pages, tags,
 *     isCrossref: true }
 *
 * The `fetch` implementation is dependency-injected via opts.fetch so
 * services stay framework-free; the scraper page uses its proxy-aware
 * `corsFetch`, tests can pass a stub, and a future Vue port can plug
 * in fetch wrappers without touching this module.
 */

import { autoTag } from './auto-tag.js';

const DEFAULT_BASE = 'https://api.crossref.org/works';

function _defaultFetch() {
  if (typeof globalThis.corsFetch === 'function') return globalThis.corsFetch;
  return (url) => fetch(url);
}

/**
 * Convert a CrossRef `message` item to our paper shape.
 *
 * @param item        — the raw item from CrossRef (`data.message` for a
 *                       lookup, or each entry of `data.message.items`
 *                       for a search).
 * @param sourceKey   — the key under which to record this source
 *                       (e.g. 'crossref', 'prl').
 * @param sourceCfg   — optional source config object; supplies fallback
 *                       journalName and is searched for matching ISSN to
 *                       refine sourceKey.
 * @param autoTagRules — optional auto-tag rule object (services/auto-tag).
 */
/**
 * Strip HTML/JATS tags from an abstract. Repeats until no tag pattern is
 * left so nested/partial constructs like `<<script>script>` can't survive
 * a single pass.
 * @param {string} s
 * @returns {string}
 */
export function stripHtmlTags(s) {
  let out = String(s ?? '');
  let prev;
  do {
    prev = out;
    out = out.replace(/<[^>]*>/g, '');
  } while (out !== prev);
  return out;
}

export function parseItem(item, sourceKey = 'crossref', sourceCfg = {}, autoTagRules = null) {
  const doi = item.DOI || '';
  const title = (item.title || [''])[0].replace(/\s+/g, ' ').trim();
  const abstract = stripHtmlTags(item.abstract || '').replace(/\s+/g, ' ').trim();
  const dateParts = item.published?.['date-parts']?.[0] || [];
  const year = dateParts[0] || '';
  const month = String(dateParts[1] || 1).padStart(2, '0');
  const day = String(dateParts[2] || 1).padStart(2, '0');
  const published = year ? `${year}-${month}-${day}` : '';

  const authors = (item.author || []).map(a => `${a.given || ''} ${a.family || ''}`.trim());
  const shortAuthors = authors.length > 3
    ? (authors[0].split(' ').pop() || 'Unknown') + ' et al.'
    : authors.map(a => a.split(' ').pop()).join(', ');

  const journal = (item['container-title'] || [''])[0];
  const volume = item.volume || '';
  const pages = item.page || item['article-number'] || '';

  const tags = autoTagRules ? autoTag(`${title} ${abstract}`, autoTagRules) : [];

  return {
    id: doi,
    title,
    summary: abstract,
    year,
    published,
    authors: authors.join(', '),
    shortAuthors,
    categories: [],
    source: sourceKey,
    url: `https://doi.org/${doi}`,
    doi,
    journal: journal || sourceCfg.journalName || '',
    volume,
    pages,
    tags,
    isCrossref: true,
  };
}

/**
 * Fetch a single paper by DOI.
 *
 * @param doi               — bare DOI (use services/doi.extractDoi to clean)
 * @param opts.fetch        — fetch function (default: globalThis.corsFetch)
 * @param opts.base         — API base (default: api.crossref.org/works)
 * @param opts.sourceKey    — sourceKey to tag the result with (default: 'crossref')
 * @param opts.sourceCfg    — sourceCfg passed through to parseItem
 * @param opts.autoTagRules — auto-tag rule object
 */
export async function lookupByDoi(doi, opts = {}) {
  if (!doi) throw new Error('lookupByDoi: doi is required');
  const fetcher = opts.fetch || _defaultFetch();
  const base = opts.base || DEFAULT_BASE;
  const url = `${base}/${encodeURIComponent(doi)}`;

  const resp = await fetcher(url);
  if (!resp.ok) {
    if (resp.status === 404) throw new Error('DOI not found');
    throw new Error(`Crossref returned ${resp.status}`);
  }
  const data = await resp.json();
  const item = data?.message;
  if (!item) throw new Error('No data returned for this DOI');

  return parseItem(item, opts.sourceKey || 'crossref', opts.sourceCfg, opts.autoTagRules);
}

/**
 * Run a CrossRef /works query.
 *
 * @param query          — the search string
 * @param opts.fetch     — fetch function (default: globalThis.corsFetch)
 * @param opts.base      — API base
 * @param opts.sourceKey — sourceKey to tag results with
 * @param opts.sourceCfg — { issn, journalName, label, ... }
 * @param opts.maxResults — default 25
 * @param opts.sort      — 'relevance' | 'date-desc' | 'date-asc' (default 'date-desc')
 * @param opts.autoTagRules
 */
export async function searchByQuery(query, opts = {}) {
  if (!query) throw new Error('searchByQuery: query is required');
  const fetcher = opts.fetch || _defaultFetch();
  const base = opts.base || DEFAULT_BASE;
  const sourceCfg = opts.sourceCfg || {};
  const maxResults = opts.maxResults || 25;
  const sort = opts.sort || 'date-desc';

  const params = new URLSearchParams({
    query,
    rows: String(maxResults),
    sort: sort === 'relevance' ? 'relevance' : 'published',
    order: sort === 'date-asc' ? 'asc' : 'desc',
  });
  if (sourceCfg.issn) params.set('filter', `issn:${sourceCfg.issn}`);

  const url = `${base}?${params}`;
  const resp = await fetcher(url);
  if (!resp.ok) throw new Error(`Crossref returned ${resp.status}`);

  const data = await resp.json();
  const items = data?.message?.items || [];
  return items.map(item =>
    parseItem(item, opts.sourceKey || 'crossref', sourceCfg, opts.autoTagRules)
  );
}
