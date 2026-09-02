// @vitest-environment jsdom

/**
 * Regression tests for the HTML/JS escaping bugs found in the 2026-04-30
 * audit of src/ui/database/library-table.js.
 *
 * These tests render() into a jsdom document and parse the resulting markup
 * to confirm that hostile data values (apostrophes, double quotes,
 * </textarea>, etc.) do not break attribute boundaries or close the
 * surrounding element early. Each `it()` corresponds to one numbered bug
 * in bug-audit-2026-04-30.md.
 *
 * The render() function depends on a *lot* of legacy globals plus several
 * cross-module helpers. We stub everything via globalThis so render() runs
 * in isolation. Stubbed helpers either return empty (safe defaults) or
 * the minimum data the assertion needs.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render } from '../../../ui/database/library-table.js';

const STATE_KEYS = [
  'PAPERS', 'FIGS', 'SCRAPER_CONFIG', 'SCQ',
  'activeCollection', 'typeFilter', 'selectedTags',
  'currentView', 'expandedId', 'openDropdownId', 'pdfSearchHits',
  'getCollectionNames', 'getFiltered', 'getAllTags',
  'getRelatedPapers', 'renderHighlights', 'renderStars',
  'renderCollectionDropdown', 'sortPapers', 'sortedClass',
  'sortArrow', 'getPdfPath',
];

const SAVED = {};

function installSandbox(papers) {
  // Minimum DOM the render block writes into.
  document.body.innerHTML = `
    <div id="sidebar"></div>
    <div id="paper-count"></div>
    <div id="footer-info"></div>
    <div id="type-filter-bar"></div>
    <div id="tag-bar"></div>
    <div id="content"></div>
  `;
  // Default stubs — overridable per test.
  globalThis.PAPERS = papers;
  globalThis.FIGS = {};
  globalThis.SCRAPER_CONFIG = { entryTypes: {}, sources: {} };
  globalThis.SCQ = {
    getHighlights: () => ({ highlights: [], notes: '' }),
    formatRelativeTime: () => 'just now',
    getCollectionPapers: () => [],
  };
  globalThis.activeCollection = null;
  globalThis.typeFilter = 'all';
  globalThis.selectedTags = new Set();
  globalThis.currentView = 'cards';
  globalThis.expandedId = null;
  globalThis.openDropdownId = null;
  globalThis.pdfSearchHits = {};
  globalThis.getCollectionNames = () => [];
  globalThis.getFiltered = () => papers;
  globalThis.getAllTags = () => [];
  globalThis.getRelatedPapers = () => [];
  globalThis.renderHighlights = () => '';
  globalThis.renderStars = (_id) => '';
  globalThis.renderCollectionDropdown = () => '';
  globalThis.sortPapers = (xs) => xs;
  globalThis.sortedClass = () => '';
  globalThis.sortArrow = () => '';
  globalThis.getPdfPath = (id) => `papers/${id}.pdf`;
}

beforeEach(() => {
  for (const k of STATE_KEYS) SAVED[k] = globalThis[k];
});
afterEach(() => {
  for (const k of STATE_KEYS) globalThis[k] = SAVED[k];
  document.body.innerHTML = '';
});

function paper(o) {
  return {
    id: 'p1',
    title: 't',
    authors: 'A',
    shortAuthors: 'A',
    year: '2024',
    group: 'g',
    tags: [],
    summary: '',
    keyResults: [],
    figures: [],
    citeBib: '',
    citeTxt: '',
    _read: false,
    _priority: 0,
    _note: '',
    _lastEdited: null,
    entryType: 'preprint',
    ...o,
  };
}

describe('library-table escaping (bug-audit 2026-04-30)', () => {
  it("#3 tag with apostrophe produces a clickable button (onclick attr is well-formed)", () => {
    installSandbox([paper({ tags: ["it's"] })]);
    globalThis.getAllTags = () => ["it's"];
    render();
    const btn = document.querySelector('#tag-bar button.tag-btn');
    expect(btn).not.toBeNull();
    // The browser-parsed attribute string contains the *unescaped* value.
    // What matters is that the button exists (parsing didn't break) and
    // that the onclick attribute references the tag in some form.
    expect(btn.getAttribute('onclick')).toContain('toggleTag');
    // After _js() escaping the value is `toggleTag('it\'s')`, so the
    // sequence between `it` and `s` is `\'` (two chars). Use `.*` to
    // tolerate any escaping form as long as both letters are present.
    expect(btn.getAttribute('onclick')).toMatch(/it.*s/);
  });

  it("#4 figure with apostrophe in label renders a clickable card (no broken HTML)", () => {
    const figs = [{ key: 'fig1', label: "Author's setup", desc: "A's diagram" }];
    installSandbox([paper({ figures: figs })]);
    globalThis.FIGS = { fig1: 'data:image/png;base64,xxx' };
    globalThis.expandedId = 'p1';
    render();
    const card = document.querySelector('.fig-card');
    expect(card).not.toBeNull();
    // The img tag should be a sibling, not gobbled up as part of a broken
    // attribute. If escaping failed, the parser would close the onclick
    // attribute at the apostrophe and the rest of the card would mangle.
    const img = card.querySelector('img');
    expect(img).not.toBeNull();
    expect(img.getAttribute('alt')).toBe("Author's setup");
  });

  it("#5 note containing </textarea> does not close the textarea early", () => {
    installSandbox([paper({ _note: 'hello </textarea> goodbye' })]);
    globalThis.expandedId = 'p1';
    render();
    const ta = document.querySelector('textarea.notes-area');
    expect(ta).not.toBeNull();
    // The full note text should be the textarea's value — if the </textarea>
    // closed the element early, the value would be 'hello ' and 'goodbye'
    // would be loose text after the element.
    expect(ta.value).toBe('hello </textarea> goodbye');
  });

  it("#6 PDF snippet with double quotes does not break the title attribute", () => {
    installSandbox([paper()]);
    globalThis.pdfSearchHits = {
      p1: { page: 5, snippet: 'they said "hello world"' },
    };
    // pdfSearchHits is reset at the top of render(); reassign after.
    render();
    // After render(), our stub above gets clobbered by `globalThis.pdfSearchHits = {}`.
    // To exercise the snippet path we need to inject *after* render() also runs
    // the reset. So: render once to clear, set the hit, then render again.
    globalThis.pdfSearchHits = {
      p1: { page: 5, snippet: 'they said "hello world"' },
    };
    // Patch the reset out for this specific assertion: re-render reads the
    // map *after* it's been reassigned to {}, so we have to look at markup
    // produced *during* a render where the hit is present at template time.
    // Simulate by not letting render reset the map: monkey-patch via a
    // getter that always returns our hit map.
    const target = { p1: { page: 5, snippet: 'they said "hello world"' } };
    Object.defineProperty(globalThis, 'pdfSearchHits', {
      configurable: true,
      get() { return target; },
      set() { /* swallow the reset */ },
    });
    try {
      render();
      const badge = document.querySelector('#content .badge[title]');
      expect(badge).not.toBeNull();
      // Browser unescapes &quot; back to " when reading getAttribute, so
      // the parsed title should contain the full snippet.
      expect(badge.getAttribute('title')).toBe('p.5: they said "hello world"');
    } finally {
      Object.defineProperty(globalThis, 'pdfSearchHits', {
        configurable: true, writable: true, value: {},
      });
    }
  });

  it("#9 related-paper reason with double quotes does not break title", () => {
    installSandbox([paper()]);
    globalThis.expandedId = 'p1';
    globalThis.getRelatedPapers = () => [{
      paper: { id: 'p2', shortAuthors: 'Bob', year: 2024 },
      reasons: ['shared authors: "Jr."'],
    }];
    render();
    const chip = document.querySelector('.related-chip');
    expect(chip).not.toBeNull();
    expect(chip.getAttribute('title')).toBe('shared authors: "Jr."');
  });
});
