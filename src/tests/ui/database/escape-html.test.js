// @vitest-environment jsdom

/**
 * Plan #8 — escape-html extraction regression.
 */

import { describe, it, expect, beforeEach } from 'vitest';

beforeEach(() => {
  delete globalThis.escapeHtml;
});

async function load() {
  return await import('../../../ui/database/escape-html.js?v=' + Math.random());
}

describe('escapeHtml', () => {
  it('shims globalThis.escapeHtml at import time', async () => {
    await load();
    expect(typeof globalThis.escapeHtml).toBe('function');
  });

  it('escapes the four primary HTML special characters', async () => {
    const { escapeHtml } = await load();
    // Browser textContent->innerHTML always escapes < > & for HTML safety
    expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
    expect(escapeHtml('a & b')).toBe('a &amp; b');
  });

  it('preserves plain text unchanged', async () => {
    const { escapeHtml } = await load();
    expect(escapeHtml('Tantalum transmons')).toBe('Tantalum transmons');
  });

  it('returns "" for null / undefined / empty input', async () => {
    const { escapeHtml } = await load();
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(undefined)).toBe('');
    expect(escapeHtml('')).toBe('');
  });

  it('coerces non-string input to string before escaping', async () => {
    const { escapeHtml } = await load();
    expect(escapeHtml(42)).toBe('42');
    expect(escapeHtml(false)).toBe('false');
  });

  it('zero (a falsy number) still produces "0", not ""', async () => {
    /* Pre-refactor `if (!text) return ''` returned '' for 0. New
     * implementation uses `text == null || text === ''` so 0 round-trips. */
    const { escapeHtml } = await load();
    expect(escapeHtml(0)).toBe('0');
  });

  it('output is safe to inject into innerHTML and re-parses to the original text', async () => {
    const { escapeHtml } = await load();
    const dangerous = '<img src=x onerror="alert(1)">';
    const escaped = escapeHtml(dangerous);
    const wrap = document.createElement('div');
    wrap.innerHTML = escaped;
    // No <img> got created — the entire string round-trips as text
    expect(wrap.querySelector('img')).toBeNull();
    expect(wrap.textContent).toBe(dangerous);
  });
});

describe('escapeJsString', () => {
  it('escapes backslashes before quotes so a pre-escaped quote cannot break out', async () => {
    const { escapeJsString } = await load();
    expect(escapeJsString("it's")).toBe("it\\'s");
    expect(escapeJsString("a\\'b")).toBe("a\\\\\\'b");
    expect(escapeJsString('back\\slash')).toBe('back\\\\slash');
  });

  it('neutralises HTML-significant characters and line terminators', async () => {
    const { escapeJsString } = await load();
    const out = escapeJsString('<b>"x"&\n\r\u2028\u2029');
    expect(out).toBe('\\x3cb\\x3e\\x22x\\x22\\x26\\n\\r\\u2028\\u2029');
    expect(out).not.toMatch(/["<>&\n\r\u2028\u2029]/);
  });

  it('treats null/undefined as empty', async () => {
    const { escapeJsString } = await load();
    expect(escapeJsString(null)).toBe('');
    expect(escapeJsString(undefined)).toBe('');
  });

  it('survives HTML attribute parsing and JS string-literal parsing', async () => {
    const { escapeJsString } = await load();
    const value = `it's "quoted" <b>&amp;</b> back\\slash\nnext`;
    document.body.innerHTML = `<button onclick="__seen('${escapeJsString(value)}')">x</button>`;
    // What the HTML parser hands to the JS engine for the handler body:
    const body = document.querySelector('button').getAttribute('onclick');
    const seen = [];
    new Function('__seen', body)((v) => seen.push(v));
    expect(seen).toEqual([value]);
  });
});
