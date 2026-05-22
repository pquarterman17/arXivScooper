"""Tests for scq.cli — argument parsing and command dispatch.

Each test invokes ``main(['config', '...'])`` directly and inspects the
return code + captured output. No subprocess overhead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq.cli import main  # noqa: E402


def test_no_args_prints_help_and_exits_nonzero(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert "scq" in out


# ─── MT-5: --help smoke for every passthrough subcommand ───
#
# B5/B9 from the 2026-04-30 audit: passthroughs (process, mendeley, watch,
# overleaf, ...) lack argparse-aware help handling, so `scq <cmd> --help`
# either crashed (watch entered the daemon loop), produced "ARXIV-ID
# --help not found" (process treated --help as the positional id), or
# silently parsed --help as the path argument. Fix: scq.cli intercepts
# --help/-h before the passthrough fires and prints the underlying
# module's docstring.
#
# This test asserts that for *every* passthrough, --help and -h:
#   1. exit 0
#   2. print the underlying module path
#   3. print a non-empty docstring summary (or a "no docstring" notice)
#
# If a future passthrough is added to scq.cli._PASSTHROUGH_COMMANDS but
# not to _PASSTHROUGH_MODULES, the parametrize set drifts and the
# missing-module check below catches it.

@pytest.mark.parametrize("flag", ["--help", "-h"])
@pytest.mark.parametrize("subcmd", [
    "process", "merge", "init-db", "digest",
    "mendeley", "inbox", "watch", "overleaf", "build-index",
])
def test_passthrough_help_exits_zero_and_prints_docstring(subcmd, flag, capsys):
    rc = main([subcmd, flag])
    out = capsys.readouterr().out
    assert rc == 0, f"`scq {subcmd} {flag}` should return 0, got {rc}"
    # Module path is always printed in the header
    assert f"scq {subcmd}" in out
    assert "scq." in out, f"missing module path in output: {out!r}"
    # Either a real docstring or the explicit "no docstring" notice
    assert ("(no docstring on" in out) or (len(out.strip().splitlines()) >= 3)


def test_passthrough_modules_table_covers_every_passthrough_command():
    """Drift catcher: keep _PASSTHROUGH_COMMANDS and _PASSTHROUGH_MODULES in sync."""
    from scq.cli import _PASSTHROUGH_COMMANDS, _PASSTHROUGH_MODULES
    cmd_names = set(_PASSTHROUGH_COMMANDS.keys())
    mod_names = set(_PASSTHROUGH_MODULES.keys())
    missing = cmd_names - mod_names
    extra = mod_names - cmd_names
    assert not missing, f"_PASSTHROUGH_MODULES missing entries: {missing}"
    assert not extra, f"_PASSTHROUGH_MODULES has stale entries: {extra}"


def test_passthrough_help_for_unknown_command_returns_1(capsys):
    """Defense-in-depth: _passthrough_help on an unknown name returns 1.

    Not directly reachable through main() (which checks _PASSTHROUGH_COMMANDS
    first), but the helper itself should be safe to call.
    """
    from scq.cli import _passthrough_help
    rc = _passthrough_help("not-a-real-passthrough")
    err = capsys.readouterr().err
    assert rc == 1
    assert "no help available" in err


def test_show_emits_json_for_all_domains(capsys):
    rc = main(["config", "show"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert isinstance(payload, dict)
    # Every shipped domain shows up
    from scq.config.user import MANIFEST
    assert set(MANIFEST).issubset(payload.keys())


def test_show_one_domain(capsys):
    rc = main(["config", "show", "digest"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    # Digest config has at least cadence + maxPapers per the schema's required list
    assert "cadence" in payload
    assert "maxPapers" in payload


def test_show_unknown_domain_raises(capsys):
    with pytest.raises(ValueError, match="unknown"):
        main(["config", "show", "not-a-real-domain"])


def test_get_extracts_a_key(capsys):
    rc = main(["config", "get", "digest", "maxPapers"])
    out = capsys.readouterr().out
    assert rc == 0
    val = json.loads(out)
    assert val is None or val >= 1  # null = no cap, or positive int


def test_get_nested_key(capsys):
    rc = main(["config", "get", "search-sources", "autoFetch.enabled"])
    out = capsys.readouterr().out
    assert rc == 0
    # Just check it parses as a JSON value
    json.loads(out)


def test_get_missing_key_returns_1(capsys):
    rc = main(["config", "get", "digest", "doesNotExist"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not found" in err


def test_validate_all_clean_exit_0(capsys):
    rc = main(["config", "validate"])
    out = capsys.readouterr().out
    assert rc == 0
    # Each domain reports "ok"
    from scq.config.user import MANIFEST
    for d in MANIFEST:
        assert f"{d}: ok" in out


def test_validate_one_domain(capsys):
    rc = main(["config", "validate", "digest"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "digest: ok" in out


def test_paths_emits_json(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("SCQ_DB_PATH", str(tmp_path / "arxiv_scooper.db"))
    from scq.config.paths import refresh
    refresh()
    rc = main(["config", "paths"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert "db_path" in payload
    assert "papers_dir" in payload
    assert payload["db_path"].endswith("arxiv_scooper.db")


def test_has_secret_returns_0_when_set(monkeypatch):
    monkeypatch.setenv("SCQ_TEST_KEY", "value")
    assert main(["config", "has-secret", "test_key"]) == 0


def test_has_secret_returns_1_when_unset(monkeypatch):
    monkeypatch.delenv("SCQ_TEST_KEY", raising=False)
    assert main(["config", "has-secret", "test_key"]) == 1


def test_set_secret_without_keyring_returns_2(monkeypatch, capsys):
    # Force keyring_available to return False
    from scq.config import secrets as secrets_mod
    monkeypatch.setattr(secrets_mod, "keyring_available", lambda: False)
    rc = main(["config", "set-secret", "anything"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "pip install" in err
    assert "keyring" in err


# ─── scq init ───


def test_init_creates_fresh_db(tmp_path, capsys):
    db = tmp_path / "arxiv_scooper.db"
    rc = main(["init", "--db-path", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert db.exists()
    assert "Created" in out or "Migrated" in out
    # Schema actually present
    import sqlite3
    c = sqlite3.connect(db)
    try:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        c.close()
    assert "papers" in tables
    assert "schema_version" in tables


def test_init_idempotent_on_empty_db(tmp_path, capsys):
    db = tmp_path / "arxiv_scooper.db"
    main(["init", "--db-path", str(db)])
    capsys.readouterr()  # discard
    rc = main(["init", "--db-path", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "up to date" in out


def test_init_refuses_when_papers_present(tmp_path, capsys):
    db = tmp_path / "arxiv_scooper.db"
    main(["init", "--db-path", str(db)])
    capsys.readouterr()
    # Insert a paper row to simulate real user data.
    import sqlite3
    c = sqlite3.connect(db)
    try:
        c.execute(
            "INSERT INTO papers (id, title, authors, year) VALUES (?, ?, ?, ?)",
            ("test/0001", "T", "A", 2026),
        )
        c.commit()
    finally:
        c.close()
    rc = main(["init", "--db-path", str(db)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "already contains" in err
    # DB untouched
    c = sqlite3.connect(db)
    try:
        n = c.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    finally:
        c.close()
    assert n == 1


def test_init_force_overwrites_populated_db(tmp_path, capsys):
    db = tmp_path / "arxiv_scooper.db"
    main(["init", "--db-path", str(db)])
    capsys.readouterr()
    import sqlite3
    c = sqlite3.connect(db)
    try:
        c.execute(
            "INSERT INTO papers (id, title, authors, year) VALUES (?, ?, ?, ?)",
            ("test/0001", "T", "A", 2026),
        )
        c.commit()
    finally:
        c.close()
    rc = main(["init", "--force", "--db-path", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "removed existing" in out
    c = sqlite3.connect(db)
    try:
        n = c.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    finally:
        c.close()
    assert n == 0


def test_init_creates_parent_directory(tmp_path, capsys):
    db = tmp_path / "nested" / "subdir" / "arxiv_scooper.db"
    rc = main(["init", "--db-path", str(db)])
    capsys.readouterr()
    assert rc == 0
    assert db.exists()


def test_init_rejects_non_sqlite_file(tmp_path, capsys):
    db = tmp_path / "junk.db"
    db.write_bytes(b"this is definitely not a sqlite file " * 100)
    rc = main(["init", "--db-path", str(db)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not a valid SQLite" in err


# ─── plan #12: passthrough subcommands ───


def test_process_subcommand_dispatches_to_module(monkeypatch, capsys):
    """`scq process X` should route to scq.ingest.process.main with `process` not in argv."""
    called_with = []
    def fake_main():
        called_with.append(list(__import__('sys').argv))
    monkeypatch.setattr("scq.ingest.process.main", fake_main)
    rc = main(["process", "2401.12345", "--note", "x"])
    assert rc == 0
    # The fake saw argv = ["scq process", "2401.12345", "--note", "x"]
    assert called_with[0][1:] == ["2401.12345", "--note", "x"]


def test_merge_subcommand_dispatches_to_module(monkeypatch):
    received = []
    def fake_main(argv):
        received.append(list(argv))
        return 0
    monkeypatch.setattr("scq.db.merge.main", fake_main)
    rc = main(["merge", "merge", "src.db", "dst.db", "--dry-run"])
    assert rc == 0
    assert received[0] == ["merge", "src.db", "dst.db", "--dry-run"]


def test_init_db_subcommand_with_options(monkeypatch):
    """init-db --stats should pass through cleanly even though `--stats` is option-shaped."""
    received = []
    def fake_main(argv):
        received.append(list(argv))
        return 0
    monkeypatch.setattr("scq.db.init.main", fake_main)
    rc = main(["init-db", "--stats"])
    assert rc == 0
    assert received[0] == ["--stats"]


def test_passthrough_appears_in_help(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert "process" in out
    assert "merge" in out
    assert "init-db" in out
    assert rc == 1


def test_top_level_init_still_works_after_passthrough_added(tmp_path, capsys):
    """Regression: the original `scq init --db-path X` (no passthrough) still works."""
    db = tmp_path / "fresh.db"
    rc = main(["init", "--db-path", str(db)])
    assert rc == 0
    assert db.exists()


# ─── plan #12 wave 2: ingest/overleaf/search passthrough subcommands ───


def test_mendeley_subcommand_routes_to_module(monkeypatch):
    received = []
    def fake_main():
        import sys as _sys
        received.append(list(_sys.argv))
    monkeypatch.setattr("scq.ingest.mendeley.main", fake_main)
    rc = main(["mendeley", "ref.bib"])
    assert rc == 0
    # supports_argv=False splices argv[0] = module path
    assert received[0][1:] == ["ref.bib"]


def test_inbox_subcommand_routes_to_module(monkeypatch):
    received = []
    def fake_main():
        import sys as _sys
        received.append(list(_sys.argv))
    monkeypatch.setattr("scq.ingest.inbox.main", fake_main)
    rc = main(["inbox"])
    assert rc == 0
    assert received[0][1:] == []


def test_overleaf_subcommand_routes_to_module(monkeypatch):
    received = []
    def fake_main():
        import sys as _sys
        received.append(list(_sys.argv))
    monkeypatch.setattr("scq.overleaf.sync.main", fake_main)
    rc = main(["overleaf", "--status"])
    assert rc == 0
    assert received[0][1:] == ["--status"]


def test_build_index_subcommand_routes_to_module(monkeypatch):
    """build-index uses supports_argv=True (its main signature accepts argv)."""
    received = []
    def fake_main(argv=None):
        received.append(list(argv or []))
        return 0
    monkeypatch.setattr("scq.search.index.main", fake_main)
    rc = main(["build-index", "--stats"])
    assert rc == 0
    assert received[0] == ["--stats"]


def test_wave2_passthrough_appears_in_help(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert "mendeley" in out
    assert "inbox" in out
    assert "watch" in out
    assert "overleaf" in out
    assert "build-index" in out


def test_mendeley_lazy_import_doesnt_crash():
    """Regression: importing scq.ingest.mendeley shouldn't try to import
    bibtexparser at module load — that used to sys.exit(1) and break the
    CLI's lazy-passthrough dispatch."""
    import importlib
    mod = importlib.import_module("scq.ingest.mendeley")
    assert hasattr(mod, "main")
    # bibtexparser should still be None at this point (lazy)
    assert mod.bibtexparser is None


def test_extract_lazy_import_doesnt_crash():
    """Regression for #13-audit B2: importing scq.ingest.extract should NOT
    sys.exit(1) at module load when PyMuPDF/Pillow are missing — that used
    to break unrelated CLI commands via the lazy-passthrough dispatch."""
    import importlib
    mod = importlib.import_module("scq.ingest.extract")
    assert hasattr(mod, "main")
    # The optional deps should still be None at this point (lazy)
    assert mod.fitz is None
    assert mod.Image is None
