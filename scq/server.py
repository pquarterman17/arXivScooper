#!/usr/bin/env python3
"""
arXivScooper — Local Server Launcher

Double-click START.bat / START.command or run from a terminal to:
  1. Start an HTTP server on localhost
  2. Open all app pages as browser tabs

Usage:
  python -m scq serve              Open everything (database + scraper)
  python -m scq serve database     Open just the database
  python -m scq serve scraper      Open just the scraper

Works on Windows, macOS, and Linux. No extra dependencies.
Press Ctrl+C (or close the terminal window) to stop.

(Moved from repo-root ``serve.py`` to ``scq/server.py`` in plan #12.)
"""

import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

PORT = 8080

# Resolve the canonical DB path via paths(). The package layout means
# there's no "fall back to relative-to-script" any more — everything goes
# through the resolver, which honors data/user_config/paths.toml + env vars
# and walks up to find the repo root from anywhere on disk.
from scq.config.paths import paths as _scq_paths  # noqa: E402

DB_PATH = _scq_paths().db_path

# Cap on save-db payload size — defends against a runaway upload. SCQ paper
# databases are typically <50 MB even for very large libraries; 200 MB is
# generous but caps the worst case.
MAX_DB_UPLOAD_BYTES = 200 * 1024 * 1024
SQLITE_MAGIC = b"SQLite format 3\x00"

PAGES = {
    "database": "paper_database.html",
    "scraper": "paper_scraper.html",
}

# ── Ensure we're running in a visible terminal ──────────────────────
# On Windows, double-clicking a .py file runs it without a console if
# Python was installed from the Microsoft Store, or if .py is associated
# with pythonw. Re-launch in a real console so the user can see output
# and hit Ctrl+C.


def _ensure_console():
    """On Windows, re-launch in a cmd.exe window if there's no console."""
    if sys.platform != "win32":
        return
    try:
        # If we already have a console, this succeeds silently
        import ctypes

        if ctypes.windll.kernel32.GetConsoleWindow() != 0:
            return
    except Exception:
        return

    # No console — relaunch in one. Use `python -m scq serve` so the
    # invocation works regardless of where scq is installed (editable
    # checkout, pip install, frozen path).
    import subprocess

    args = " ".join(f'"{a}"' for a in sys.argv[1:])
    cmd = f'start "arXivScooper" /WAIT python -m scq serve {args}'.strip()
    subprocess.Popen(cmd, shell=True)
    sys.exit(0)


# ── Server logic ────────────────────────────────────────────────────
# Note: the bootstrap (chdir, sys.argv parsing, port binding, serve_forever)
# all lives in main() at the bottom. That makes serve.py importable for
# tests and for plan #12 when this becomes scq/server.py — no terminal
# auto-relaunch fires on import.

# File extensions that should never be cached (forces browser to always
# fetch fresh copies — no more Ctrl+Shift+R needed after edits).
NO_CACHE_EXTENSIONS = {".html", ".js", ".css", ".json", ".db"}


def _atomic_write(target: Path, text: str) -> None:
    """Write `text` to `target` atomically (temp file + os.replace).

    Defends against partial writes if the process is killed mid-write.
    The temp file lives in the same directory so os.replace is atomic
    on POSIX (and best-effort on Windows).
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".scq_write_", suffix=target.suffix, dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_email_config():
    """Return (email_cfg_dict, smtp_app_password). Falls back to env vars
    if user_config/email.json is missing or the keyring/secret isn't set.
    Used by the /api/test/smtp + /api/test/digest endpoints."""
    from scq.config import user as _user_cfg

    try:
        from scq.config import secrets as _secrets_mod  # type: ignore[import-not-found]

        password = _secrets_mod.get("email_app_password") or os.environ.get(
            "SCQ_EMAIL_APP_PASSWORD", ""
        )
    except Exception:  # noqa: BLE001
        password = os.environ.get("SCQ_EMAIL_APP_PASSWORD", "")
    try:
        cfg = _user_cfg.load_config("email").data
    except Exception:  # noqa: BLE001
        cfg = {}
    return cfg, password


def _load_digest_config():
    """Return the merged digest config (defaults + user_config override)."""
    from scq.config import user as _user_cfg

    try:
        return _user_cfg.load_config("digest").data
    except Exception:  # noqa: BLE001
        return {}


def _smtp_connect(host, port, use_tls, from_addr, app_password, *, timeout=10):
    """Open an authenticated SMTP connection, branching on transport.

    - port 465 → ``smtplib.SMTP_SSL`` (implicit TLS).
    - port 587 (the default the schema ships with) → ``smtplib.SMTP``
      + ``starttls()`` if ``use_tls`` is true; plaintext otherwise.

    Both `_handle_test_smtp` and `_handle_test_digest` route through
    this helper so the SMTP transport choice can't drift between them
    (#13 audit found they used to hardcode SMTP_SSL).
    """
    import smtplib
    import ssl

    ctx = ssl.create_default_context()
    if port == 465:
        smtp = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
    else:
        smtp = smtplib.SMTP(host, port, timeout=timeout)
        if use_tls:
            smtp.starttls(context=ctx)
    smtp.login(from_addr, app_password)
    return smtp


def _normalize_recipients(raw):
    """Accept either a list of strings or a list of {email, active?} dicts.
    Returns a flat list of active email strings, deduplicated, order preserved."""
    out = []
    seen = set()
    for entry in raw or []:
        if isinstance(entry, str):
            email = entry.strip()
            active = True
        elif isinstance(entry, dict):
            email = (entry.get("email") or "").strip()
            active = entry.get("active", True)
        else:
            continue
        if email and active and email not in seen:
            seen.add(email)
            out.append(email)
    return out


def _serialize_paths_toml(value: dict) -> str:
    """Serialize the paths config as TOML.

    Uses single-quoted literal strings so backslashes (Windows paths)
    and other escape-prone characters pass through verbatim. Each entry
    becomes one line, no nesting.
    """
    lines = [
        "# Bootstrap config for filesystem locations.",
        "# Auto-generated by serve.py POST /api/config/paths.",
        "# Single-quoted literal strings preserve backslashes verbatim.",
        "",
    ]
    for key, val in value.items():
        if key == "$schema":
            continue
        # Escape only the single quote (rare in paths) by switching to a
        # double-quoted string with escaped backslashes if needed.
        if "'" in str(val):
            escaped = str(val).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        else:
            lines.append(f"{key} = '{val}'")
    return "\n".join(lines) + "\n"


def find_open_port(start=8080, end=8099):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _env_truthy(value):
    """Treat conventional falsy strings as ``False`` rather than truthy.

    Python's bare ``if os.environ.get(...)`` is truthy for any non-empty
    string, which means ``SCQ_NO_BROWSER=false`` would still suppress
    the browser — the opposite of what a sysadmin reading the var name
    would expect. Recognise the standard set of false-y strings instead.
    """
    if value is None:
        return False
    s = str(value).strip().lower()
    if not s:
        return False
    return s not in ("0", "false", "no", "off")


def open_tabs(port, which):
    """Open requested pages as browser tabs with a small stagger."""
    time.sleep(0.5)  # let server bind first
    for i, key in enumerate(which):
        url = f"http://localhost:{port}/{PAGES[key]}"
        if i == 0:
            webbrowser.open(url)
        else:
            time.sleep(0.3)  # small gap so browser registers separate tabs
            webbrowser.open_new_tab(url)


# ── arXiv Proxy Handler ────────────────────────────────────────────
# Proxies /api/arxiv?<query_string> → https://arxiv.org/api/query?<query_string>
# This avoids CORS issues and lets us set a proper User-Agent header.

ARXIV_API_BASE = "https://arxiv.org/api/query"
ARXIV_USER_AGENT = "SCQDatabase/1.0 (+https://github.com/pquarterman17/arXivScooper)"

# PatentsView (USPTO) PatentSearch API. The browser/host hits
# /api/patents/<rest> and we forward to PATENTSVIEW_API_BASE/<rest>,
# injecting the X-Api-Key header from the 'patentsview_api_key' secret so
# the key never reaches the client. Overridable via SCQ_PATENTSVIEW_API_BASE
# so a future host relocation (PatentsView→ODP migration) is config, not
# code. See scq/patents/providers/patentsview.py for the request shapes.
PATENTSVIEW_API_BASE = os.environ.get(
    "SCQ_PATENTSVIEW_API_BASE", "https://search.patentsview.org/api/v1"
).rstrip("/")


class SCQHandler(http.server.SimpleHTTPRequestHandler):
    """Serves static files + proxies arXiv API requests."""

    def log_message(self, format, *args):
        # Only log proxy requests, suppress static file noise
        if args and isinstance(args[0], str) and "/api/arxiv" in args[0]:
            print(f"  [proxy] {args[0]}")

    def end_headers(self):
        """Inject no-cache headers for HTML/JS/CSS/JSON files so the
        browser always fetches fresh copies after edits."""
        path = urllib.parse.urlparse(self.path).path
        ext = os.path.splitext(path)[1].lower()
        if ext in NO_CACHE_EXTENSIONS:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/arxiv"):
            self._proxy_arxiv()
        elif self.path.startswith("/api/patents/list"):
            self._handle_patents_list()
        elif self.path.startswith("/api/patents"):
            self._proxy_patents()
        elif self.path.startswith("/api/crossref/search"):
            self._proxy_crossref_search()
        elif self.path.startswith("/api/crossref/"):
            self._proxy_crossref()
        elif self.path.startswith("/api/config/"):
            self._handle_config_get()
        elif self._is_canonical_db_request():
            self._serve_canonical_db()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/bookmarklet":
            self._handle_bookmarklet()
        elif self.path == "/api/upload-pdf":
            self._handle_pdf_upload()
        elif self.path == "/api/save-db":
            self._handle_save_db()
        elif self.path == "/api/patents/add":
            self._handle_patents_add()
        elif self.path.startswith("/api/config/"):
            self._handle_config_post()
        elif self.path == "/api/test/db-path":
            self._handle_test_db_path()
        elif self.path == "/api/test/smtp":
            self._handle_test_smtp()
        elif self.path == "/api/test/digest":
            self._handle_test_digest()
        elif self.path == "/api/secret":
            self._handle_secret_post()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _proxy_arxiv(self):
        """Forward request to arXiv API with proper headers."""
        # Extract query string: /api/arxiv?search_query=... → search_query=...
        parts = urllib.parse.urlparse(self.path)
        qs = parts.query
        if not qs:
            self.send_error(400, "Missing query parameters")
            return

        target = f"{ARXIV_API_BASE}?{qs}"
        req = urllib.request.Request(
            target,
            headers={
                "User-Agent": ARXIV_USER_AGENT,
                "Accept": "application/xml, text/xml, */*",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(resp.status)
                # Forward content type; add CORS for good measure
                ct = resp.headers.get("Content-Type", "application/xml")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            self.send_response(e.code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            msg = f"arXiv API returned {e.code}: {e.reason}"
            if e.code == 429:
                msg += "\nRate limited — wait a moment and try again."
            self.wfile.write(msg.encode())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f"Proxy error: {e}".encode())

    def _proxy_patents(self):
        """Forward request to PatentsView, injecting the X-Api-Key header.

        Path format: /api/patents/<endpoint>?<query> →
        https://search.patentsview.org/api/v1/<endpoint>?<query>
        The API key is read from the 'patentsview_api_key' secret so it
        never has to live in client-side JS. Returns 503 if no key is set.
        """
        from scq.config import secrets as secrets_mod

        api_key = secrets_mod.get("patentsview_api_key")
        if not api_key:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(
                b'{"error": "PatentsView API key not set. Run: '
                b'scq config set-secret patentsview_api_key"}'
            )
            return

        rest = self.path[len("/api/patents") :].lstrip("/")
        target = f"{PATENTSVIEW_API_BASE}/{rest}"
        req = urllib.request.Request(
            target,
            headers={
                "X-Api-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "arXivScooper/1.0 (+https://github.com/pquarterman17/arXivScooper)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(resp.status)
                self.send_header(
                    "Content-Type", resp.headers.get("Content-Type", "application/json")
                )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f'{{"error": "PatentsView returned {e.code}: {e.reason}"}}'.encode())
        except Exception as e:  # noqa: BLE001
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f'{{"error": "Proxy error: {e}"}}'.encode())

    def _handle_patents_add(self):
        """Fetch a patent via a provider and store it. POST {number, source}.

        Runs the same provider+store pipeline as `scq patents fetch
        --process`, server-side, so the browser never needs the network or
        the Python ingest. Default source is 'google' (keyless).
        Returns {ok, patent: {...}} or {ok: false, error}.
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0 or content_length > 8192:
                self._json_response(400, {"ok": False, "error": "Missing or oversized body"})
                return
            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._json_response(400, {"ok": False, "error": f"Invalid JSON: {e}"})
                return

            number = (payload.get("number") or "").strip()
            source = (payload.get("source") or "google").strip()
            if not number:
                self._json_response(400, {"ok": False, "error": "Missing 'number'"})
                return
            if source not in ("google", "patentsview"):
                self._json_response(400, {"ok": False, "error": f"Unknown source: {source}"})
                return

            from scq.db.connection import connect
            from scq.patents import store
            from scq.patents.providers import google, patentsview

            try:
                if source == "patentsview":
                    from scq.config import secrets as _secrets_mod

                    api_key = _secrets_mod.get("patentsview_api_key")
                    patent = patentsview.fetch_patent(number, api_key=api_key)
                else:
                    patent = google.fetch_patent(number)
            except ValueError as e:  # missing key / bad number
                self._json_response(400, {"ok": False, "error": str(e)})
                return
            except LookupError as e:  # no such patent
                self._json_response(404, {"ok": False, "error": str(e)})
                return
            except Exception as e:  # noqa: BLE001 — network/parse
                self._json_response(502, {"ok": False, "error": f"Fetch failed: {e}"})
                return

            conn = connect()
            try:
                store.upsert_patent(conn, patent)
                rec = store.get_patent(conn, patent.number)
            finally:
                conn.close()
            self._json_response(200, {"ok": True, "patent": rec})
        except Exception as e:  # noqa: BLE001
            self._json_response(500, {"ok": False, "error": str(e)})

    def _handle_patents_list(self):
        """List stored patents. GET /api/patents/list?q=<fts>&limit=<n>."""
        try:
            from scq.db.connection import connect
            from scq.patents import store

            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            query = (qs.get("q", [""])[0] or "").strip() or None
            try:
                limit = int(qs.get("limit", ["200"])[0])
            except ValueError:
                limit = 200

            conn = connect()
            try:
                patents = store.list_patents(conn, query=query, limit=limit)
            finally:
                conn.close()
            self._json_response(200, {"ok": True, "patents": patents})
        except Exception as e:  # noqa: BLE001
            self._json_response(500, {"ok": False, "error": str(e)})

    def _proxy_crossref(self):
        """Forward request to CrossRef API with proper headers.
        Path format: /api/crossref/10.1103/PhysRevLett.130.267001
        """
        # Extract DOI from path: /api/crossref/<doi>
        parts = urllib.parse.urlparse(self.path)
        # Remove /api/crossref/ prefix to get the DOI
        doi = parts.path.replace("/api/crossref/", "", 1)

        if not doi:
            self.send_error(400, "Missing DOI")
            return

        target = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        req = urllib.request.Request(
            target,
            headers={
                "User-Agent": "SCQDatabase/1.0 (+https://github.com/pquarterman17/arXivScooper)",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(resp.status)
                ct = resp.headers.get("Content-Type", "application/json")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            error_msg = f'{{"error": "CrossRef API returned {e.code}: {e.reason}"}}'
            self.wfile.write(error_msg.encode())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            error_msg = f'{{"error": "Proxy error: {e}"}}'
            self.wfile.write(error_msg.encode())

    def _proxy_crossref_search(self):
        """Forward keyword search to CrossRef API.
        Path format: /api/crossref/search?query=...&filter=...&rows=...&sort=...&order=...
        Maps to: https://api.crossref.org/works?query=...&filter=...&rows=...&sort=...&order=...
        """
        parts = urllib.parse.urlparse(self.path)
        qs = parts.query
        if not qs:
            self.send_error(400, "Missing query parameters")
            return

        target = f"https://api.crossref.org/works?{qs}"
        req = urllib.request.Request(
            target,
            headers={
                "User-Agent": "SCQDatabase/1.0 (+https://github.com/pquarterman17/arXivScooper)",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(resp.status)
                ct = resp.headers.get("Content-Type", "application/json")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            error_msg = f'{{"error": "CrossRef search returned {e.code}: {e.reason}"}}'
            self.wfile.write(error_msg.encode())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            error_msg = f'{{"error": "Proxy error: {e}"}}'
            self.wfile.write(error_msg.encode())

    def _handle_save_db(self):
        """Persist the in-memory sql.js DB back to data/arxiv_scooper.db.

        The browser POSTs the raw bytes of `db.export()`. We:
          1. Cap the size (defends against runaway uploads)
          2. Verify the SQLite magic header (defends against wrong-type
             bytes being POSTed by accident)
          3. Write to a temp file in the same directory as the target
          4. os.replace() onto the canonical path (atomic on POSIX,
             best-effort on Windows — but still better than open+write
             which can leave a half-written file on crash)

        Returns JSON: {ok, bytes, path, savedAt} or {error}.
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._save_db_error(400, "Empty body")
                return
            if content_length > MAX_DB_UPLOAD_BYTES:
                self._save_db_error(
                    413,
                    f"DB too large ({content_length} bytes; max {MAX_DB_UPLOAD_BYTES})",
                )
                return

            data = self.rfile.read(content_length)
            if len(data) < len(SQLITE_MAGIC) or not data.startswith(SQLITE_MAGIC):
                self._save_db_error(
                    400,
                    "Body is not a SQLite database (magic header mismatch)",
                )
                return

            # Atomic write: temp file in the same dir, then os.replace.
            target = Path(DB_PATH)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".scq_save_",
                suffix=".db",
                dir=str(target.parent),
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp_path, target)
            except Exception:
                # On any failure mid-write, clean up the temp file
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            payload = {
                "ok": True,
                "bytes": len(data),
                "path": str(target),
                "savedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        except Exception as e:  # noqa: BLE001 — keep response well-formed
            self._save_db_error(500, f"Save failed: {e}")

    def _save_db_error(self, status, message):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode())

    def _is_canonical_db_request(self):
        # The browser fetches the DB at /data/<dbname>.db. Since 2026-05-01 the
        # canonical DB has been able to live outside the repo (paths.toml), so
        # the static-file handler can't reliably serve it. Resolve per-request
        # so a paths.toml edit takes effect without restarting the server.
        from scq.config.paths import paths as _paths_resolver

        url_path = urllib.parse.urlparse(self.path).path
        db_path = _paths_resolver(force_reload=True).db_path
        return url_path == f"/data/{Path(db_path).name}"

    def _serve_canonical_db(self):
        from scq.config.paths import paths as _paths_resolver

        db_path = Path(_paths_resolver(force_reload=True).db_path)
        try:
            data = db_path.read_bytes()
        except FileNotFoundError:
            self.send_error(404, f"DB not found at {db_path}")
            return
        except OSError as e:
            self.send_error(500, f"Could not read DB: {e}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.sqlite3")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    # ── Config GET / POST endpoints (plan #11) ──
    #
    # GET  /api/config/<domain> → returns the current resolved value as JSON.
    # POST /api/config/<domain> → validates body against the domain's schema
    #                              and writes to data/user_config/<domain>.json
    #                              (or paths.toml for the bootstrap domain).
    #
    # `paths` is the special domain — it lives in TOML so the resolver can read
    # it before the database (and this server) opens. All other 9 domains use
    # the same JSON pipeline as the JS-side config loader.

    def _handle_config_get(self):
        from scq.config import user as _user_cfg
        from scq.config.paths import paths as _paths_resolver

        domain = self.path.rsplit("/", 1)[-1].split("?")[0]
        try:
            if domain == "paths":
                p = _paths_resolver(force_reload=True)
                payload = {
                    "db_path": str(p.db_path),
                    "papers_dir": str(p.papers_dir),
                    "figures_dir": str(p.figures_dir),
                    "inbox_dir": str(p.inbox_dir),
                    "exports_dir": str(p.exports_dir),
                    "digests_dir": str(p.digests_dir),
                    "references_bib_path": str(p.references_bib_path),
                    "references_txt_path": str(p.references_txt_path),
                }
                self._json_response(200, payload)
                return

            if domain not in _user_cfg.MANIFEST:
                self._json_response(404, {"error": f"unknown config domain '{domain}'"})
                return

            result = _user_cfg.load_config(domain)
            self._json_response(200, result.data)
        except Exception as e:  # noqa: BLE001
            self._json_response(500, {"error": str(e)})

    def _handle_config_post(self):
        from scq.config import user as _user_cfg
        from scq.config.paths import paths as _paths_resolver
        from scq.config.paths import refresh as _paths_refresh

        domain = self.path.rsplit("/", 1)[-1].split("?")[0]
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._json_response(400, {"error": "Empty body"})
                return
            if content_length > 1048576:
                self._json_response(413, {"error": "Body too large"})
                return
            body = self.rfile.read(content_length)
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._json_response(400, {"error": f"Invalid JSON: {e}"})
                return

            repo_root = _paths_resolver(force_reload=True).repo_root

            if domain == "paths":
                # Validate against the paths schema (it's not in MANIFEST so we
                # load + validate manually).
                schema_path = repo_root / "src" / "config" / "schema" / "paths.schema.json"
                with open(schema_path, encoding="utf-8") as f:
                    schema = json.load(f)
                errors = _user_cfg._validate(value, schema)
                if errors:
                    self._json_response(400, {"errors": list(errors)})
                    return
                # Serialize as TOML (single-quoted literal strings preserve
                # backslashes, important for Windows paths).
                toml_path = repo_root / "data" / "user_config" / "paths.toml"
                toml_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(toml_path, _serialize_paths_toml(value))
                _paths_refresh()  # invalidate the lru_cache so subsequent reads see new values
                self._json_response(200, {"ok": True, "path": str(toml_path)})
                return

            if domain not in _user_cfg.MANIFEST:
                self._json_response(404, {"error": f"unknown config domain '{domain}'"})
                return

            # Validate against the domain's schema
            schema_path = repo_root / "src" / "config" / "schema" / f"{domain}.schema.json"
            with open(schema_path, encoding="utf-8") as f:
                schema = json.load(f)
            errors = _user_cfg._validate(value, schema)
            if errors:
                self._json_response(400, {"errors": list(errors)})
                return

            # Write to user_config/<domain>.json atomically
            target = repo_root / "data" / "user_config" / f"{domain}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            self._json_response(200, {"ok": True, "path": str(target)})
        except Exception as e:  # noqa: BLE001
            self._json_response(500, {"error": str(e)})

    def _json_response(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ── Secrets endpoint (plan #11) ──
    #
    # POST /api/secret  body: {name: str, value: str}
    #   Writes a secret to the OS keyring via scq.config.secrets.set.
    #   Validates the name against a small allowlist so a misconfigured
    #   page can't pollute the keyring with arbitrary entries.
    #
    # No GET endpoint by design — secret values must never be served back
    # to the browser. Use `has(name)` semantics through `/api/secret` (POST
    # with empty value? no — clients should ask the user to re-enter).
    # Deletion stays on the CLI (`scq config delete-secret <name>`); the
    # browser can clear a value by setting it to "" if `value` is omitted.
    SECRET_NAME_ALLOWLIST = frozenset(
        {
            "email_app_password",  # SMTP App Password (Gmail or generic)
        }
    )

    def _handle_secret_post(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._json_response(400, {"ok": False, "error": "Empty body"})
                return
            if content_length > 4096:
                self._json_response(413, {"ok": False, "error": "Body too large"})
                return
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._json_response(400, {"ok": False, "error": f"Invalid JSON: {e}"})
                return

            name = payload.get("name")
            value = payload.get("value")
            if not isinstance(name, str) or not name:
                self._json_response(400, {"ok": False, "error": "Missing or invalid 'name'"})
                return
            if name not in self.SECRET_NAME_ALLOWLIST:
                self._json_response(
                    400,
                    {
                        "ok": False,
                        "error": f"Secret name not in allowlist. Allowed: {sorted(self.SECRET_NAME_ALLOWLIST)}",
                    },
                )
                return
            if not isinstance(value, str):
                self._json_response(
                    400, {"ok": False, "error": "Missing or invalid 'value' (must be string)"}
                )
                return

            from scq.config import secrets as _secrets_mod

            try:
                _secrets_mod.set(name, value)
            except _secrets_mod.KeyringUnavailable as e:
                self._json_response(503, {"ok": False, "error": str(e)})
                return
            self._json_response(200, {"ok": True, "name": name})
        except Exception as e:  # noqa: BLE001
            self._json_response(500, {"ok": False, "error": str(e)})

    # ── Test-button endpoints (plan #11) ──
    #
    # Each verifies one side of the user's configuration end-to-end and
    # returns `{ok: true, ...details}` or `{ok: false, error}`. Designed to
    # be cheap and *non-destructive* — `test_smtp` doesn't send anything,
    # `test_digest` sends a single tiny email rather than running the full
    # arxiv pipeline.
    def _handle_test_db_path(self):
        import sqlite3

        from scq.config.paths import paths as _paths_resolver

        try:
            p = _paths_resolver(force_reload=True).db_path
            if not p.exists():
                self._json_response(200, {"ok": False, "error": f"File not found: {p}"})
                return
            size = p.stat().st_size
            try:
                conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            except sqlite3.OperationalError as e:
                self._json_response(200, {"ok": False, "error": f"Open failed: {e}"})
                return
            try:
                # Check magic + papers count. Both fail loudly if the file isn't a valid SQLite paper DB.
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='papers'"
                ).fetchone()
                papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] if row else None
            finally:
                conn.close()
            if papers is None:
                self._json_response(
                    200,
                    {
                        "ok": False,
                        "error": "File exists but has no `papers` table — not an SCQ database.",
                    },
                )
                return
            self._json_response(
                200,
                {
                    "ok": True,
                    "path": str(p),
                    "size": size,
                    "papers": papers,
                },
            )
        except Exception as e:  # noqa: BLE001
            self._json_response(200, {"ok": False, "error": str(e)})

    def _handle_test_smtp(self):
        import smtplib

        try:
            email_cfg, app_password = _load_email_config()
            # Schema keys are camelCase (smtpHost / smtpPort / fromAddress /
            # useTls). #13-audit fixed snake_case lookups that always returned
            # None and silently fell through to gmail-on-465.
            host = email_cfg.get("smtpHost") or "smtp.gmail.com"
            port = int(email_cfg.get("smtpPort") or 587)
            use_tls = email_cfg.get("useTls", True)
            from_addr = email_cfg.get("fromAddress") or os.environ.get("SCQ_EMAIL_FROM", "")
            if not from_addr:
                self._json_response(
                    200,
                    {
                        "ok": False,
                        "error": "No fromAddress set (data/user_config/email.json or SCQ_EMAIL_FROM).",
                    },
                )
                return
            if not app_password:
                self._json_response(
                    200,
                    {
                        "ok": False,
                        "error": "No SMTP app password — `scq config set-secret email_app_password` or set SCQ_EMAIL_APP_PASSWORD.",
                    },
                )
                return
            smtp = _smtp_connect(host, port, use_tls, from_addr, app_password, timeout=10)
            try:
                pass  # connection + login already verified
            finally:
                smtp.quit()
            self._json_response(
                200,
                {
                    "ok": True,
                    "host": host,
                    "port": port,
                    "from": from_addr,
                },
            )
        except smtplib.SMTPAuthenticationError as e:
            self._json_response(
                200,
                {
                    "ok": False,
                    "error": f"Auth failed ({e.smtp_code}): {e.smtp_error.decode(errors='replace') if hasattr(e, 'smtp_error') else e}",
                },
            )
        except Exception as e:  # noqa: BLE001
            self._json_response(200, {"ok": False, "error": str(e)})

    def _handle_test_digest(self):
        from datetime import datetime, timezone
        from email.message import EmailMessage

        try:
            email_cfg, app_password = _load_email_config()
            digest_cfg = _load_digest_config()
            # Schema keys are camelCase; same fix as _handle_test_smtp.
            host = email_cfg.get("smtpHost") or "smtp.gmail.com"
            port = int(email_cfg.get("smtpPort") or 587)
            use_tls = email_cfg.get("useTls", True)
            from_addr = email_cfg.get("fromAddress") or os.environ.get("SCQ_EMAIL_FROM", "")
            recipients = _normalize_recipients(digest_cfg.get("recipients", []))
            if not from_addr or not app_password:
                self._json_response(
                    200, {"ok": False, "error": "Missing email credentials — see Email tab."}
                )
                return
            if not recipients:
                self._json_response(
                    200, {"ok": False, "error": "No active recipients in digest config."}
                )
                return

            msg = EmailMessage()
            msg["From"] = from_addr
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = "[arXivScooper] Test digest"
            msg.set_content(
                "This is a test digest sent from the Settings UI to verify your "
                f"email pipeline.\n\nSent at {datetime.now(timezone.utc).isoformat()} "
                f"from {from_addr} to {len(recipients)} recipient(s).\n\n"
                "If you got this, your SMTP credentials and recipient list are "
                "wired up correctly.\n"
            )
            smtp = _smtp_connect(host, port, use_tls, from_addr, app_password, timeout=15)
            try:
                smtp.send_message(msg)
            finally:
                smtp.quit()

            self._json_response(
                200,
                {
                    "ok": True,
                    "recipients": recipients,
                    "papers": 0,  # this is a stub digest, no papers fetched
                    "from": from_addr,
                },
            )
        except Exception as e:  # noqa: BLE001
            self._json_response(200, {"ok": False, "error": str(e)})

    def _handle_bookmarklet(self):
        """Handle bookmarklet POST requests.

        Receives JSON payload with paper metadata:
          {url, title, arxivId, doi, authors, abstract, source}

        Saves to inbox/bookmarklet_<timestamp>.json for later import.
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 1048576:  # 1 MB limit
                self.send_response(413)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"error": "Payload too large"}')
                return

            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))

            # Create inbox directory if needed (resolved via paths() so the
            # user's user_config/paths.toml override is honored).
            inbox_dir = _scq_paths().inbox_dir
            inbox_dir.mkdir(parents=True, exist_ok=True)

            # Save to timestamped JSON file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"bookmarklet_{timestamp}.json"
            filepath = inbox_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

            # Success response
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            response = {"status": "ok", "message": "Paper queued for import", "file": filename}
            self.wfile.write(json.dumps(response).encode("utf-8"))

        except json.JSONDecodeError:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"error": "Invalid JSON"}')
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            error = {"error": f"Server error: {str(e)}"}
            self.wfile.write(json.dumps(error).encode("utf-8"))

    def _handle_pdf_upload(self):
        """Handle PDF file uploads via drag-and-drop or file picker.

        Expects multipart/form-data with a single file field named 'pdf'.
        Saves the PDF to papers/ directory and returns metadata.
        """
        import re

        try:
            content_type = self.headers.get("Content-Type", "")
            content_length = int(self.headers.get("Content-Length", 0))

            # Sanity check: reject oversized files (>100 MB)
            if content_length > 104857600:
                self.send_response(413)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"error": "File too large (max 100 MB)"}')
                return

            # Parse multipart/form-data
            if "multipart/form-data" not in content_type:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"error": "Expected multipart/form-data"}')
                return

            # Extract boundary from Content-Type header
            boundary_match = re.search(r"boundary=([^;\s]+)", content_type)
            if not boundary_match:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"error": "Missing boundary in Content-Type"}')
                return

            boundary = boundary_match.group(1).strip('"')
            body = self.rfile.read(content_length)

            # Parse the multipart body
            parts = body.split(f"--{boundary}".encode())
            pdf_data = None
            original_filename = "document"

            for part in parts:
                if b"Content-Disposition" not in part:
                    continue

                # Extract filename if present
                filename_match = re.search(rb'filename="([^"]+)"', part)
                if filename_match:
                    original_filename = filename_match.group(1).decode("utf-8", errors="replace")
                    # Extract just the filename without path
                    original_filename = os.path.basename(original_filename)

                # Content after the headers (separated by blank line)
                if b"\r\n\r\n" in part:
                    content_start = part.index(b"\r\n\r\n") + 4
                    content_end = part.rfind(b"\r\n")
                    pdf_data = (
                        part[content_start:content_end]
                        if content_end > content_start
                        else part[content_start:]
                    )
                elif b"\n\n" in part:
                    content_start = part.index(b"\n\n") + 2
                    content_end = part.rfind(b"\n")
                    pdf_data = (
                        part[content_start:content_end]
                        if content_end > content_start
                        else part[content_start:]
                    )

                if pdf_data:
                    break

            if not pdf_data:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"error": "No file data found"}')
                return

            # Sanitize filename
            safe_name = re.sub(r"[^a-zA-Z0-9._\- ]", "_", original_filename)
            if not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"
            base, ext = os.path.splitext(safe_name)
            if len(base.encode("utf-8")) > 200:
                base = base[:200]
            safe_name = base + ext

            # Ensure papers directory exists (resolved via paths()).
            papers_dir = _scq_paths().papers_dir
            papers_dir.mkdir(parents=True, exist_ok=True)

            # Check for duplicate filename
            filepath = papers_dir / safe_name
            counter = 1
            base_name, ext = os.path.splitext(safe_name)
            while filepath.exists():
                safe_name = f"{base_name}_{counter}{ext}"
                filepath = papers_dir / safe_name
                counter += 1

            # Save the PDF
            with open(filepath, "wb") as f:
                f.write(pdf_data)

            # Create metadata entry in inbox (resolved via paths()).
            inbox_dir = _scq_paths().inbox_dir
            inbox_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:-3]
            meta_filename = f"upload_{timestamp}.json"
            meta_filepath = inbox_dir / meta_filename

            metadata = {
                "original_filename": original_filename,
                "saved_filename": safe_name,
                "pdf_path": os.path.join("papers", safe_name),
                "upload_time": datetime.now().isoformat(),
                "file_size": len(pdf_data),
                "status": "awaiting_processing",
            }

            with open(meta_filepath, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

            # Success response
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            response = {
                "status": "ok",
                "filename": safe_name,
                "path": os.path.join("papers", safe_name),
                "size": len(pdf_data),
                "metadata_file": meta_filename,
            }
            self.wfile.write(json.dumps(response).encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            error = {"error": f"Upload failed: {str(e)}"}
            self.wfile.write(json.dumps(error).encode("utf-8"))


def main(argv=None):
    """CLI entry point. Pulls argv, finds a port, binds + serves until Ctrl+C.

    The server static-files everything relative to the *repo root* (paper_*.html,
    src/, data/), not relative to the package install location. Resolve via
    paths().repo_root and chdir there.
    """
    _ensure_console()
    if argv is not None:
        # Reset sys.argv so the existing arg-parsing block sees the passed
        # values. Keeps argv[0] meaningful for any consumers that read it.
        sys.argv = ["scq serve", *argv]
    os.chdir(_scq_paths().repo_root)

    # Determine which pages to open
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    # SCQ_NO_BROWSER=1 makes the server start without opening any tabs.
    # Used by the e2e smoke workflow and useful for headless dev. Goes
    # through _env_truthy so `SCQ_NO_BROWSER=false` is honored as "still
    # open" rather than getting swallowed by Python's bare truthiness.
    if _env_truthy(os.environ.get("SCQ_NO_BROWSER")):
        to_open = []
    elif arg in PAGES:
        to_open = [arg]
    elif arg == "all":
        to_open = list(PAGES.keys())
    else:
        print(f"Unknown page '{arg}'. Options: {', '.join(PAGES.keys())}, all")
        input("Press Enter to exit.")
        sys.exit(1)

    port = find_open_port(PORT)
    server = http.server.HTTPServer(("127.0.0.1", port), SCQHandler)

    base = f"http://localhost:{port}"
    print(f"arXivScooper — serving at {base}")
    print()
    for key in PAGES:
        marker = " *" if key in to_open else ""
        print(f"  {PAGES[key]:30s} {base}/{PAGES[key]}{marker}")
    print()
    print("  * = opening in browser")
    print("  Close this window or Ctrl+C to stop.\n")

    threading.Thread(target=open_tabs, args=(port, to_open), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        print("Stopped.")


if __name__ == "__main__":
    main()
