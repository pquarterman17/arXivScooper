"""
Full step-2 pipeline for adding an arXiv paper to the database.

Reads the ``_meta.json`` produced by ``fetch_arxiv.js``, then:
  1. Extracts figures + captions from the PDF (via ``scq.ingest.extract``)
  2. Generates BibTeX and plain-text citations
  3. Inserts into the SQLite database (paper, figures, read_status, FTS)
  4. Appends to ``references.bib`` and ``references.txt``

Module entry point: ``python -m scq.ingest.process <arxiv_id> [--note "..."]``
or via the CLI: ``scq process <arxiv_id>``. The legacy
``python tools/process_paper.py ...`` invocation is preserved by a thin shim
in ``tools/process_paper.py``.

Expects:
  ``<inbox_dir>/<arxiv_id>_meta.json``   (from fetch_arxiv.js)
  ``<papers_dir>/<pdf_file>``            (downloaded by fetch_arxiv.js)

All filesystem locations resolve through :func:`scq.config.paths.paths` so
``data/user_config/paths.toml`` overrides take effect transparently.
"""

import json
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────
# This module lives at scq/ingest/process.py. The scq package is normally
# imported via the canonical pip-install path; running from a fresh
# checkout (`python -m scq.ingest.process`) also works because the repo
# root is implicitly on sys.path. The PROJECT_DIR fallback is only used
# for the legacy ``tools/process_paper.py`` shim invocation.
_HERE = Path(__file__).resolve()
PROJECT_DIR = _HERE.parents[2]  # scq/ingest/process.py → repo root
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from scq.config.paths import paths as _scq_paths  # noqa: E402


# Resolve paths lazily on each access so paths.refresh() / SCQ_REPO_ROOT
# overrides take effect mid-process (e.g. integration tests using tmp_path).
# Capturing them at module load was B4 from the 2026-04-30 audit.
def _inbox_dir() -> Path:
    return Path(_scq_paths().inbox_dir)


def _papers_dir() -> Path:
    return Path(_scq_paths().papers_dir)


def _figures_dir() -> Path:
    return Path(_scq_paths().figures_dir)


def _db_path() -> Path:
    return Path(_scq_paths().db_path)


def _bib_path() -> Path:
    return Path(_scq_paths().references_bib_path)


def _txt_path() -> Path:
    return Path(_scq_paths().references_txt_path)


# Figure extraction lives at scq.ingest.extract; invoke via the `-m` runner
# so the same path works in pip-installed and source-checkout setups.
EXTRACT_CMD = [sys.executable, "-m", "scq.ingest.extract"]


# ─── Citation generators ──────────────────────────────────────────


def make_bibtex(meta):
    """Generate a BibTeX entry from metadata."""
    authors = meta["authors"]
    first_last = authors[0].split()[-1].lower()
    title_word = re.sub(r"[^a-z]", "", meta["title"].split()[0].lower())
    key = f"{first_last}{meta['published'][:4]}{title_word}"

    # Format author list: "Last, First and Last, First"
    bib_authors = []
    for a in authors:
        parts = a.strip().split()
        if len(parts) >= 2:
            bib_authors.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        else:
            bib_authors.append(a)

    cat = meta["categories"][0] if meta.get("categories") else ""
    year = meta["published"][:4]
    arxiv_id = meta["arxiv_id"]

    bib = f"""@article{{{key},
  title     = {{{meta["title"]}}},
  author    = {{{" and ".join(bib_authors)}}},
  journal   = {{arXiv preprint}},
  volume    = {{}},
  pages     = {{}},
  year      = {{{year}}},
  doi       = {{10.48550/arXiv.{arxiv_id}}},
  note      = {{arXiv:{arxiv_id} [{cat}]}}
}}"""
    return key, bib


def make_plain_cite(meta):
    """Generate a plain-text citation in Physical Review style."""
    authors = meta["authors"]
    year = meta["published"][:4]
    arxiv_id = meta["arxiv_id"]
    cat = meta["categories"][0] if meta.get("categories") else ""

    # Format: F. M. Last
    formatted = []
    for a in authors:
        parts = a.strip().split()
        if len(parts) >= 2:
            initials = " ".join(p[0] + "." for p in parts[:-1])
            formatted.append(f"{initials} {parts[-1]}")
        else:
            formatted.append(a)

    if len(formatted) > 2:
        author_str = ", ".join(formatted[:-1]) + ", and " + formatted[-1]
    elif len(formatted) == 2:
        author_str = " and ".join(formatted)
    else:
        author_str = formatted[0] if formatted else "Unknown"

    return f'{author_str}, "{meta["title"]}," arXiv:{arxiv_id} [{cat}] ({year}).'


def short_author(authors):
    """Generate short author reference like 'Pitton et al.'"""
    if not authors:
        return "Unknown"
    last = authors[0].split()[-1]
    return f"{last} et al." if len(authors) > 1 else last


# ─── CrossRef DOI lookup ──────────────────────────────────────────


def lookup_doi(doi):
    """Fetch metadata from CrossRef API for a given DOI.
    Returns dict with: title, authors, short_authors, year, journal,
    volume, pages, doi, cite_bib, cite_txt
    Returns None on network error or invalid DOI."""
    try:
        import json
        import urllib.error
        import urllib.request

        url = f"https://api.crossref.org/works/{doi}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SCQDatabase/1.0 (+https://github.com/pquarterman17/arXivScooper)",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not data.get("message"):
            return None

        msg = data["message"]
        title = (
            msg.get("title", [""])[0]
            if isinstance(msg.get("title"), list)
            else msg.get("title", "")
        )
        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip() for a in msg.get("author", [])
        ]
        year = msg.get("published", {}).get("date-parts", [[None]])[0][0] or date.today().year
        journal = (
            msg.get("container-title", [""])[0]
            if isinstance(msg.get("container-title"), list)
            else msg.get("container-title", "")
        )
        volume = msg.get("volume", "")
        pages = msg.get("page", "")

        # Generate citations
        cite_bib_key, cite_bib = _make_doi_bibtex(doi, title, authors, year, journal, volume, pages)
        cite_txt = _make_doi_plain_cite(authors, title, journal, volume, pages, year, doi)

        return {
            "title": title,
            "authors": authors,
            "short_authors": short_author(authors),
            "year": year,
            "journal": journal,
            "volume": volume,
            "pages": pages,
            "doi": doi,
            "cite_bib": cite_bib,
            "cite_txt": cite_txt,
        }
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        print(f"  [warn] DOI lookup failed for {doi}: {e}")
        return None
    except Exception as e:
        print(f"  [warn] Unexpected error in DOI lookup: {e}")
        return None


def _make_doi_bibtex(doi, title, authors, year, journal, volume, pages):
    """Generate BibTeX entry from CrossRef metadata."""
    first_last = authors[0].split()[-1].lower() if authors else "unknown"
    title_word = re.sub(r"[^a-z]", "", title.split()[0].lower() if title else "article")
    key = f"{first_last}{year}{title_word}"

    # Format author list: "Last, First and Last, First"
    bib_authors = []
    for a in authors:
        parts = a.strip().split()
        if len(parts) >= 2:
            bib_authors.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        else:
            bib_authors.append(a)

    bib = f"""@article{{{key},
  title     = {{{title}}},
  author    = {{{" and ".join(bib_authors)}}},
  journal   = {{{journal}}},
  volume    = {{{volume}}},
  pages     = {{{pages}}},
  year      = {{{year}}},
  doi       = {{{doi}}}
}}"""
    return key, bib


def _make_doi_plain_cite(authors, title, journal, volume, pages, year, doi):
    """Generate plain-text citation in Physical Review style from CrossRef data."""
    # Format: F. M. Last
    formatted = []
    for a in authors:
        parts = a.strip().split()
        if len(parts) >= 2:
            initials = " ".join(p[0] + "." for p in parts[:-1])
            formatted.append(f"{initials} {parts[-1]}")
        else:
            formatted.append(a)

    if len(formatted) > 2:
        author_str = ", ".join(formatted[:-1]) + ", and " + formatted[-1]
    elif len(formatted) == 2:
        author_str = " and ".join(formatted)
    else:
        author_str = formatted[0] if formatted else "Unknown"

    pages_str = f", {pages}" if pages else ""
    return f'{author_str}, "{title}," {journal} {volume}{pages_str} ({year}). https://doi.org/{doi}'


# ─── Figure extraction ────────────────────────────────────────────


def extract_figures(pdf_path, arxiv_id, prefix):
    """Run extract_figures.py and return the captions dict."""
    fig_dir = _figures_dir() / arxiv_id
    fig_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        EXTRACT_CMD + [str(pdf_path), str(fig_dir), "--prefix", prefix],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"  [warn] Figure extraction failed: {result.stderr[:300]}")
        return {}

    captions_file = fig_dir / "captions.json"
    if captions_file.exists():
        with open(captions_file) as f:
            return json.load(f)
    return {}


# ─── Database operations ──────────────────────────────────────────


def load_db():
    """Open the canonical SQLite DB at data/arxiv_scooper.db, applying migrations."""
    from scq.db.migrations import apply_pending

    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending(conn)
    return conn


def insert_paper(
    conn,
    meta,
    summary,
    key_results_list,
    tags_list,
    group_name,
    bib,
    plain_cite,
    pdf_rel_path,
    note,
):
    """Insert or update a paper in all relevant tables."""
    cur = conn.cursor()
    arxiv_id = meta["arxiv_id"]
    year = int(meta["published"][:4])

    cur.execute(
        """
        INSERT OR REPLACE INTO papers
        (id, title, authors, short_authors, year, journal, volume, pages,
         doi, arxiv_id, url, group_name, date_added, tags, summary,
         key_results, cite_bib, cite_txt, pdf_path)
        VALUES (?,?,?,?,?, '','','', ?,?, ?,?,?, ?,?, ?,?,?,?)
    """,
        (
            arxiv_id,
            meta["title"],
            ", ".join(meta["authors"]),
            short_author(meta["authors"]),
            year,
            f"10.48550/arXiv.{arxiv_id}",
            arxiv_id,
            f"https://arxiv.org/abs/{arxiv_id}",
            group_name,
            date.today().isoformat(),
            json.dumps(tags_list),
            summary,
            json.dumps(key_results_list),
            bib,
            plain_cite,
            pdf_rel_path,
        ),
    )

    # Read status (unread, no priority)
    cur.execute(
        """
        INSERT OR IGNORE INTO read_status (paper_id, is_read, priority)
        VALUES (?, 0, 0)
    """,
        (arxiv_id,),
    )

    # Note
    if note:
        cur.execute(
            """
            INSERT OR REPLACE INTO notes (paper_id, content, last_edited)
            VALUES (?, ?, ?)
        """,
            (arxiv_id, note, datetime.now().isoformat()),
        )

    # FTS index — delete old entry first to avoid duplicates
    try:
        cur.execute("DELETE FROM papers_fts WHERE id = ?", (arxiv_id,))
    except Exception:
        pass
    cur.execute(
        """
        INSERT INTO papers_fts (id, title, authors, summary, tags, key_results)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (
            arxiv_id,
            meta["title"],
            ", ".join(meta["authors"]),
            summary,
            json.dumps(tags_list),
            json.dumps(key_results_list),
        ),
    )

    conn.commit()


def insert_figures(conn, arxiv_id, figures, prefix):
    """Insert figure entries into the database."""
    cur = conn.cursor()
    for fig_key, fig_data in sorted(figures.items()):
        fig_num = int(re.search(r"\d+", fig_key).group())
        cur.execute(
            """
            INSERT OR REPLACE INTO figures
            (paper_id, figure_key, file_path, label, caption, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                arxiv_id,
                f"{prefix}_{fig_key}",
                f"figures/{arxiv_id}/{fig_data['file']}",
                f"Fig. {fig_num}",
                fig_data["caption"],
                fig_num - 1,
            ),
        )
    conn.commit()


# ─── Citation file updates ────────────────────────────────────────


def append_bib(bib_entry, arxiv_id):
    """Append a BibTeX entry to references.bib if not already present."""
    bib_path = _bib_path()
    existing = bib_path.read_text(encoding="utf-8") if bib_path.exists() else ""
    if arxiv_id in existing:
        print("  references.bib: already contains this entry")
        return
    with open(bib_path, "a", encoding="utf-8") as f:
        f.write("\n" + bib_entry + "\n")
    print("  references.bib: appended")


def append_txt(plain_cite, arxiv_id):
    """Append a plain-text citation to references.txt."""
    txt_path = _txt_path()
    existing = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
    if arxiv_id in existing:
        print("  references.txt: already contains this entry")
        return
    # Find next reference number
    nums = re.findall(r"^\[(\d+)\]", existing, re.MULTILINE)
    next_num = max(int(n) for n in nums) + 1 if nums else 1
    with open(txt_path, "a", encoding="utf-8") as f:
        f.write(f"\n[{next_num}] {plain_cite}\n")
    print(f"  references.txt: appended as [{next_num}]")


# ─── DOI processing ───────────────────────────────────────────────


def _process_doi(doi, note):
    """Process a paper using CrossRef metadata from a DOI."""
    print(f"\n{'=' * 60}")
    print(f"Fetching from CrossRef: {doi}")
    print(f"{'=' * 60}")

    # Fetch metadata
    print("\n[1/3] Looking up DOI metadata...")
    meta = lookup_doi(doi)
    if not meta:
        print("ERROR: Could not fetch DOI metadata")
        sys.exit(1)

    print(f"  Title: {meta['title']}")
    print(f"  Authors: {meta['short_authors']}")
    print(f"  Year: {meta['year']}")

    # Load or create database
    conn = load_db()

    # Generate entry ID from DOI
    entry_id = doi.replace("/", "-").replace(".", "_")[:50]

    # Build tags from title/journal
    tags = []
    text_to_scan = f"{meta['title']} {meta['journal']}"
    kw_tags = {
        r"josephson": "Josephson junctions",
        r"transmon": "transmon",
        r"qubit": "qubits",
        r"resonator": "resonators",
        r"tantalum|\\bTa\\b": "tantalum",
        r"niobium|\\bNb\\b": "niobium",
        r"loss|quality factor|Q\s*factor": "loss/coherence",
        r"diode": "Josephson diode",
        r"review": "review",
        r"kinetic inductance": "kinetic inductance",
        r"two.level system|TLS": "TLS",
        r"surface": "surface losses",
    }
    for pattern, tag in kw_tags.items():
        if re.search(pattern, text_to_scan, re.IGNORECASE) and tag not in tags:
            tags.append(tag)

    # Insert into database
    print("\n[2/3] Inserting into database...")
    pdf_rel = ""  # No PDF for published papers (unless downloaded separately)
    insert_paper(
        conn,
        {
            "title": meta["title"],
            "authors": meta["authors"],
            "published": f"{meta['year']}-01-01",
            "arxiv_id": "",
            "categories": [],
            "abstract": meta["journal"],
        },
        meta["title"],
        [],
        tags,
        "",
        meta["cite_bib"],
        meta["cite_txt"],
        pdf_rel,
        note,
    )

    # Update the paper entry with published metadata
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE papers
        SET journal = ?, volume = ?, pages = ?, doi = ?, entry_type = 'published'
        WHERE id = ?
    """,
        (meta["journal"], meta["volume"], meta["pages"], meta["doi"], entry_id),
    )
    conn.commit()
    conn.close()

    print("  Paper inserted with entry_type='published'")

    # 3. Update citation files
    print("\n[3/3] Updating citation files...")
    append_bib(meta["cite_bib"], entry_id)
    append_txt(meta["cite_txt"], entry_id)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"DONE — {doi} added to database")
    print(f"  Title:   {meta['title']}")
    print(f"  Authors: {meta['short_authors']}")
    print(f"  Journal: {meta['journal']}")
    print(f"  Tags:    {tags}")
    if note:
        print(f"  Note:    {note}")
    print(f"{'=' * 60}")


# ─── Main ─────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 tools/process_paper.py <arxiv_id> [--note "..."]')
        print('       python3 tools/process_paper.py --doi <doi> [--note "..."]')
        print(
            "       Reads inbox/<arxiv_id>_meta.json (from fetch_arxiv.js) or fetches from CrossRef"
        )
        sys.exit(1)

    note = ""
    if "--note" in sys.argv:
        idx = sys.argv.index("--note")
        if idx + 1 < len(sys.argv):
            note = sys.argv[idx + 1]

    # Check if processing a DOI instead of arXiv ID
    if sys.argv[1] == "--doi":
        if len(sys.argv) < 3:
            print("ERROR: --doi requires a DOI argument")
            sys.exit(1)
        doi = sys.argv[2]
        _process_doi(doi, note)
        return

    arxiv_id = sys.argv[1]

    # Load metadata
    meta_path = _inbox_dir() / f"{arxiv_id}_meta.json"
    if not meta_path.exists():
        print(f"ERROR: {meta_path} not found.")
        print(f"Run fetch_arxiv.js first: node tools/fetch_arxiv.js {arxiv_id}")
        sys.exit(1)

    with open(meta_path) as f:
        meta = json.load(f)

    print(f"\n{'=' * 60}")
    print(f"Processing: {meta['title']}")
    print(f"  {short_author(meta['authors'])} ({meta['published'][:4]})")
    print(f"{'=' * 60}")

    # Find the PDF
    pdf_file = meta.get("pdf_file", "")
    papers_dir = _papers_dir()
    pdf_path = papers_dir / pdf_file
    if not pdf_path.exists():
        # Try to find it by arxiv_id prefix
        matches = list(papers_dir.glob(f"{arxiv_id}*"))
        if matches:
            pdf_path = matches[0]
            pdf_file = pdf_path.name
        else:
            print(f"ERROR: PDF not found: {pdf_file}")
            sys.exit(1)

    # 1. Extract figures
    print(f"\n[1/4] Extracting figures from {pdf_file}...")
    prefix = meta["authors"][0].split()[-1].lower() if meta["authors"] else "fig"
    figures = extract_figures(pdf_path, arxiv_id, prefix)
    print(f"  Found {len(figures)} figures")

    # 2. Generate citations
    print("\n[2/4] Generating citations...")
    bib_key, bib_entry = make_bibtex(meta)
    plain_cite = make_plain_cite(meta)
    print(f"  BibTeX key: {bib_key}")

    # 3. Build summary and tags from abstract
    # (These are placeholders — Claude will enrich them interactively)
    abstract = meta.get("abstract", "")
    summary = abstract[:500] if abstract else "Summary pending."
    key_results = []
    tags = []

    # Auto-tag from categories
    cat_tags = {
        "cond-mat.supr-con": "superconductivity",
        "cond-mat.mtrl-sci": "materials",
        "cond-mat.mes-hall": "mesoscopic",
        "quant-ph": "quantum computing",
        "cond-mat.str-el": "strongly correlated",
    }
    for cat in meta.get("categories", []):
        if cat in cat_tags:
            tags.append(cat_tags[cat])

    # Auto-detect common keywords in title/abstract
    kw_tags = {
        r"josephson": "Josephson junctions",
        r"transmon": "transmon",
        r"qubit": "qubits",
        r"resonator": "resonators",
        r"tantalum|\\bTa\\b": "tantalum",
        r"niobium|\\bNb\\b": "niobium",
        r"loss|quality factor|Q\s*factor": "loss/coherence",
        r"diode": "Josephson diode",
        r"review": "review",
        r"kinetic inductance": "kinetic inductance",
        r"two.level system|TLS": "TLS",
        r"surface": "surface losses",
    }
    text_to_scan = f"{meta['title']} {abstract}"
    for pattern, tag in kw_tags.items():
        if re.search(pattern, text_to_scan, re.IGNORECASE) and tag not in tags:
            tags.append(tag)

    group_name = ""  # Claude fills this in interactively

    # 4. Insert into database
    print("\n[3/4] Inserting into database...")
    conn = load_db()
    pdf_rel = f"papers/{pdf_file}"
    insert_paper(
        conn, meta, summary, key_results, tags, group_name, bib_entry, plain_cite, pdf_rel, note
    )
    if figures:
        insert_figures(conn, arxiv_id, figures, prefix)
    conn.commit()
    conn.close()
    print(f"  Paper + {len(figures)} figures + read status inserted")

    # 5. Update citation files
    print("\n[4/4] Updating citation files...")
    append_bib(bib_entry, arxiv_id)
    append_txt(plain_cite, arxiv_id)

    # 7. Auto-sync to Overleaf if configured
    overleaf_config_path = PROJECT_DIR / ".overleaf" / "config.json"
    if overleaf_config_path.exists():
        try:
            with open(overleaf_config_path) as f:
                overleaf_config = json.load(f)
            if overleaf_config.get("auto_sync", False):
                print("\n[5/4] Auto-syncing to Overleaf...")
                # Overleaf sync now lives at scq.overleaf.sync (#12 wave 2).
                # Use the `-m` runner so any environment with the scq package
                # installed works regardless of cwd.
                result = subprocess.run(
                    [sys.executable, "-m", "scq.overleaf.sync"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode == 0:
                    print("  Overleaf sync successful")
                else:
                    print(
                        "  Warning: Overleaf sync failed (run manually: python tools/overleaf_sync.py)"
                    )
                    if result.stderr:
                        print(f"  Error: {result.stderr[:200]}")
        except Exception as e:
            print(f"  Warning: Could not auto-sync to Overleaf: {e}")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"DONE — {arxiv_id} added to database")
    print(f"  Title:    {meta['title']}")
    print(f"  Authors:  {short_author(meta['authors'])}")
    print(f"  Figures:  {len(figures)}")
    print(f"  Tags:     {tags}")
    if note:
        print(f"  Note:     {note}")
    print("\nTo enrich (summary, key results, group name), ask Claude.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
