/**
 * Collection UI helpers (plan #8 strangler-fig migration).
 *
 * Sidebar / per-paper collection interactions:
 *   - getCollectionNames, isPaperInCollection, togglePaperCollection
 *   - setActiveCollection
 *   - showNewCollectionModal, createCollection, deleteCollectionUI
 *   - toggleCollectionDropdown, renderCollectionDropdown
 *   - closeModal (the generic modal-root dismisser used by every modal)
 *
 * `closeModal` lives here because Collections owns both the new-collection
 * modal and the most common "Cancel" path — but it's the project-wide
 * modal closer, called from many other modules' inline onclick attributes
 * via the window shim.
 *
 * Cross-module legacy globals reached via `globalThis`:
 *   - render            — re-renders the table after state changes
 *   - activeCollection  — `let` in the legacy boot block
 *   - openDropdownId    — `let` in the legacy boot block
 *
 * The two `let` bindings can't be reassigned from a module, so we mutate
 * via globalThis and rely on the legacy block to re-read them on each
 * render() call. setActiveCollection and toggleCollectionDropdown both
 * write through `window.*` so legacy reads stay consistent.
 */

import { escapeJsString } from './escape-html.js';

function _scq() { return globalThis.SCQ; }
function _render() {
  if (typeof globalThis.render === 'function') globalThis.render();
}

export function getCollectionNames() {
  return _scq().getCollections();
}

export function isPaperInCollection(paperId, collName) {
  return _scq().getCollectionsForPaper(paperId).includes(collName);
}

export function togglePaperCollection(paperId, collName, event) {
  if (event) event.stopPropagation();
  const SCQ = _scq();
  if (isPaperInCollection(paperId, collName)) {
    SCQ.removeFromCollection(collName, paperId);
  } else {
    SCQ.addToCollection(collName, paperId);
  }
  _render();
}

export function setActiveCollection(name) {
  const current = globalThis.activeCollection;
  globalThis.activeCollection = (current === name) ? null : name;
  _render();
}

export function showNewCollectionModal() {
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="modal-overlay" onclick="closeModal()">
    <div class="modal" onclick="event.stopPropagation()">
      <h3>New Collection</h3>
      <input type="text" id="new-coll-name" placeholder="e.g., Dissertation Ch.3, Group meeting..." autofocus>
      <div class="modal-btns">
        <button class="modal-btn" onclick="closeModal()">Cancel</button>
        <button class="modal-btn primary" onclick="createCollection()">Create</button>
      </div>
    </div>
  </div>`;
  setTimeout(() => document.getElementById('new-coll-name').focus(), 50);
  document.getElementById('new-coll-name').addEventListener('keydown', e => {
    if (e.key === 'Enter') createCollection();
    if (e.key === 'Escape') closeModal();
  });
}

export function createCollection() {
  const name = document.getElementById('new-coll-name').value.trim();
  if (!name) return;
  if (getCollectionNames().includes(name)) { alert('Collection already exists'); return; }
  _scq().run("INSERT OR IGNORE INTO collections (name, paper_id) VALUES (?, '')", [name]);
  closeModal();
  _render();
}

export function deleteCollectionUI(name, event) {
  if (event) event.stopPropagation();
  if (!confirm('Delete collection "' + name + '"? (Papers won\'t be removed from the database.)')) return;
  _scq().deleteCollection(name);
  if (globalThis.activeCollection === name) globalThis.activeCollection = null;
  _render();
}

export function closeModal() {
  document.getElementById('modal-root').innerHTML = '';
}

export function toggleCollectionDropdown(paperId, event) {
  if (event) event.stopPropagation();
  const current = globalThis.openDropdownId;
  globalThis.openDropdownId = (current === paperId) ? null : paperId;
  _render();
}

export function renderCollectionDropdown(paperId) {
  const names = getCollectionNames();
  if (names.length === 0) return `<div class="collection-dropdown"><div class="collection-dropdown-item" style="color:var(--text3)">No collections yet</div></div>`;
  return `<div class="collection-dropdown">${names.map(n =>
    `<div class="collection-dropdown-item" onclick="togglePaperCollection('${escapeJsString(paperId)}', '${escapeJsString(n)}', event)">
      <span class="cdi-check">${isPaperInCollection(paperId, n) ? '&#10003;' : ''}</span>
      <span>${n}</span>
    </div>`
  ).join('')}</div>`;
}
