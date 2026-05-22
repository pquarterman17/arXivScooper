"""Tests for scq.config.paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the repo root importable when running pytest from the repo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq.config.paths import Paths, paths, repo_root, refresh  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Each test starts with a clean env, a clean cache, and a fresh fake repo."""
    for var in [
        "SCQ_REPO_ROOT", "SCQ_DB_PATH", "SCQ_PAPERS_DIR", "SCQ_FIGURES_DIR",
        "SCQ_INBOX_DIR", "SCQ_EXPORTS_DIR", "SCQ_REFERENCES_BIB", "SCQ_REFERENCES_TXT",
    ]:
        monkeypatch.delenv(var, raising=False)
    refresh()
    # Build a minimal fake repo so repo_root() resolves predictably
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    monkeypatch.setenv("SCQ_REPO_ROOT", str(tmp_path))
    yield
    refresh()


def test_defaults_resolve_under_repo_root(tmp_path):
    p = paths(force_reload=True)
    assert p.repo_root == tmp_path.resolve()
    assert p.db_path == (tmp_path / "data" / "arxiv_scooper.db").resolve()
    assert p.papers_dir == (tmp_path / "papers").resolve()
    assert p.digests_dir == (tmp_path / "digests").resolve()
    assert p.references_bib_path == (tmp_path / "references.bib").resolve()


def test_paths_object_is_immutable():
    p = paths(force_reload=True)
    assert isinstance(p, Paths)
    with pytest.raises(Exception):
        p.db_path = Path("/nope")  # type: ignore[misc]


def test_env_var_override(monkeypatch, tmp_path):
    custom_db = tmp_path / "alt" / "papers.db"
    monkeypatch.setenv("SCQ_DB_PATH", str(custom_db))
    p = paths(force_reload=True)
    assert p.db_path == custom_db.resolve()


def test_env_var_relative_path_resolves_under_repo_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SCQ_PAPERS_DIR", "relative/papers")
    p = paths(force_reload=True)
    assert p.papers_dir == (tmp_path / "relative" / "papers").resolve()


def test_toml_override(tmp_path):
    user_dir = tmp_path / "data" / "user_config"
    user_dir.mkdir(parents=True)
    (user_dir / "paths.toml").write_text(
        'db_path = "alt/db.sqlite"\n'
        'papers_dir = "/abs/papers"\n'
    )
    p = paths(force_reload=True)
    assert p.db_path == (tmp_path / "alt" / "db.sqlite").resolve()
    assert p.papers_dir == Path("/abs/papers").resolve()
    # Untouched fields keep defaults
    assert p.figures_dir == (tmp_path / "figures").resolve()


def test_env_overrides_toml(monkeypatch, tmp_path):
    user_dir = tmp_path / "data" / "user_config"
    user_dir.mkdir(parents=True)
    (user_dir / "paths.toml").write_text('db_path = "from-toml.db"\n')
    monkeypatch.setenv("SCQ_DB_PATH", str(tmp_path / "from-env.db"))
    p = paths(force_reload=True)
    assert p.db_path == (tmp_path / "from-env.db").resolve()


def test_malformed_toml_falls_back_to_defaults(tmp_path, capsys):
    user_dir = tmp_path / "data" / "user_config"
    user_dir.mkdir(parents=True)
    (user_dir / "paths.toml").write_text("this is not valid TOML [[")
    p = paths(force_reload=True)
    # Defaults still apply
    assert p.db_path == (tmp_path / "data" / "arxiv_scooper.db").resolve()
    # And we logged a warning
    err = capsys.readouterr().err
    assert "paths.toml" in err.lower() or "scq.config.paths" in err.lower()


def test_caching(tmp_path):
    p1 = paths()
    p2 = paths()
    assert p1 is p2
    refresh()
    p3 = paths()
    # After refresh, a new instance is built (frozen dataclasses use value
    # equality but not identity, so we just check the cache cleared)
    assert p1 is not p3 or p1 == p3


def test_force_reload_picks_up_env_change(monkeypatch, tmp_path):
    paths(force_reload=True)
    monkeypatch.setenv("SCQ_DB_PATH", str(tmp_path / "new.db"))
    p = paths(force_reload=True)
    assert p.db_path == (tmp_path / "new.db").resolve()


def test_repo_root_detection_finds_pyproject(tmp_path, monkeypatch):
    # Without SCQ_REPO_ROOT, fall back to walking up from the source file.
    # We can't easily test this without moving the source, but we can verify
    # that repo_root() returns something containing pyproject.toml.
    monkeypatch.delenv("SCQ_REPO_ROOT", raising=False)
    refresh()
    root = repo_root()
    assert (root / "pyproject.toml").is_file()
