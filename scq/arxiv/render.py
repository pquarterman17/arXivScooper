"""HTML digest rendering (plan #13).

One entry point: :func:`generate_html_digest`. Takes a ranked list of
arXiv papers (as produced by :func:`scq.arxiv.search.rank_papers`) and
writes a styled HTML page with triage buttons (mark as add / dismiss /
download for the SCQ database).

Pure rendering — no email side-effect, no DB writes. Output is the
file path. The accompanying CSS lives inline in this module rather than
in ``src/styles/`` because the digest is delivered both as an emailed
HTML body and as a standalone artifact downloadable from GH Actions;
both contexts need the styling embedded.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from html import escape as _esc

from scq.arxiv.common import display_title
from scq.arxiv.search import ARXIV_CATEGORIES

# ─── HTML Digest Generator ───


def generate_html_digest(papers, digest_date, output_path):
    """Generate a styled HTML digest with triage buttons."""
    high_relevance = [p for p in papers if p["relevance_score"] >= 20]
    medium_relevance = [p for p in papers if 5 <= p["relevance_score"] < 20]
    low_relevance = [p for p in papers if p["relevance_score"] < 5]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SCQ arXiv Digest — {digest_date}</title>
<style>
  :root {{
    --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
    --text: #e6edf3; --text2: #8b949e; --text3: #6e7681;
    --accent: #58a6ff; --green: #3fb950; --orange: #d29922;
    --red: #f85149; --border: #30363d;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.6;
    max-width: 1000px; margin: 0 auto; padding: 24px;
  }}
  h1 {{ color: var(--accent); margin-bottom: 4px; font-size: 24px; }}
  .subtitle {{ color: var(--text2); margin-bottom: 24px; font-size: 14px; }}
  .stats {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .stat {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
           padding: 8px 16px; font-size: 13px; }}
  .stat b {{ color: var(--accent); }}

  .section-header {{
    font-size: 16px; font-weight: 600; margin: 24px 0 12px 0;
    padding: 8px 12px; border-radius: 6px;
  }}
  .section-high {{ background: rgba(63,185,80,0.1); border-left: 3px solid var(--green); color: var(--green); }}
  .section-medium {{ background: rgba(210,153,34,0.1); border-left: 3px solid var(--orange); color: var(--orange); }}
  .section-low {{ background: rgba(139,148,158,0.1); border-left: 3px solid var(--text3); color: var(--text3); }}

  .paper-card {{
    background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; margin-bottom: 12px; transition: border-color 0.2s;
  }}
  .paper-card:hover {{ border-color: var(--accent); }}
  .paper-card.triaged-add {{ border-color: var(--green); opacity: 0.7; }}
  .paper-card.triaged-skip {{ opacity: 0.35; }}

  .paper-title {{ font-size: 15px; font-weight: 600; margin-bottom: 4px; }}
  .paper-title a {{ color: var(--text); text-decoration: none; }}
  .paper-title a:hover {{ color: var(--accent); text-decoration: underline; }}
  .paper-meta {{ font-size: 12px; color: var(--text2); margin-bottom: 8px; }}
  .paper-meta a {{ color: var(--accent); text-decoration: none; }}
  .paper-abstract {{ font-size: 13px; color: var(--text2); margin-bottom: 10px;
                     max-height: 120px; overflow: hidden; transition: max-height 0.3s; }}
  .paper-abstract.expanded {{ max-height: none; }}
  .expand-btn {{ font-size: 11px; color: var(--accent); cursor: pointer; background: none;
                  border: none; padding: 0; margin-bottom: 8px; }}

  .keywords {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 10px; }}
  .kw-tag {{ font-size: 10px; padding: 2px 6px; border-radius: 3px;
             background: rgba(88,166,255,0.15); color: var(--accent); }}
  .score-badge {{ font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px;
                  float: right; }}
  .score-high {{ background: rgba(63,185,80,0.2); color: var(--green); }}
  .score-med {{ background: rgba(210,153,34,0.2); color: var(--orange); }}
  .score-low {{ background: rgba(139,148,158,0.2); color: var(--text3); }}

  .triage-row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  .triage-btns {{ display: flex; gap: 6px; align-items: center; }}
  .triage-btns button, .triage-btns a {{
    font-size: 11px; font-weight: 600; padding: 4px 12px; border-radius: 4px;
    cursor: pointer; border: 1px solid var(--border); font-family: inherit;
    transition: all 0.2s; text-decoration: none; display: inline-block;
  }}
  .btn-add {{ background: rgba(63,185,80,0.15); color: var(--green); border-color: var(--green); }}
  .btn-add:hover {{ background: rgba(63,185,80,0.3); }}
  .btn-add.active {{ background: rgba(63,185,80,0.4); box-shadow: 0 0 0 2px var(--green); }}
  .btn-star {{ background: rgba(210,153,34,0.15); color: var(--orange); border-color: var(--orange); }}
  .btn-star:hover {{ background: rgba(210,153,34,0.3); }}
  .btn-star.active {{ background: rgba(210,153,34,0.4); box-shadow: 0 0 0 2px var(--orange); }}
  .btn-ignore {{ background: var(--bg3); color: var(--text3); }}
  .btn-ignore:hover {{ background: var(--border); color: var(--text2); }}
  .btn-ignore.active {{ background: rgba(248,81,73,0.2); color: var(--red); border-color: var(--red); }}
  .btn-skip {{ background: var(--bg3); color: var(--text3); }}
  .btn-skip:hover {{ background: var(--border); color: var(--text2); }}
  .btn-pdf {{ background: rgba(88,166,255,0.1); color: var(--accent); border-color: var(--accent); }}
  .btn-pdf:hover {{ background: rgba(88,166,255,0.25); }}

  .tag-row {{ display: flex; gap: 4px; flex-wrap: wrap; margin-top: 6px; }}
  .tag-btn {{ font-size: 10px; padding: 2px 8px; border-radius: 10px; cursor: pointer;
              border: 1px solid var(--border); background: var(--bg3); color: var(--text2);
              transition: all 0.15s; font-family: inherit; }}
  .tag-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
  .tag-btn.selected {{ background: rgba(88,166,255,0.2); color: var(--accent); border-color: var(--accent); }}
  .custom-tag-input {{ font-size: 10px; padding: 2px 8px; border-radius: 10px;
                       border: 1px solid var(--border); background: var(--bg); color: var(--text);
                       width: 90px; font-family: inherit; }}
  .custom-tag-input::placeholder {{ color: var(--text3); }}

  .triage-status {{ font-size: 11px; font-weight: 600; margin-left: 8px; }}

  .paper-card.triaged-ignore {{ opacity: 0.25; }}
  .paper-card.triaged-star {{ border-color: var(--orange); border-width: 2px; }}

  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border);
             font-size: 12px; color: var(--text3); text-align: center; }}
  .save-bar {{
    position: sticky; bottom: 0; background: var(--bg2); border-top: 1px solid var(--border);
    padding: 12px 16px; text-align: center; z-index: 100;
  }}
  .save-bar button {{
    font-size: 13px; font-weight: 600; padding: 8px 24px; border-radius: 6px;
    cursor: pointer; border: none; font-family: inherit;
    background: var(--green); color: #000;
  }}
  .save-bar button:hover {{ filter: brightness(1.1); }}
  .save-bar .count {{ font-size: 12px; color: var(--text2); margin-left: 12px; }}
</style>
</head>
<body>

<h1>SCQ arXiv Digest</h1>
<p class="subtitle">{digest_date} &mdash; {len(papers)} papers from {", ".join(ARXIV_CATEGORIES)}</p>

<div class="stats">
  <div class="stat"><b>{len(high_relevance)}</b> high relevance</div>
  <div class="stat"><b>{len(medium_relevance)}</b> medium relevance</div>
  <div class="stat"><b>{len(low_relevance)}</b> other</div>
</div>
"""

    def render_papers(papers_list):
        cards = ""
        for p in papers_list:
            score = p["relevance_score"]
            score_class = (
                "score-high" if score >= 20 else ("score-med" if score >= 5 else "score-low")
            )
            kw_html = "".join(
                f'<span class="kw-tag">{_esc(kw)}</span>' for kw in p["matched_keywords"][:6]
            )
            safe_id = p["id"].replace(".", "_")
            authors_short = _esc(p["short_authors"])
            cats = _esc(", ".join(p["categories"][:3]))
            title = _esc(display_title(p))
            abs_url = _esc(p["abs_url"], quote=True)
            pdf_url = _esc(p["pdf_url"], quote=True)

            # Escape HTML in abstract
            abstract = p["abstract"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            # JSON-safe paper data for the triage button
            paper_json = (
                json.dumps(
                    {
                        "id": p["id"],
                        "title": p["title"],
                        "authors": p["authors"],
                        "short_authors": p["short_authors"],
                        "year": datetime.fromisoformat(p["published"]).year,
                        "abstract": p["abstract"][:500],
                        "categories": p["categories"],
                        "pdf_url": p["pdf_url"],
                        "abs_url": p["abs_url"],
                    }
                )
                .replace("'", "&#39;")
                .replace('"', "&quot;")
            )

            cards += f"""
  <div class="paper-card" id="card-{safe_id}">
    <span class="score-badge {score_class}">{score}</span>
    <div class="paper-title"><a href="{abs_url}" target="_blank">{title}</a></div>
    <div class="paper-meta">
      {authors_short} &middot; {p["published"][:10]} &middot; {cats}
      &middot; <a href="{abs_url}" target="_blank">{p["id"]}</a>
    </div>
    <div class="paper-abstract" id="abs-{safe_id}">{abstract}</div>
    <button class="expand-btn" onclick="toggleAbstract('{safe_id}')">show more</button>
    <div class="keywords">{kw_html}</div>
    <div class="triage-row">
      <div class="triage-btns">
        <button class="btn-add" id="btn-add-{safe_id}" onclick="triagePaper('{safe_id}', 'add', '{paper_json}')">+ Read List</button>
        <button class="btn-star" id="btn-star-{safe_id}" onclick="triagePaper('{safe_id}', 'star', '{paper_json}')">&#9733; Star</button>
        <button class="btn-ignore" id="btn-ignore-{safe_id}" onclick="triagePaper('{safe_id}', 'ignore')">&#10005; Ignore</button>
        <a class="btn-pdf" href="{pdf_url}" target="_blank">PDF</a>
        <span class="triage-status" id="status-{safe_id}"></span>
      </div>
    </div>
    <div class="tag-row" id="tags-{safe_id}"></div>
  </div>"""
        return cards

    if high_relevance:
        html += '<div class="section-header section-high">High Relevance</div>'
        html += render_papers(high_relevance)
    if medium_relevance:
        html += '<div class="section-header section-medium">Medium Relevance</div>'
        html += render_papers(medium_relevance)
    if low_relevance:
        html += f'<div class="section-header section-low">Other ({len(low_relevance)} papers)</div>'
        html += render_papers(low_relevance)

    html += f"""
<div class="save-bar">
  <button onclick="savePending()">Save Triage Selections</button>
  <span class="count" id="triage-count">0 papers selected</span>
</div>

<div class="footer">
  Generated by SCQ arXiv Digest &middot; Categories: {", ".join(ARXIV_CATEGORIES)}
</div>

<script>
// ── State ──
const triaged = {{}};           // id -> {{action, data, tags, priority}}
const paperTags = {{}};         // id -> Set of tag strings
const TRIAGE_PREFIX = 'scq-triage-';
const TRIAGE_EXPIRY_DAYS = 14;

// Preset tags from scraper_config
const PRESET_TAGS = [
  "tantalum","aluminum","niobium","TLS","surface loss","Josephson junction",
  "transmon","resonator","qubit","kinetic inductance","quasiparticle",
  "oxide","sapphire","silicon","coherence","decoherence","microwave",
  "cryogenic","fabrication","quality factor"
];

// ── localStorage helpers ──
function persistTriage(id) {{
  try {{
    localStorage.setItem(TRIAGE_PREFIX + id, JSON.stringify({{
      action: triaged[id]?.action || 'none',
      tags: Array.from(paperTags[id] || []),
      priority: triaged[id]?.priority || 0,
      ts: Date.now()
    }}));
  }} catch(e) {{}}
}}

function getPersistedTriage(id) {{
  try {{
    const raw = localStorage.getItem(TRIAGE_PREFIX + id);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (Date.now() - data.ts > TRIAGE_EXPIRY_DAYS * 86400000) {{
      localStorage.removeItem(TRIAGE_PREFIX + id);
      return null;
    }}
    return data;
  }} catch(e) {{ return null; }}
}}

// ── Render tag row for a paper ──
function renderTagRow(id) {{
  const row = document.getElementById('tags-' + id);
  if (!row) return;
  const selected = paperTags[id] || new Set();
  let html = PRESET_TAGS.map(t =>
    `<button class="tag-btn${{selected.has(t) ? ' selected' : ''}}" onclick="toggleTag('${{id}}','${{t}}')">${{t}}</button>`
  ).join('');
  html += `<input class="custom-tag-input" placeholder="+ custom" onkeydown="if(event.key==='Enter')addCustomTag('${{id}}',this)">`;
  row.innerHTML = html;
}}

function toggleTag(id, tag) {{
  if (!paperTags[id]) paperTags[id] = new Set();
  if (paperTags[id].has(tag)) paperTags[id].delete(tag);
  else paperTags[id].add(tag);
  if (triaged[id]) triaged[id].tags = Array.from(paperTags[id]);
  renderTagRow(id);
  persistTriage(id);
}}

function addCustomTag(id, input) {{
  const tag = input.value.trim();
  if (!tag) return;
  if (!paperTags[id]) paperTags[id] = new Set();
  paperTags[id].add(tag);
  if (triaged[id]) triaged[id].tags = Array.from(paperTags[id]);
  input.value = '';
  renderTagRow(id);
  persistTriage(id);
}}

// ── On page load: restore triage state ──
document.addEventListener('DOMContentLoaded', function() {{
  document.querySelectorAll('.paper-card').forEach(function(card) {{
    const id = card.id.replace('card-', '');
    const prev = getPersistedTriage(id);
    if (!prev) {{ renderTagRow(id); return; }}

    // Restore tags
    if (prev.tags && prev.tags.length) paperTags[id] = new Set(prev.tags);

    const status = document.getElementById('status-' + id);
    if (prev.action === 'add' || prev.action === 'star') {{
      card.className = 'paper-card triaged-' + prev.action;
      const btn = document.getElementById('btn-' + prev.action + '-' + id);
      if (btn) btn.classList.add('active');
      status.textContent = prev.action === 'star' ? '★ Starred' : '✓ Added';
      status.style.color = prev.action === 'star' ? 'var(--orange)' : 'var(--green)';
    }} else if (prev.action === 'ignore') {{
      card.className = 'paper-card triaged-ignore';
      const btn = document.getElementById('btn-ignore-' + id);
      if (btn) btn.classList.add('active');
      status.textContent = 'ignored';
      status.style.color = 'var(--red)';
    }}
    renderTagRow(id);
  }});
  updateCount();
}});

function toggleAbstract(id) {{
  const el = document.getElementById('abs-' + id);
  const btn = el.nextElementSibling;
  el.classList.toggle('expanded');
  btn.textContent = el.classList.contains('expanded') ? 'show less' : 'show more';
}}

function triagePaper(id, action, paperJson) {{
  const card = document.getElementById('card-' + id);
  const status = document.getElementById('status-' + id);

  // Clear previous active states
  ['add','star','ignore'].forEach(a => {{
    const b = document.getElementById('btn-' + a + '-' + id);
    if (b) b.classList.remove('active');
  }});

  // Toggle: clicking same action again deselects
  if (triaged[id] && triaged[id].action === action) {{
    delete triaged[id];
    card.className = 'paper-card';
    status.textContent = '';
    persistTriage(id);
    updateCount();
    return;
  }}

  const tags = Array.from(paperTags[id] || []);

  if (action === 'add') {{
    const data = JSON.parse(paperJson.replace(/&quot;/g, '"').replace(/&#39;/g, "'"));
    triaged[id] = {{ action: 'add', data: data, tags: tags, priority: 0 }};
    card.className = 'paper-card triaged-add';
    document.getElementById('btn-add-' + id).classList.add('active');
    status.textContent = '✓ Added';
    status.style.color = 'var(--green)';
  }} else if (action === 'star') {{
    const data = JSON.parse(paperJson.replace(/&quot;/g, '"').replace(/&#39;/g, "'"));
    triaged[id] = {{ action: 'star', data: data, tags: tags, priority: 3 }};
    card.className = 'paper-card triaged-star';
    document.getElementById('btn-star-' + id).classList.add('active');
    status.textContent = '★ Starred (high priority)';
    status.style.color = 'var(--orange)';
  }} else if (action === 'ignore') {{
    triaged[id] = {{ action: 'ignore', tags: tags, priority: -1 }};
    card.className = 'paper-card triaged-ignore';
    document.getElementById('btn-ignore-' + id).classList.add('active');
    status.textContent = 'ignored';
    status.style.color = 'var(--red)';
  }}
  persistTriage(id);
  updateCount();
}}

function updateCount() {{
  const adds = Object.values(triaged).filter(t => t.action === 'add' || t.action === 'star').length;
  const ignored = Object.values(triaged).filter(t => t.action === 'ignore').length;
  let txt = adds + ' to add';
  if (ignored) txt += ', ' + ignored + ' ignored';
  document.getElementById('triage-count').textContent = txt;
}}

function savePending() {{
  const toAdd = Object.entries(triaged)
    .filter(([_, t]) => t.action === 'add' || t.action === 'star')
    .map(([id, t]) => ({{ ...t.data, tags: t.tags, priority: t.priority }}));

  const toIgnore = Object.entries(triaged)
    .filter(([_, t]) => t.action === 'ignore')
    .map(([id, _]) => id.replace(/_/g, '.'));

  if (toAdd.length === 0 && toIgnore.length === 0) {{
    alert('No papers triaged yet. Use the buttons on each paper card.');
    return;
  }}

  const pending = {{
    digestDate: {json.dumps(digest_date)},
    savedAt: new Date().toISOString(),
    papers: toAdd,
    ignored: toIgnore
  }};

  const blob = new Blob([JSON.stringify(pending, null, 2)], {{ type: 'application/json' }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'pending_papers.json';
  a.click();
  URL.revokeObjectURL(a.href);

  alert('Saved ' + toAdd.length + ' paper(s) to add, ' + toIgnore.length + ' to ignore.\\n\\n' +
        'To import: open paper_database.html and click Import.');
}}
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  HTML digest: {output_path}")
    return output_path
