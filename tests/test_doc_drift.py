"""MT-6 — Doc-drift detection.

After a refactor retires a symbol (renamed file, deleted module, deprecated
config key), human-facing markdown docs often keep referencing it. Users
following copy/pasted commands hit "file not found" or worse.

This test greps every tracked ``*.md`` file for symbols that have been
formally retired and fails the build if any *active instructional* hit
shows up. Historical mentions phrased as ``... was retired in commit ...``
are explicitly allowed — they help future Claude sessions disambiguate
when they remember the old pattern.

Add a row to ``RETIRED_SYMBOLS`` whenever a refactor removes something
users would have copy-pasted from docs. Each entry is the literal symbol
string + the commit/date when it was retired (for the failure message).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# Each entry: (literal_symbol, deprecation_note_for_failure_message)
RETIRED_SYMBOLS = [
    ("scq_data.js",              "retired 2026-04-28 (commit c3694d1); use data/arxiv_scooper.db"),
    ("SCQ_DB_BASE64",            "retired 2026-04-28; the base64 bootstrap is gone, use sqlite3.connect(DB)"),
    ("/.scq_tmp.db",             "retired 2026-04-28; the canonical .db replaces the tmp roundtrip"),
    ("email_recipients.json",    "retired 2026-04-28 (commit 7089d55); use data/user_config/digest.json"),
    ("scq_papers.db",            "retired 2026-05-01 (commit c9ed78b); renamed to arxiv_scooper.db"),
    ("scientific_litter_scoop.db", "retired 2026-05-03; renamed to arxiv_scooper.db"),
    ("serve.py",                 "renamed 2026-05-03; moved to scq/server.py — invoke via `python -m scq serve`"),
    ("arxiv_poop_scooper.db",    "retired 2026-05-21; renamed to arxiv_scooper.db"),
    ("arXivPoopScooper",         "retired 2026-05-21; repo renamed to arXivScooper"),
]


# Lines matching any of these patterns are *historical* mentions and don't
# count as drift. Keep narrow — too-generous patterns mask real drift.
HISTORICAL_PATTERNS = [
    re.compile(r"\bwas retired\b", re.IGNORECASE),
    re.compile(r"\bretired (?:on |in )?\d{4}-\d{2}-\d{2}", re.IGNORECASE),
    re.compile(r"\bretired in commit\b", re.IGNORECASE),
    re.compile(r"\b(?:legacy|deprecated|pre-rename|formerly|previously known as)\b", re.IGNORECASE),
    re.compile(r"\brenamed (?:from|to)\b", re.IGNORECASE),
    # `.gitignore` entries that mention a retired filename are documentation
    # of "still ignored so old working copies can stick around" — not drift.
    re.compile(r"\bignore[d]?\b", re.IGNORECASE),
    # Memory/note files referencing a renamed file as historical context
    re.compile(r"\bsupersed(?:e|ed|es)\b", re.IGNORECASE),
]


def _is_historical(line: str) -> bool:
    return any(p.search(line) for p in HISTORICAL_PATTERNS)


def _markdown_files() -> list[Path]:
    """All git-tracked .md files in the repo.

    Uses ``git ls-files`` so gitignored paths (``plans/``, OneDrive
    junctioned content, archived audits) are skipped automatically. This
    keeps the doc-drift test scoped to user-facing committed docs.
    """
    import subprocess
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line]


@pytest.mark.parametrize("symbol,deprecation_note", RETIRED_SYMBOLS)
def test_retired_symbol_not_referenced_in_active_docs(symbol, deprecation_note):
    """Each retired symbol must only appear in historical contexts."""
    offending = []
    for md in _markdown_files():
        try:
            text = md.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if symbol in line and not _is_historical(line):
                rel = md.relative_to(REPO_ROOT)
                offending.append(f"  {rel}:{lineno}: {line.strip()[:120]}")
    assert not offending, (
        f"\n\nDrift: '{symbol}' appears in active doc text "
        f"({deprecation_note}).\n\n"
        f"If the line is genuinely historical (\"was retired in commit X\"),\n"
        f"phrase it so HISTORICAL_PATTERNS in tests/test_doc_drift.py picks\n"
        f"it up. Otherwise update the doc to use the current symbol.\n\n"
        f"Offending lines ({len(offending)}):\n" + "\n".join(offending)
    )
