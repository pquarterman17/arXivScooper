"""Database path resolution and connection helpers.

Single source of truth for "where does the SCQ paper database live?". Used
by every Python tool that touches the DB so they all agree on the path,
and so that swapping the location (env var, OneDrive, alternate test DB)
is a one-place change.

Until plan item #5 wires up `scq.config.paths` properly, this module
implements path resolution directly:

  1. Environment variable `SCQ_DB_PATH` (absolute or repo-relative)
  2. `data/arxiv_scooper.db` under the repo root (default)

The repo root is detected by walking upward from this file until we find
a directory containing both `data/` and `pyproject.toml`.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root directory."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "pyproject.toml").is_file() and (parent / "data").is_dir():
            return parent
    # Fallback: two levels up from this file (scq/db/connection.py -> repo)
    return here.parents[2]


def db_path() -> Path:
    """Resolve the path to arxiv_scooper.db."""
    env = os.environ.get("SCQ_DB_PATH")
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = repo_root() / p
        return p
    return repo_root() / "data" / "arxiv_scooper.db"


def connect(*, ensure_migrations: bool = True) -> sqlite3.Connection:
    """Open a connection to the SCQ database, creating + migrating if needed.

    With `ensure_migrations=True` (the default), pending migrations are
    applied before the connection is returned. Tests that want a clean
    in-memory DB should call `apply_pending()` directly instead.
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    if ensure_migrations:
        # Lazy import to avoid a circular import at module load time.
        from scq.db.migrations import apply_pending

        apply_pending(conn)
    return conn
