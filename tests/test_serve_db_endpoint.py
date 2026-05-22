"""Integration tests for the canonical DB GET route.

The browser loads the SQLite DB via ``GET /data/<dbname>.db`` and reads
the bytes with sql.js. Since paths.toml can place the DB *outside* the
repo (the OneDrive layout most users have), the static-file handler
can't serve it — there's a dedicated route that resolves the URL
against ``paths().db_path`` per request.

These tests verify three behaviors:
  1. The route serves the DB even when it lives outside the static root
  2. The route returns 404 with a helpful path when the DB is missing
  3. URLs that don't match the canonical name fall through to the
     static handler (i.e. the route doesn't accidentally hijack siblings)
"""

from __future__ import annotations

import contextlib
import http.server
import shutil
import socket
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq import server as serve  # noqa: E402


SQLITE_MAGIC = b"SQLite format 3\x00"


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    monkeypatch.setattr(serve, "_ensure_console", lambda: None)


@pytest.fixture
def server_with_external_db(tmp_path, monkeypatch):
    """Server whose repo_root is `tmp_path/repo` but whose DB lives at
    `tmp_path/external/store.db` — i.e. *outside* the static-served tree,
    mimicking the OneDrive layout. paths.toml is the bridge."""
    repo_src = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    external = tmp_path / "external"
    (repo / "data" / "user_config").mkdir(parents=True)
    (repo / "data" / "migrations").mkdir(parents=True)
    (repo / "src" / "config" / "schema").mkdir(parents=True)
    (repo / "src" / "config" / "defaults").mkdir(parents=True)
    external.mkdir()
    for f in (repo_src / "src" / "config" / "schema").iterdir():
        shutil.copy2(f, repo / "src" / "config" / "schema" / f.name)
    for f in (repo_src / "src" / "config" / "defaults").iterdir():
        shutil.copy2(f, repo / "src" / "config" / "defaults" / f.name)
    for f in (repo_src / "data" / "migrations").iterdir():
        shutil.copy2(f, repo / "data" / "migrations" / f.name)
    shutil.copy2(repo_src / "pyproject.toml", repo / "pyproject.toml")

    db_file = external / "store.db"
    (repo / "data" / "user_config" / "paths.toml").write_text(
        f"db_path = '{db_file.as_posix()}'\n"
    )

    monkeypatch.chdir(repo)
    monkeypatch.setenv("SCQ_REPO_ROOT", str(repo))
    from scq.config.paths import refresh as _paths_refresh
    _paths_refresh()

    port = _free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), serve.SCQHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, db_file, repo
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_serves_db_living_outside_static_root(server_with_external_db):
    port, db_file, repo = server_with_external_db
    payload = SQLITE_MAGIC + b"\x00" * 200
    db_file.write_bytes(payload)
    # Sanity: the file is genuinely not reachable by the static handler
    assert not (repo / "data" / "store.db").exists()

    status, body, headers = _get(port, "/data/store.db")
    assert status == 200
    assert body == payload
    assert headers.get("Content-Type") == "application/vnd.sqlite3"
    assert headers.get("Content-Length") == str(len(payload))


def test_returns_404_when_db_missing(server_with_external_db):
    port, _db_file, _repo = server_with_external_db
    status, body, _ = _get(port, "/data/store.db")
    assert status == 404
    assert b"not found" in body.lower()


def test_non_canonical_db_url_falls_through_to_static(server_with_external_db):
    port, _db, repo = server_with_external_db
    # An *unrelated* file in data/ should still be served by the static
    # handler — the new route must not hijack siblings.
    (repo / "data" / "README.md").write_text("hello")
    status, body, _ = _get(port, "/data/README.md")
    assert status == 200
    assert body == b"hello"
