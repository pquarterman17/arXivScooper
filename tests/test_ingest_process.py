"""Plan #14 — end-to-end ingest pipeline tests for scq.ingest.process.

Drives ``main()`` against a fully-isolated tmp_path so the canonical .db,
references.bib/.txt, papers/, figures/, and inbox/ all live under the
sandbox. Asserts the database side-effects (papers, read_status, notes,
papers_fts, figures rows) and the citation-file appends match the
documented contract.

Strategy:
  - SCQ_REPO_ROOT=tmp_path + paths.refresh() so every path resolver in
    scq.config.paths returns under the sandbox.
  - Stub subprocess.run so the figure-extraction subprocess doesn't fork
    (we don't want pdfplumber/PyMuPDF in the test deps just for this).
  - Write a minimal meta JSON in inbox/<id>_meta.json + a fake PDF byte
    in papers/<file>.pdf so the file-existence checks pass.
  - Drive main() via sys.argv injection (the legacy CLI shim) — the
    same path scq.cli's `process` passthrough takes.

Companion to test_ingest_process_paths.py (which tests the lazy-path
fix in isolation) — together they cover plan #14's
"tests/test_ingest_process.py — fixture meta JSON + fake PDF, assert DB
rows" line item.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scq.config.paths import paths as resolve_paths
from scq.config.paths import refresh as refresh_paths
from scq.ingest import process as proc

# ─── Fixture: an isolated repo root with all required subdirs ─────


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Set SCQ_REPO_ROOT to a fresh tmp dir and create the sub-tree the
    pipeline expects.

    Yields the resolved :class:`scq.config.paths.Paths` so tests can
    assert against the same view ``process`` will see.
    """
    monkeypatch.setenv("SCQ_REPO_ROOT", str(tmp_path))
    refresh_paths()
    p = resolve_paths()

    # Pre-create directories — the pipeline assumes they exist.
    for d in (p.inbox_dir, p.papers_dir, p.figures_dir,
              p.exports_dir, p.references_bib_path.parent,
              p.references_txt_path.parent):
        Path(d).mkdir(parents=True, exist_ok=True)

    yield p

    refresh_paths()


@pytest.fixture
def stub_subprocess(monkeypatch):
    """Replace subprocess.run inside process.py with a no-op so figure
    extraction doesn't fork.

    Returns the call log so a test can assert what would have been run.
    """
    calls = []

    def fake_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        # Returncode 1 → process.extract_figures returns {} (no figures
        # inserted). That's exactly what we want for a hermetic test.
        return subprocess.CompletedProcess(
            args=args[0] if args else [], returncode=1,
            stdout="", stderr="extract stub: no real PDF",
        )

    monkeypatch.setattr(proc.subprocess, "run", fake_run)
    return calls


# ─── End-to-end: arxiv-id path ─────────────────────────────────────


def _write_meta(inbox_dir: Path, arxiv_id: str, **overrides) -> dict:
    """Build a canonical fetch_arxiv.js-style meta JSON in inbox/."""
    meta = {
        "arxiv_id": arxiv_id,
        "title": "Tantalum transmons with millisecond coherence",
        "authors": ["Alice Smith", "Bob Jones"],
        "abstract": "We demonstrate a tantalum-based transmon qubit with T1 "
                    "exceeding 500 μs. The improvement is attributed to "
                    "reduced surface oxide losses.",
        "categories": ["quant-ph", "cond-mat.supr-con"],
        "published": "2024-01-23T00:00:00Z",
        "pdf_file": f"{arxiv_id}_Smith_Tantalum.pdf",
    }
    meta.update(overrides)
    meta_path = inbox_dir / f"{arxiv_id}_meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return meta


def _write_fake_pdf(papers_dir: Path, pdf_file: str) -> Path:
    """Write a single byte to act as the on-disk PDF (existence-only check)."""
    pdf_path = papers_dir / pdf_file
    pdf_path.write_bytes(b"%PDF-1.4 fake\n")
    return pdf_path


def _drive_main(monkeypatch, *cli_args):
    """Set sys.argv and call proc.main() with the given args."""
    monkeypatch.setattr(sys, "argv", ["process", *cli_args])
    proc.main()


def test_arxiv_pipeline_inserts_paper_row_with_correct_metadata(
        isolated_repo, stub_subprocess, monkeypatch):
    arxiv_id = "2401.12345"
    meta = _write_meta(isolated_repo.inbox_dir, arxiv_id)
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id)

    conn = sqlite3.connect(str(isolated_repo.db_path))
    try:
        row = conn.execute(
            "SELECT id, title, authors, short_authors, year, doi, group_name, "
            "       cite_bib IS NOT NULL, cite_txt IS NOT NULL, pdf_path "
            "FROM papers WHERE id = ?",
            (arxiv_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "papers row was not inserted"
    (rid, title, authors, short_authors, year, doi,
     group_name, has_bib, has_txt, pdf_path) = row
    assert rid == arxiv_id
    assert title == meta["title"]
    assert authors == "Alice Smith, Bob Jones"
    assert short_authors == "Smith et al."
    assert year == 2024
    assert doi == f"10.48550/arXiv.{arxiv_id}"
    assert group_name == "", "group_name should start blank — Claude fills it later"
    assert has_bib == 1
    assert has_txt == 1
    assert pdf_path == f"papers/{meta['pdf_file']}"


def test_arxiv_pipeline_creates_unread_zero_priority_read_status(
        isolated_repo, stub_subprocess, monkeypatch):
    arxiv_id = "2401.99001"
    meta = _write_meta(isolated_repo.inbox_dir, arxiv_id)
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id)

    conn = sqlite3.connect(str(isolated_repo.db_path))
    try:
        row = conn.execute(
            "SELECT is_read, priority FROM read_status WHERE paper_id = ?",
            (arxiv_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row == (0, 0)


def test_arxiv_pipeline_inserts_note_when_provided(
        isolated_repo, stub_subprocess, monkeypatch):
    arxiv_id = "2401.99002"
    meta = _write_meta(isolated_repo.inbox_dir, arxiv_id)
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id, "--note", "interesting T1 results")

    conn = sqlite3.connect(str(isolated_repo.db_path))
    try:
        row = conn.execute(
            "SELECT content FROM notes WHERE paper_id = ?",
            (arxiv_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "interesting T1 results"


def test_arxiv_pipeline_does_not_create_note_row_when_omitted(
        isolated_repo, stub_subprocess, monkeypatch):
    arxiv_id = "2401.99003"
    meta = _write_meta(isolated_repo.inbox_dir, arxiv_id)
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id)  # no --note

    conn = sqlite3.connect(str(isolated_repo.db_path))
    try:
        row = conn.execute(
            "SELECT content FROM notes WHERE paper_id = ?",
            (arxiv_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_arxiv_pipeline_indexes_into_papers_fts(
        isolated_repo, stub_subprocess, monkeypatch):
    arxiv_id = "2401.99004"
    meta = _write_meta(
        isolated_repo.inbox_dir, arxiv_id,
        title="Niobium qubits with reduced flux noise",
        abstract="distinctive marker XYZ-99 for FTS lookup",
    )
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id)

    conn = sqlite3.connect(str(isolated_repo.db_path))
    try:
        # FTS5 MATCH should hit the abstract marker
        rows = conn.execute(
            "SELECT id FROM papers_fts WHERE papers_fts MATCH ?",
            ('"XYZ-99"',),
        ).fetchall()
    finally:
        conn.close()
    assert any(r[0] == arxiv_id for r in rows), \
        "abstract content not in FTS index"


def test_arxiv_pipeline_auto_tags_from_categories_and_keywords(
        isolated_repo, stub_subprocess, monkeypatch):
    arxiv_id = "2401.99005"
    meta = _write_meta(
        isolated_repo.inbox_dir, arxiv_id,
        # Categories should produce: superconductivity, quantum computing
        categories=["quant-ph", "cond-mat.supr-con"],
        # Title contains "transmon" + "tantalum" + "qubit" — should produce
        # those tags via the keyword regex pass.
        title="Tantalum transmons with high-Q resonators",
    )
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id)

    conn = sqlite3.connect(str(isolated_repo.db_path))
    try:
        row = conn.execute(
            "SELECT tags FROM papers WHERE id = ?",
            (arxiv_id,),
        ).fetchone()
    finally:
        conn.close()

    tags = json.loads(row[0])
    # Category-driven
    assert "superconductivity" in tags
    assert "quantum computing" in tags
    # Keyword-driven (from title scan)
    assert "transmon" in tags
    assert "tantalum" in tags


def test_arxiv_pipeline_appends_to_references_bib_and_txt(
        isolated_repo, stub_subprocess, monkeypatch):
    arxiv_id = "2401.99006"
    meta = _write_meta(isolated_repo.inbox_dir, arxiv_id)
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id)

    bib = isolated_repo.references_bib_path.read_text(encoding="utf-8")
    txt = isolated_repo.references_txt_path.read_text(encoding="utf-8")

    assert arxiv_id in bib, "arXiv id not present in references.bib"
    assert "@article{smith2024tantalum" in bib, "BibTeX key missing"
    assert arxiv_id in txt, "arXiv id not present in references.txt"
    # First entry → numbered [1]
    assert txt.lstrip().startswith("[1]")


def test_arxiv_pipeline_skips_duplicate_bib_entry_on_second_run(
        isolated_repo, stub_subprocess, monkeypatch):
    """Re-processing the same paper is idempotent on the citation files."""
    arxiv_id = "2401.99007"
    meta = _write_meta(isolated_repo.inbox_dir, arxiv_id)
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id)
    bib_first = isolated_repo.references_bib_path.read_text(encoding="utf-8")
    txt_first = isolated_repo.references_txt_path.read_text(encoding="utf-8")

    # Re-run — should detect arxiv_id present in both files and skip
    _drive_main(monkeypatch, arxiv_id)

    bib_second = isolated_repo.references_bib_path.read_text(encoding="utf-8")
    txt_second = isolated_repo.references_txt_path.read_text(encoding="utf-8")

    assert bib_first == bib_second, "references.bib was double-appended"
    assert txt_first == txt_second, "references.txt was double-appended"


def test_arxiv_pipeline_no_figures_when_extract_subprocess_fails(
        isolated_repo, stub_subprocess, monkeypatch):
    """The stub returncode=1 path must not crash and must produce zero figs."""
    arxiv_id = "2401.99008"
    meta = _write_meta(isolated_repo.inbox_dir, arxiv_id)
    _write_fake_pdf(isolated_repo.papers_dir, meta["pdf_file"])

    _drive_main(monkeypatch, arxiv_id)

    conn = sqlite3.connect(str(isolated_repo.db_path))
    try:
        n_figs = conn.execute(
            "SELECT COUNT(*) FROM figures WHERE paper_id = ?",
            (arxiv_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_figs == 0


def test_arxiv_pipeline_resolves_pdf_by_arxiv_id_prefix_when_pdf_file_missing(
        isolated_repo, stub_subprocess, monkeypatch):
    """If meta['pdf_file'] doesn't exist but a glob match does, that wins."""
    arxiv_id = "2401.99009"
    _write_meta(
        isolated_repo.inbox_dir, arxiv_id,
        pdf_file="not-this-name.pdf",  # intentionally points at nothing
    )
    # Create a different file under the prefix that the glob will catch
    actual = _write_fake_pdf(isolated_repo.papers_dir, f"{arxiv_id}_recovery.pdf")
    assert actual.exists()

    _drive_main(monkeypatch, arxiv_id)

    conn = sqlite3.connect(str(isolated_repo.db_path))
    try:
        row = conn.execute(
            "SELECT pdf_path FROM papers WHERE id = ?", (arxiv_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == f"papers/{actual.name}"


# ─── Failure paths ─────────────────────────────────────────────────


def test_arxiv_pipeline_exits_when_meta_json_missing(
        isolated_repo, stub_subprocess, monkeypatch, capsys):
    """No inbox/<id>_meta.json → SystemExit(1) with a helpful message."""
    arxiv_id = "9999.00000"
    # Don't write meta or pdf
    monkeypatch.setattr(sys, "argv", ["process", arxiv_id])
    with pytest.raises(SystemExit) as exc:
        proc.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "_meta.json not found" in out


def test_arxiv_pipeline_exits_when_pdf_missing(
        isolated_repo, stub_subprocess, monkeypatch, capsys):
    """meta exists but no pdf at the named path nor any glob match → exit 1."""
    arxiv_id = "9999.00001"
    _write_meta(isolated_repo.inbox_dir, arxiv_id)
    # Don't write any pdf

    monkeypatch.setattr(sys, "argv", ["process", arxiv_id])
    with pytest.raises(SystemExit) as exc:
        proc.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "PDF not found" in out


def test_arxiv_pipeline_usage_when_no_argv(monkeypatch, capsys):
    """`scq process` (no args) → SystemExit(1) + usage message."""
    monkeypatch.setattr(sys, "argv", ["process"])
    with pytest.raises(SystemExit) as exc:
        proc.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Usage" in out
