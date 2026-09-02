/**
 * Tag manager modal (plan #8 strangler-fig migration).
 *
 * Lists every tag with its paper count, plus per-row Rename / Merge /
 * Delete actions. Each mutation refreshes the legacy PAPERS array via
 * loadPapersFromDB, removes the tag from the active filter set, and
 * re-renders both the table and the modal so the count updates live.
 *
 * All four functions are reachable from inline onclick attributes.
 */

import { escapeJsString } from './escape-html.js';

function _scq() { return globalThis.SCQ; }
function _refresh() {
  if (typeof globalThis.loadPapersFromDB === 'function') globalThis.loadPapersFromDB();
  if (typeof globalThis.render === 'function') globalThis.render();
}
function _untag(tag) {
  const sel = globalThis.selectedTags;
  if (sel && typeof sel.delete === 'function') sel.delete(tag);
}

export function showTagManagerModal() {
  const SCQ = _scq();
  const PAPERS = globalThis.PAPERS || [];
  const tagCounts = SCQ.getAllTags();
  const sorted = Object.entries(tagCounts).sort((a, b) => b[1] - a[1]);
  const modalRoot = document.getElementById('modal-root');
  modalRoot.innerHTML = `
    <div class="modal-backdrop" onclick="closeModal()">
      <div class="modal" onclick="event.stopPropagation()" style="min-width:380px;max-width:500px">
        <h3>Tag Manager</h3>
        <p style="font-size:11px;color:var(--text3);margin-bottom:8px">${sorted.length} tags across ${PAPERS.length} papers</p>
        <div class="tag-mgmt-modal-list">
          ${sorted.map(([tag, count]) => `
            <div class="tag-mgmt-item">
              <span class="tag-name">${tag}</span>
              <span class="tag-count">${count} paper${count !== 1 ? 's' : ''}</span>
              <button onclick="event.stopPropagation(); promptRenameTag('${escapeJsString(tag)}')">Rename</button>
              <button onclick="event.stopPropagation(); promptMergeTag('${escapeJsString(tag)}')">Merge</button>
              <button class="danger" onclick="event.stopPropagation(); doDeleteTag('${escapeJsString(tag)}')">Delete</button>
            </div>`).join('')}
        </div>
        <div class="modal-btns" style="margin-top:12px">
          <button class="modal-btn primary" onclick="closeModal()">Done</button>
        </div>
      </div>
    </div>`;
}

export function promptRenameTag(oldTag) {
  const newTag = prompt('Rename "' + oldTag + '" to:', oldTag);
  if (!newTag || newTag.trim() === '' || newTag.trim() === oldTag) return;
  _scq().renameTags(oldTag, newTag.trim().toLowerCase());
  _refresh();
  _untag(oldTag);
  showTagManagerModal();
}

export function promptMergeTag(tag) {
  const allTags = Object.keys(_scq().getAllTags()).filter(t => t !== tag).sort();
  const target = prompt('Merge "' + tag + '" into which tag?\n\nExisting tags: ' + allTags.join(', '), '');
  if (!target || target.trim() === '') return;
  _scq().renameTags(tag, target.trim().toLowerCase());
  _refresh();
  _untag(tag);
  showTagManagerModal();
}

export function doDeleteTag(tag) {
  if (!confirm('Remove the tag "' + tag + '" from all papers?')) return;
  _scq().deleteTag(tag);
  _refresh();
  _untag(tag);
  showTagManagerModal();
}
