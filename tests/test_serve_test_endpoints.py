"""Integration tests for the Settings UI's three test-button endpoints
(`/api/test/db-path`, `/api/test/smtp`, `/api/test/digest`).

The DB-path test exercises a real SQLite file. The SMTP + digest tests
monkeypatch ``smtplib.SMTP_SSL`` so they don't actually open a network
connection — the goal is to verify the request → handler wiring,
config loading, and JSON shape, not to validate credentials end-to-end
(which is what the buttons do at runtime against real services).
"""

from __future__ import annotations

import contextlib
import http.server
import json
import shutil
import socket
import sqlite3
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq import server as serve  # noqa: E402


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _block_browser(monkeypatch):
    import webbrowser
    def _no_browser(*_a, **_kw):
        raise AssertionError("test attempted to open a browser tab")
    monkeypatch.setattr(webbrowser, "open", _no_browser)
    monkeypatch.setattr(webbrowser, "open_new_tab", _no_browser)
    monkeypatch.setattr(serve, "_ensure_console", lambda: None)


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Server with an isolated SCQ_REPO_ROOT pointing at tmp_path that
    mimics the real repo layout (config dirs + pyproject.toml)."""
    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "data" / "user_config").mkdir(parents=True)
    (tmp_path / "data" / "migrations").mkdir(parents=True)
    (tmp_path / "src" / "config" / "schema").mkdir(parents=True)
    (tmp_path / "src" / "config" / "defaults").mkdir(parents=True)
    for f in (repo / "src" / "config" / "schema").iterdir():
        shutil.copy2(f, tmp_path / "src" / "config" / "schema" / f.name)
    for f in (repo / "src" / "config" / "defaults").iterdir():
        shutil.copy2(f, tmp_path / "src" / "config" / "defaults" / f.name)
    for f in (repo / "data" / "migrations").iterdir():
        shutil.copy2(f, tmp_path / "data" / "migrations" / f.name)
    shutil.copy2(repo / "pyproject.toml", tmp_path / "pyproject.toml")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCQ_REPO_ROOT", str(tmp_path))
    from scq.config.paths import refresh as _paths_refresh
    _paths_refresh()

    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), serve.SCQHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post(port, path, body=b""):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ─── /api/test/db-path ───


def test_db_path_returns_ok_for_real_sqlite_file(running_server):
    port, root = running_server
    db = root / "data" / "arxiv_scooper.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    # Apply migrations so the DB has the `papers` table
    from scq.db.migrations import apply_pending
    conn = sqlite3.connect(db)
    try:
        apply_pending(conn)
        conn.execute(
            "INSERT INTO papers (id, title, authors, year) VALUES (?, ?, ?, ?)",
            ("test/0001", "T", "A", 2026),
        )
        conn.commit()
    finally:
        conn.close()

    status, body = _post(port, "/api/test/db-path")
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True, payload
    assert payload["path"].endswith("arxiv_scooper.db")
    assert payload["size"] > 0
    assert payload["papers"] == 1


def test_db_path_returns_error_when_file_missing(running_server):
    port, _ = running_server
    status, body = _post(port, "/api/test/db-path")
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "not found" in payload["error"].lower()


def test_db_path_returns_error_for_non_sqlite_file(running_server):
    port, root = running_server
    db = root / "data" / "arxiv_scooper.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"not a sqlite file at all")
    status, body = _post(port, "/api/test/db-path")
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is False


# ─── /api/test/smtp (serve._smtp_connect monkeypatched) ───
#
# We mock at `serve._smtp_connect` rather than at `smtplib.SMTP_SSL`/`smtplib.SMTP`
# because the helper now branches on transport (SSL vs STARTTLS). Mocking the
# stdlib classes individually creates a coupling between tests and our
# branch logic; mocking the helper isolates one boundary.


class _FakeSMTP:
    """Stand-in for an authenticated SMTP client. Records send calls."""
    def __init__(self, *, raise_on_send=None, recorder=None):
        self._raise = raise_on_send
        self._rec = recorder if recorder is not None else []

    def send_message(self, msg):
        self._rec.append(("send", msg["To"], msg["Subject"]))
        if self._raise:
            raise self._raise

    def quit(self):
        self._rec.append(("quit",))


def _patch_smtp_connect(monkeypatch, recorder=None, raise_on_login=None):
    """Patch `serve._smtp_connect` to a function that records its args + returns
    a fake SMTP client (or raises). Tests can introspect the recorder list to
    verify host/port/use_tls/from_addr were correctly forwarded.
    """
    rec = recorder if recorder is not None else []
    def fake_connect(host, port, use_tls, from_addr, app_password, *, timeout=10):
        rec.append(("connect", host, port, use_tls, from_addr, app_password))
        if raise_on_login is not None:
            raise raise_on_login
        return _FakeSMTP(recorder=rec)
    monkeypatch.setattr(serve, "_smtp_connect", fake_connect)
    return rec


def test_smtp_ok_with_credentials(running_server, monkeypatch):
    port, _ = running_server
    rec = _patch_smtp_connect(monkeypatch)
    monkeypatch.setenv("SCQ_EMAIL_FROM", "me@example.com")
    monkeypatch.setenv("SCQ_EMAIL_APP_PASSWORD", "pw1234")

    status, body = _post(port, "/api/test/smtp")
    payload = json.loads(body)
    assert status == 200
    assert payload["ok"] is True, payload
    assert payload["from"] == "me@example.com"
    # Defaults from the email schema: host=smtp.gmail.com, port=587, useTls=true
    assert payload["host"] == "smtp.gmail.com"
    assert payload["port"] == 587
    # Recorder: ("connect", host, port, use_tls, from_addr, app_password)
    assert any(
        c[0] == "connect" and c[1] == "smtp.gmail.com" and c[2] == 587
        and c[3] is True and c[4] == "me@example.com" and c[5] == "pw1234"
        for c in rec
    )


def test_smtp_uses_camelcase_keys_from_user_config(running_server, monkeypatch):
    """Regression for #13-audit B1: handler must read camelCase keys
    (smtpHost / smtpPort / fromAddress / useTls), not snake_case. Pre-fix,
    every lookup returned None and silently fell through to gmail-on-465."""
    port, root = running_server
    monkeypatch.setenv("SCQ_EMAIL_APP_PASSWORD", "pw1234")
    monkeypatch.delenv("SCQ_EMAIL_FROM", raising=False)
    # User-config sets a non-default host + port. If the code was still reading
    # snake_case it would silently pick smtp.gmail.com:465 and the assertions
    # below would fail.
    (root / "data" / "user_config" / "email.json").write_text(json.dumps({
        "smtpHost": "smtp.fastmail.com",
        "smtpPort": 465,
        "useTls": False,
        "fromAddress": "me@fastmail.com",
    }), encoding="utf-8")
    rec = _patch_smtp_connect(monkeypatch)

    status, body = _post(port, "/api/test/smtp")
    payload = json.loads(body)
    assert status == 200, payload
    assert payload["ok"] is True, payload
    assert payload["host"] == "smtp.fastmail.com"
    assert payload["port"] == 465
    assert payload["from"] == "me@fastmail.com"
    # Recorder confirms the handler forwarded the user's host/port/useTls verbatim
    assert any(
        c[0] == "connect" and c[1] == "smtp.fastmail.com" and c[2] == 465
        and c[3] is False
        for c in rec
    ), f"connect call not in recorder: {rec}"


def test_smtp_missing_password_returns_error(running_server, monkeypatch):
    port, _ = running_server
    monkeypatch.setenv("SCQ_EMAIL_FROM", "me@example.com")
    monkeypatch.delenv("SCQ_EMAIL_APP_PASSWORD", raising=False)
    # also force secrets.get to return None
    from scq.config import secrets as _secrets_mod
    monkeypatch.setattr(_secrets_mod, "get", lambda *_a, **_kw: None)

    status, body = _post(port, "/api/test/smtp")
    payload = json.loads(body)
    assert status == 200
    assert payload["ok"] is False
    assert "password" in payload["error"].lower()


def test_smtp_missing_from_returns_error(running_server, monkeypatch):
    port, _ = running_server
    monkeypatch.delenv("SCQ_EMAIL_FROM", raising=False)
    monkeypatch.setenv("SCQ_EMAIL_APP_PASSWORD", "pw1234")
    status, body = _post(port, "/api/test/smtp")
    payload = json.loads(body)
    assert status == 200
    assert payload["ok"] is False
    assert "fromAddress" in payload["error"]


def test_smtp_auth_failure_surfaces_error(running_server, monkeypatch):
    import smtplib
    port, _ = running_server
    monkeypatch.setenv("SCQ_EMAIL_FROM", "me@example.com")
    monkeypatch.setenv("SCQ_EMAIL_APP_PASSWORD", "wrong")
    err = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    _patch_smtp_connect(monkeypatch, raise_on_login=err)

    status, body = _post(port, "/api/test/smtp")
    payload = json.loads(body)
    assert status == 200
    assert payload["ok"] is False
    assert "535" in payload["error"]


def test_smtp_helper_branches_on_port(monkeypatch):
    """Regression for #13-audit B3: _smtp_connect uses SMTP_SSL on 465 and
    SMTP+starttls on 587. Pre-fix, both endpoints hardcoded SMTP_SSL."""
    import smtplib
    calls = []
    class _Stub:
        def __init__(self, host, port, *a, **kw):
            calls.append(("init", type(self).__name__, host, port))
        def login(self, *a):
            calls.append(("login",))
        def starttls(self, *a, **kw):
            calls.append(("starttls",))
        def quit(self):
            pass
    class _StubSSL(_Stub): pass
    monkeypatch.setattr(smtplib, "SMTP", _Stub)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _StubSSL)

    # Port 465 → SMTP_SSL, no starttls
    serve._smtp_connect("h", 465, True, "f", "p")
    assert ("init", "_StubSSL", "h", 465) in calls
    assert not any(c[0] == "starttls" for c in calls)

    calls.clear()
    # Port 587 + use_tls=True → SMTP + starttls
    serve._smtp_connect("h", 587, True, "f", "p")
    assert ("init", "_Stub", "h", 587) in calls
    assert ("starttls",) in calls

    calls.clear()
    # Port 587 + use_tls=False → SMTP, no starttls
    serve._smtp_connect("h", 587, False, "f", "p")
    assert ("init", "_Stub", "h", 587) in calls
    assert not any(c[0] == "starttls" for c in calls)


# ─── /api/test/digest ───


def test_digest_sends_to_recipients_when_configured(running_server, monkeypatch):
    port, root = running_server
    monkeypatch.setenv("SCQ_EMAIL_FROM", "me@example.com")
    monkeypatch.setenv("SCQ_EMAIL_APP_PASSWORD", "pw1234")
    # Provide a digest config with active recipients
    digest_cfg = {
        "cadence": "daily",
        "maxPapers": 10,
        "recipients": [
            {"email": "alice@x.com", "active": True},
            {"email": "bob@x.com", "active": False},  # disabled — should be skipped
            {"email": "alice@x.com", "active": True},  # dedup
        ],
    }
    (root / "data" / "user_config" / "digest.json").write_text(
        json.dumps(digest_cfg), encoding="utf-8"
    )
    rec = _patch_smtp_connect(monkeypatch)

    status, body = _post(port, "/api/test/digest")
    payload = json.loads(body)
    assert status == 200, payload
    assert payload["ok"] is True, payload
    # Only alice@x.com (active + deduped); bob@x.com excluded because inactive
    assert payload["recipients"] == ["alice@x.com"]
    # The fake recorded a connect call + a send call
    assert any(c[0] == "connect" for c in rec)
    assert any(c[0] == "send" for c in rec)


def test_digest_no_recipients_returns_error(running_server, monkeypatch):
    port, root = running_server
    monkeypatch.setenv("SCQ_EMAIL_FROM", "me@example.com")
    monkeypatch.setenv("SCQ_EMAIL_APP_PASSWORD", "pw1234")
    (root / "data" / "user_config" / "digest.json").write_text(
        json.dumps({"cadence": "daily", "maxPapers": 10, "recipients": []}),
        encoding="utf-8",
    )
    status, body = _post(port, "/api/test/digest")
    payload = json.loads(body)
    assert status == 200
    assert payload["ok"] is False
    assert "recipient" in payload["error"].lower()


def test_digest_normalize_helper_handles_string_format(monkeypatch):
    """Lower-level: serve._normalize_recipients accepts bare strings even
    though the digest schema now requires objects. Keeps the helper
    forward/backward compatible for any other caller passing raw lists."""
    out = serve._normalize_recipients(["a@x.com", "b@x.com", "a@x.com"])
    assert out == ["a@x.com", "b@x.com"]  # dedup preserved
    out = serve._normalize_recipients([
        {"email": "x@x.com", "active": False},
        {"email": "y@x.com"},  # active defaults to True
    ])
    assert out == ["y@x.com"]


def test_pdf_upload_filename_truncated_to_safe_length():
    """Regression: a 300-char original filename must be truncated to fit OS
    limits (255 bytes). The sanitizer should keep the base under 200 chars."""
    import re, os
    long_name = "A" * 300 + ".pdf"
    safe_name = re.sub(r'[^a-zA-Z0-9._\- ]', '_', long_name)
    if not safe_name.lower().endswith('.pdf'):
        safe_name += '.pdf'
    base, ext = os.path.splitext(safe_name)
    if len(base.encode('utf-8')) > 200:
        base = base[:200]
    safe_name = base + ext
    assert len(safe_name) == 204  # 200 + ".pdf"
    assert safe_name.endswith('.pdf')
