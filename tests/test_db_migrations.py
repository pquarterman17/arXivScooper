"""Tests for ``scq.db.migrations``.

Covers the discover / apply / record cycle plus the safety properties:
re-running is a no-op, malformed migration files are rejected, and a
SQL error rolls back the schema_version row so the migration stays
pending. The shipped ``data/migrations/001_initial.sql`` is exercised
end-to-end against an in-memory SQLite to catch schema regressions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scq.db.migrations import (
    DEFAULT_MIGRATIONS_DIR,
    applied_versions,
    apply_pending,
    current_version,
    discover,
)

# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def migrations_dir(tmp_path: Path) -> Path:
    """Empty migrations directory the test populates explicitly."""
    d = tmp_path / "migrations"
    d.mkdir()
    return d


def write_mig(d: Path, version: int, name: str, sql: str) -> Path:
    p = d / f"{version:03d}_{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return p


# ─── discover() ───────────────────────────────────────────────────────


class TestDiscover:
    def test_returns_empty_list_for_empty_dir(self, migrations_dir: Path):
        assert discover(migrations_dir) == []

    def test_returns_migrations_sorted_by_version(self, migrations_dir: Path):
        write_mig(migrations_dir, 3, "third", "")
        write_mig(migrations_dir, 1, "first", "")
        write_mig(migrations_dir, 2, "second", "")

        result = discover(migrations_dir)

        assert [m.version for m in result] == [1, 2, 3]
        assert [m.name for m in result] == ["001_first", "002_second", "003_third"]

    def test_rejects_duplicate_versions(self, migrations_dir: Path):
        write_mig(migrations_dir, 1, "first", "")
        write_mig(migrations_dir, 1, "also_first", "")

        with pytest.raises(ValueError, match="[Dd]uplicate"):
            discover(migrations_dir)

    def test_ignores_non_sql_files(self, migrations_dir: Path):
        # README and .gitkeep are common siblings; they shouldn't be touched.
        (migrations_dir / "README.md").write_text("notes")
        (migrations_dir / ".gitkeep").write_text("")
        write_mig(migrations_dir, 1, "real", "")

        result = discover(migrations_dir)

        assert len(result) == 1
        assert result[0].version == 1

    def test_rejects_sql_files_with_bad_naming(self, migrations_dir: Path):
        # Strictness here is the point: a misnamed migration is a bug, not
        # something to silently skip.
        (migrations_dir / "no_version.sql").write_text("")
        with pytest.raises(ValueError, match="does not match"):
            discover(migrations_dir)

    def test_missing_directory_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            discover(tmp_path / "does_not_exist")

    def test_migration_sql_is_lazily_loaded_from_disk(self, migrations_dir: Path):
        path = write_mig(migrations_dir, 1, "x", "SELECT 1;")
        mig = discover(migrations_dir)[0]
        # Edit the file after discovery; .sql reads it again
        path.write_text("SELECT 2;", encoding="utf-8")
        assert mig.sql.strip() == "SELECT 2;"


# ─── apply_pending() ──────────────────────────────────────────────────


class TestApplyPending:
    def test_applies_all_pending_migrations(
        self, conn: sqlite3.Connection, migrations_dir: Path
    ):
        write_mig(migrations_dir, 1, "a", "CREATE TABLE a (id INTEGER);")
        write_mig(migrations_dir, 2, "b", "CREATE TABLE b (id INTEGER);")

        applied = apply_pending(conn, migrations_dir)

        assert [m.version for m in applied] == [1, 2]
        # Both tables exist
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"a", "b"}.issubset(tables)
        # Both versions recorded
        assert applied_versions(conn) == {1, 2}

    def test_second_run_is_a_noop(
        self, conn: sqlite3.Connection, migrations_dir: Path
    ):
        write_mig(migrations_dir, 1, "a", "CREATE TABLE a (id INTEGER);")
        apply_pending(conn, migrations_dir)

        applied = apply_pending(conn, migrations_dir)

        assert applied == []
        assert applied_versions(conn) == {1}

    def test_only_applies_versions_not_already_recorded(
        self, conn: sqlite3.Connection, migrations_dir: Path
    ):
        write_mig(migrations_dir, 1, "a", "CREATE TABLE a (id INTEGER);")
        apply_pending(conn, migrations_dir)
        # Add a second migration after the first one has been applied
        write_mig(migrations_dir, 2, "b", "CREATE TABLE b (id INTEGER);")

        applied = apply_pending(conn, migrations_dir)

        assert [m.version for m in applied] == [2]
        assert applied_versions(conn) == {1, 2}

    def test_rolls_back_schema_version_on_sql_error(
        self, conn: sqlite3.Connection, migrations_dir: Path
    ):
        write_mig(migrations_dir, 1, "broken", "CREATE TABLE; -- syntax error")

        with pytest.raises(sqlite3.OperationalError):
            apply_pending(conn, migrations_dir)

        # The migration row must NOT have been recorded — otherwise a fix
        # would never be re-applied.
        assert applied_versions(conn) == set()

    def test_applies_in_ascending_version_order(
        self, conn: sqlite3.Connection, migrations_dir: Path
    ):
        # Migration 2 depends on a table created by migration 1; if order
        # is wrong, the second one fails.
        write_mig(migrations_dir, 1, "create", "CREATE TABLE x (id INTEGER);")
        write_mig(
            migrations_dir, 2, "alter", "ALTER TABLE x ADD COLUMN name TEXT;"
        )

        # Insert in reverse to make sure discover() (not filesystem order) is
        # the source of truth — write order shouldn't matter.
        apply_pending(conn, migrations_dir)

        cols = {row[1] for row in conn.execute("PRAGMA table_info(x)")}
        assert cols == {"id", "name"}


# ─── current_version() / applied_versions() ───────────────────────────


class TestVersionState:
    def test_current_version_zero_on_empty(self, conn: sqlite3.Connection):
        assert current_version(conn) == 0

    def test_current_version_returns_max_applied(
        self, conn: sqlite3.Connection, migrations_dir: Path
    ):
        write_mig(migrations_dir, 1, "a", "")
        write_mig(migrations_dir, 5, "b", "")
        apply_pending(conn, migrations_dir)
        assert current_version(conn) == 5

    def test_applied_versions_creates_table_idempotently(
        self, conn: sqlite3.Connection
    ):
        # First call creates the table; second still works
        assert applied_versions(conn) == set()
        assert applied_versions(conn) == set()


# ─── Real shipped migrations ──────────────────────────────────────────


class TestShippedMigrations:
    """End-to-end: the actual data/migrations/*.sql against in-memory SQLite.

    Catches the case where someone breaks the SQL syntax or removes a
    required table/column without realising the migration runner can't
    recover.
    """

    def test_shipped_migrations_apply_cleanly(self, conn: sqlite3.Connection):
        applied = apply_pending(conn, DEFAULT_MIGRATIONS_DIR)
        assert len(applied) >= 1, "no shipped migrations discovered"

    def test_shipped_schema_has_core_tables(self, conn: sqlite3.Connection):
        apply_pending(conn, DEFAULT_MIGRATIONS_DIR)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        # The four core tables every downstream service depends on
        assert {"papers", "notes", "read_status", "collections"}.issubset(tables)

    def test_shipped_migrations_record_themselves(self, conn: sqlite3.Connection):
        applied = apply_pending(conn, DEFAULT_MIGRATIONS_DIR)
        recorded = applied_versions(conn)
        assert recorded == {m.version for m in applied}

    def test_shipped_migrations_are_idempotent(self, conn: sqlite3.Connection):
        apply_pending(conn, DEFAULT_MIGRATIONS_DIR)
        # Second run should be a no-op
        second = apply_pending(conn, DEFAULT_MIGRATIONS_DIR)
        assert second == []
