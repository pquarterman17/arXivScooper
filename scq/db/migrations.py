"""Migration runner for the SCQ paper database.

Applies versioned SQL migrations from data/migrations/NNN_*.sql in numeric
order, recording each application in a schema_version table so a second run
is a no-op.

Usage:
    from scq.db.migrations import apply_pending
    import sqlite3
    conn = sqlite3.connect("data/arxiv_scooper.db")
    apply_pending(conn)

Or via CLI:
    python -m scq.db.migrations data/arxiv_scooper.db

Migration files MUST be named "NNN_<description>.sql" where NNN is a
zero-padded integer (001, 002, ...). The integer is the version. Files
are applied in ascending order; each is wrapped in a single transaction.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Path of the migrations directory relative to the repo root.
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "data" / "migrations"

_MIGRATION_NAME_RE = re.compile(r"^(\d+)_[A-Za-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def discover(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[Migration]:
    """Return all migrations in `migrations_dir`, sorted by version."""
    if not migrations_dir.is_dir():
        raise FileNotFoundError(f"Migrations directory not found: {migrations_dir}")

    found: list[Migration] = []
    for path in sorted(migrations_dir.iterdir()):
        if not path.is_file() or path.suffix != ".sql":
            continue
        m = _MIGRATION_NAME_RE.match(path.name)
        if not m:
            raise ValueError(f"Migration filename {path.name!r} does not match NNN_<name>.sql")
        found.append(Migration(version=int(m.group(1)), name=path.stem, path=path))

    versions = [mig.version for mig in found]
    if len(versions) != len(set(versions)):
        raise ValueError(f"Duplicate migration version numbers: {versions}")
    return sorted(found, key=lambda mig: mig.version)


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of migration versions already applied to `conn`."""
    _ensure_schema_version_table(conn)
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    return {row[0] for row in rows}


def apply_pending(
    conn: sqlite3.Connection,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    *,
    verbose: bool = False,
) -> list[Migration]:
    """Apply every migration in `migrations_dir` not yet recorded on `conn`.

    Returns the list of migrations that were applied this call (empty if
    everything was already up to date).
    """
    _ensure_schema_version_table(conn)
    already = applied_versions(conn)
    pending = [mig for mig in discover(migrations_dir) if mig.version not in already]

    for mig in pending:
        if verbose:
            print(f"Applying migration {mig.version:03d}: {mig.name}")
        # Each migration runs in its own transaction. executescript() commits
        # any pending transaction before running, so we BEGIN explicitly to
        # wrap the whole thing.
        try:
            conn.execute("BEGIN")
            conn.executescript(mig.sql)
            conn.execute(
                "INSERT INTO schema_version (version, name) VALUES (?, ?)",
                (mig.version, mig.name),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return pending


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    versions = applied_versions(conn)
    return max(versions) if versions else 0


def _cli(argv: Iterable[str]) -> int:
    args = list(argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    db_path = Path(args[0])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        applied = apply_pending(conn, verbose=True)
        if not applied:
            print(f"Database at {db_path} is up to date (version {current_version(conn)}).")
        else:
            print(f"Applied {len(applied)} migration(s); now at version {current_version(conn)}.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
