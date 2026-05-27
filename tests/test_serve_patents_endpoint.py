"""Integration tests for the patent server endpoints.

  POST /api/patents/add   {number, source} → fetch via provider + store
  GET  /api/patents/list  ?q=&limit=        → stored patents

The provider's network leg is monkeypatched so no test touches the
internet; the DB is a temp file pointed at via SCQ_DB_PATH.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import socket
import sqlite3
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq import server as serve  # noqa: E402
from scq.db.migrations import apply_pending  # noqa: E402
from scq.patents.normalize import Patent  # noqa: E402
from scq.patents.providers import google  # noqa: E402


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _block_browser_and_console(monkeypatch):
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *_a, **_k: None)
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda *_a, **_k: None)
    monkeypatch.setattr(serve, "_ensure_console", lambda: None)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db = tmp_path / "patents_endpoint.db"
    seed = sqlite3.connect(db)
    apply_pending(seed)
    seed.close()
    monkeypatch.setenv("SCQ_DB_PATH", str(db))
    return db


@pytest.fixture
def fake_google(monkeypatch):
    """Return a fixed Patent instead of scraping Google."""

    def fake_fetch(number, **_kw):
        return Patent(
            number="US10374134B2",
            doc_number="10374134",
            kind_code="B2",
            title="Superconducting qubit with tantalum",
            assignee="International Business Machines",
            inventors=["Jay Gambetta"],
            claims=[
                {"num": 1, "text": "A qubit comprising a tantalum pad.", "is_independent": True}
            ],
            source="google",
        )

    monkeypatch.setattr(google, "fetch_patent", fake_fetch)


@pytest.fixture
def running_server(temp_db, fake_google):
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), serve.SCQHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post(port, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, json.loads(r.read())


def test_add_then_list(running_server):
    port = running_server
    status, body = _post(port, "/api/patents/add", {"number": "US10374134B2"})
    assert status == 200
    assert body["ok"] is True
    assert body["patent"]["assignee"] == "International Business Machines"

    status, body = _get(port, "/api/patents/list")
    assert status == 200
    assert body["ok"] is True
    numbers = [p["number"] for p in body["patents"]]
    assert "US10374134B2" in numbers
    rec = next(p for p in body["patents"] if p["number"] == "US10374134B2")
    assert rec["has_summary"] is False  # not summarized yet


def test_add_missing_number_400(running_server):
    import urllib.error

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(running_server, "/api/patents/add", {"source": "google"})
    assert exc.value.code == 400


def test_add_unknown_source_400(running_server):
    import urllib.error

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(running_server, "/api/patents/add", {"number": "US1", "source": "espacenet"})
    assert exc.value.code == 400


def test_list_fts_filter(running_server):
    port = running_server
    _post(port, "/api/patents/add", {"number": "US10374134B2"})
    status, body = _get(port, "/api/patents/list?q=tantalum")
    assert status == 200
    assert any(p["number"] == "US10374134B2" for p in body["patents"])

    status, body = _get(port, "/api/patents/list?q=graphene")
    assert body["patents"] == []
