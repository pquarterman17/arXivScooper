/**
 * Single source of truth for HTML escaping in the database UI.
 *
 * Extracted from paper_database.html boot block (line 433 pre-refactor)
 * as part of plan #8 boot-block polish. The browser's textContent->
 * innerHTML round-trip is the canonical "safe in HTML attribute or text
 * node" escape: it converts <, >, &, ", ' into the right entities for
 * those contexts.
 *
 * Pre-refactor this lived in three places:
 *   1. paper_database.html top-level (used by every template-string in
 *      the boot block).
 *   2. src/ui/database/drag-drop-import.js (private duplicate).
 *   3. Implicit in many other render functions that did manual replace
 *      chains.
 *
 * This module replaces (1). The drag-drop-import.js duplicate gets
 * removed in this same commit. Future renderers should import from
 * here rather than re-implementing.
 *
 * Note: this is HTML-content escape, NOT URL-attribute escape. For
 * `href="..."` values use encodeURIComponent on the relevant segment
 * separately.
 */

export function escapeHtml(text) {
  if (text == null || text === '') return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

/**
 * Escape a value for use inside a *single-quoted* JS string literal that is
 * itself embedded in an inline `onclick="fn('...')"` HTML attribute.
 *
 * Backslashes are escaped first (so a pre-existing `\'` can't be turned
 * into an unescaped quote), then the quote characters, line terminators
 * and every character that could be interpreted by the HTML parser
 * (`<`, `>`, `&`, `"`) are written as JS `\xNN` escapes, which the HTML
 * attribute parser leaves untouched.
 *
 * @param {unknown} s
 * @returns {string}
 */
export function escapeJsString(s) {
  return String(s == null ? '' : s)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\x22')
    .replace(/</g, '\\x3c')
    .replace(/>/g, '\\x3e')
    .replace(/&/g, '\\x26')
    .replace(/\r/g, '\\r')
    .replace(/\n/g, '\\n')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

// Window shim — the boot block has dozens of template literals that
// reference `escapeHtml(...)` by bare name (resolves through globalThis).
globalThis.escapeHtml = escapeHtml;
