"""Integration tests for serve.py's /api/save-db endpoint.

Runs the SCQHandler in-process via ThreadingHTTPServer on an OS-allocated
port, posts SQLite-shaped bytes, and asserts the side effects on disk +
the response shape. No subprocess; no terminal-relaunch behavior fires.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Make the repo root importable so we can pull in serve directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq import server as serve  # noqa: E402

SQLITE_MAGIC = b"SQLite format 3\x00"


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _block_browser_and_console_relaunch(monkeypatch):
    """Defense in depth: if some code path *tries* to open a browser tab or
    re-launch in a new console (the two ways earlier subprocess-based test
    runs caused window/tab leaks), fail loudly instead of opening it.

    Applied to every test in this file via autouse.
    """
    import webbrowser
    def _no_browser(*_a, **_kw):
        raise AssertionError(
            "test attempted to open a browser tab — this should never happen "
            "in the in-process test fixture"
        )
    monkeypatch.setattr(webbrowser, "open", _no_browser)
    monkeypatch.setattr(webbrowser, "open_new_tab", _no_browser)
    # Same for serve._ensure_console — should never fire from a test
    monkeypatch.setattr(serve, "_ensure_console", lambda: None)


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Start ThreadingHTTPServer with the real SCQHandler, pointing at an
    isolated tmp_path DB. Yields (port, db_path). Tears down cleanly."""
    db_path = tmp_path / "arxiv_scooper.db"
    monkeypatch.setattr(serve, "DB_PATH", db_path)

    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), serve.SCQHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, db_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post(port, body, content_type="application/octet-stream", content_length=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/save-db",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", content_type)
    if content_length is not None:
        req.add_header("Content-Length", str(content_length))
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _sqlite_body(payload=b"x" * 1000):
    return SQLITE_MAGIC + payload


# ─── happy paths ───


def test_post_with_valid_magic_writes_file(running_server):
    port, db_path = running_server
    body = _sqlite_body(b"hello world payload")
    status, resp_body = _post(port, body)
    assert status == 200, resp_body
    assert db_path.is_file()
    assert db_path.read_bytes() == body


def test_response_is_well_formed_json(running_server):
    port, _ = running_server
    status, body = _post(port, _sqlite_body())
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["bytes"] > 0
    assert payload["path"].endswith("arxiv_scooper.db")
    assert payload["savedAt"].endswith("Z")


def test_post_replaces_existing_file_atomically(running_server):
    port, db_path = running_server
    db_path.write_bytes(_sqlite_body(b"OLD"))
    new_body = _sqlite_body(b"NEW")
    status, _ = _post(port, new_body)
    assert status == 200
    assert db_path.read_bytes() == new_body


def test_no_temp_files_left_behind_after_success(running_server):
    port, db_path = running_server
    _post(port, _sqlite_body())
    leftovers = [p.name for p in db_path.parent.glob(".scq_save_*")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


# ─── reject paths ───


def test_rejects_body_without_sqlite_magic(running_server):
    port, db_path = running_server
    status, body = _post(port, b"not a database " * 10)
    assert status == 400
    payload = json.loads(body)
    assert "error" in payload
    assert "magic" in payload["error"].lower() or "sqlite" in payload["error"].lower()
    assert not db_path.is_file()


def test_rejects_empty_body(running_server):
    port, db_path = running_server
    status, body = _post(port, b"")
    assert status == 400
    payload = json.loads(body)
    assert "empty" in payload["error"].lower()
    assert not db_path.is_file()


def test_rejects_oversize_body_via_content_length_header(running_server):
    """A misbehaving client could declare a huge Content-Length. The server
    should refuse before reading the body."""
    port, db_path = running_server
    # Use the same tiny body but lie about its length
    huge = serve.MAX_DB_UPLOAD_BYTES + 1
    status, body = _post(port, _sqlite_body(), content_length=huge)
    assert status == 413
    payload = json.loads(body)
    assert "too large" in payload["error"].lower()
    assert not db_path.is_file()


def test_unknown_post_path_still_returns_404(running_server):
    """The new endpoint shouldn't shadow the existing 404 path."""
    port, _ = running_server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/does-not-exist",
        data=b"{}",
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 404


# ─── magic-header check ───


def test_magic_header_must_be_complete(running_server):
    """A truncated body that's shorter than the magic marker must be rejected."""
    port, db_path = running_server
    # `SQLITE_MAGIC` is 16 bytes; send 5
    status, _ = _post(port, b"SQLit")
    assert status == 400
    assert not db_path.is_file()
