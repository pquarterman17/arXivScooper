"""Path resolution for the SCQ toolkit.

Reads ``data/user_config/paths.toml`` (if it exists), applies env-var overrides,
and falls back to sensible defaults rooted at the repo root. All returned
paths are absolute :class:`pathlib.Path` objects.

Resolution order (highest priority first):
    1. Environment variables: ``SCQ_DB_PATH``, ``SCQ_PAPERS_DIR``, etc.
    2. Values from ``data/user_config/paths.toml``
    3. Built-in defaults

Repo root detection walks up from this file until it finds a directory
containing ``pyproject.toml``. Override with ``SCQ_REPO_ROOT`` if needed
(useful when running outside a normal checkout).

Usage::

    from scq.config import paths
    p = paths()
    conn = sqlite3.connect(p.db_path)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover — only on 3.10
    import tomli as tomllib  # type: ignore[no-redef]


# Defaults are repo-relative; resolved to absolute by `_resolve()`.
_DEFAULTS: dict[str, str] = {
    "db_path": "data/arxiv_scooper.db",
    "papers_dir": "papers",
    "figures_dir": "figures",
    "inbox_dir": "inbox",
    "exports_dir": "exports",
    "digests_dir": "digests",
    "references_bib_path": "references.bib",
    "references_txt_path": "references.txt",
}

# Env var → field map. Each var, when set, replaces the corresponding field.
_ENV_OVERRIDES: dict[str, str] = {
    "SCQ_DB_PATH": "db_path",
    "SCQ_PAPERS_DIR": "papers_dir",
    "SCQ_FIGURES_DIR": "figures_dir",
    "SCQ_INBOX_DIR": "inbox_dir",
    "SCQ_EXPORTS_DIR": "exports_dir",
    "SCQ_DIGESTS_DIR": "digests_dir",
    "SCQ_REFERENCES_BIB": "references_bib_path",
    "SCQ_REFERENCES_TXT": "references_txt_path",
}


@dataclass(frozen=True)
class Paths:
    """All filesystem locations the toolkit needs. Frozen — read-only."""

    repo_root: Path
    db_path: Path
    papers_dir: Path
    figures_dir: Path
    inbox_dir: Path
    exports_dir: Path
    digests_dir: Path
    references_bib_path: Path
    references_txt_path: Path


def repo_root() -> Path:
    """Locate the repo root.

    Honors ``SCQ_REPO_ROOT`` if set. Otherwise walks up from this file until a
    directory containing ``pyproject.toml`` is found. Falls back to the current
    working directory if that fails.
    """
    env = os.environ.get("SCQ_REPO_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd().resolve()


def paths(*, force_reload: bool = False) -> Paths:
    """Return the resolved :class:`Paths` for this process.

    Cached after the first call. Pass ``force_reload=True`` from tests when
    env vars or the TOML file change between calls. Tests can also call
    :func:`refresh` to invalidate the cache without inspecting the cached
    object first.
    """
    if force_reload:
        _cached_paths.cache_clear()
    return _cached_paths()


def refresh() -> None:
    """Drop the cache. Call from tests after mutating env or paths.toml."""
    _cached_paths.cache_clear()


# ─── internals ───


@lru_cache(maxsize=1)
def _cached_paths() -> Paths:
    root = repo_root()
    raw = dict(_DEFAULTS)

    # Layer 2: TOML overrides
    toml_path = root / "data" / "user_config" / "paths.toml"
    if toml_path.is_file():
        try:
            with open(toml_path, "rb") as f:
                doc = tomllib.load(f)
            for key in _DEFAULTS:
                if key in doc and isinstance(doc[key], str) and doc[key]:
                    raw[key] = doc[key]
        except (OSError, tomllib.TOMLDecodeError) as e:
            # Log to stderr; missing/malformed TOML shouldn't crash a CLI tool
            # the user is trying to run to fix the very same problem.
            import sys

            print(
                f"[scq.config.paths] could not read {toml_path}: {e}; using defaults",
                file=sys.stderr,
            )

    # Layer 1: env-var overrides (highest priority)
    for env_var, field_name in _ENV_OVERRIDES.items():
        v = os.environ.get(env_var)
        if v:
            raw[field_name] = v

    return Paths(
        repo_root=root,
        **{k: _resolve(root, v) for k, v in raw.items()},
    )


def _resolve(root: Path, value: str) -> Path:
    """Make ``value`` absolute. Relative paths resolve under the repo root."""
    p = Path(value)
    return p.resolve() if p.is_absolute() else (root / p).resolve()


__all__ = ["Paths", "paths", "repo_root", "refresh"]
