"""Cross-run dedup state for the arXiv digest.

The digest runs on GitHub Actions with a fresh checkout each time and no
access to the user's (OneDrive-hosted, gitignored) SQLite database, so it
cannot remember what it already emailed by reading the paper library.

This module persists a small ``data/digest_state.json`` *inside the repo*
that the digest workflow commits back after each successful run. Because
the file is committed, the next scheduled run checks it out and knows
which arXiv IDs have already gone out — which lets us use a wide,
overlapping lookback window (so a failed/empty day is always recovered)
without re-emailing the same papers.

State shape::

    {"2401.00001": "2026-05-29", "2401.00002": "2026-05-30", ...}

i.e. ``{arxiv_id: iso_date_first_sent}``. Entries older than
``KEEP_DAYS`` are pruned on each save so the file stays small and
bounded.

Pure I/O + dict manipulation — no network, no DOM. The date-dependent
helpers take the reference date as an argument so they stay deterministic
under test.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

# How long a sent arXiv ID is remembered. Must comfortably exceed the
# digest's lookback window so a paper at the edge of the window is never
# re-sent. With a 7-day overlapping window, 60 days is generous.
KEEP_DAYS = 60

# Default location: the committed state file in the repo checkout. The
# digest workflow runs from the repo root, so the cwd-relative path
# resolves to the checked-out (and re-committed) file. ``pip install``
# puts ``scq`` in site-packages, so a ``__file__``-relative path would
# point at the wrong tree on CI — cwd is the reliable anchor. Override
# with ``SCQ_DIGEST_STATE_PATH`` for tests or non-standard layouts.
def state_path() -> Path:
    """Return the path to the committed digest-state JSON file."""
    override = os.environ.get("SCQ_DIGEST_STATE_PATH")
    if override:
        return Path(override)
    return Path.cwd() / "data" / "digest_state.json"


def load_sent_ids(path: Path | None = None) -> dict[str, str]:
    """Return ``{arxiv_id: iso_date}`` of already-emailed papers.

    A missing or unreadable file yields an empty dict — never raises — so
    a fresh checkout (or a corrupted state file) degrades to "remember
    nothing" rather than crashing the digest. The wide lookback window
    means the worst case of a lost state file is one digest with repeats,
    not lost papers.
    """
    p = path or state_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    # Defensive: only keep str->str pairs; ignore anything malformed.
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def filter_unsent(papers, sent, *, id_key: str = "id"):
    """Return the subset of ``papers`` whose IDs are not in ``sent``.

    ``papers`` is a list of dicts (the digest's paper shape). Order is
    preserved. Papers missing the id key are kept (fail-open — better to
    risk a duplicate than to silently drop a paper).
    """
    return [p for p in papers if p.get(id_key) not in sent]


def record_sent(papers, sent, *, date_str: str, id_key: str = "id") -> dict[str, str]:
    """Add each paper's ID to ``sent`` (in place) stamped with ``date_str``.

    Existing entries are left untouched so an ID keeps its *first*-sent
    date. Returns the same ``sent`` dict for chaining.
    """
    for p in papers:
        pid = p.get(id_key)
        if pid and pid not in sent:
            sent[pid] = date_str
    return sent


def prune(sent, *, keep_days: int = KEEP_DAYS, today: date | None = None) -> dict[str, str]:
    """Drop entries older than ``keep_days`` (in place). Returns ``sent``.

    ``today`` defaults to the current UTC date. Entries with an
    unparseable date are kept (fail-open) so a hand-edit can't silently
    erase history.
    """
    ref = today or datetime.utcnow().date()
    horizon = ref - timedelta(days=keep_days)
    stale = []
    for pid, iso in sent.items():
        try:
            d = date.fromisoformat(iso)
        except (ValueError, TypeError):
            continue
        if d < horizon:
            stale.append(pid)
    for pid in stale:
        del sent[pid]
    return sent


def save_sent_ids(sent, path: Path | None = None) -> Path:
    """Write ``sent`` to disk as sorted, pretty JSON. Returns the path.

    Sorted keys keep the committed diff minimal and review-friendly. The
    parent directory is created if missing.
    """
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(sent, f, indent=2, sort_keys=True)
        f.write("\n")
    return p
