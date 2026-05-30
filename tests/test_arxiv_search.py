"""Plan #14 — arXiv-search query construction + HTTP client tests.

Complements ``test_arxiv_modules.py`` which already covers the scoring /
ranking / budget half of ``scq.arxiv.search``. This file focuses on the
parts that talk to the arXiv API:

  - URL + query-string construction in ``fetch_arxiv_papers``
  - Combined OR-query vs. per-category fallback path
  - User-Agent header in ``_arxiv_get``
  - 429 / 5xx retry behaviour with mocked Retry-After
  - Timeout + URLError retries
  - Date-cutoff filtering and version-suffix stripping

All mocked at the ``urllib.request.urlopen`` boundary so no real network
calls fire. The Atom XML payloads are minimal — just enough structure for
``ET.fromstring`` to parse and ``findall("atom:entry")`` to yield the
right number of entries.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from scq.arxiv import search as arxiv_search

# ─── Atom payload helpers ──────────────────────────────────────────


def _atom_response(entries: list[dict]) -> bytes:
    """Build a minimal Atom feed with the given entries.

    Each entry dict accepts: id (arxiv id), title, summary, authors,
    categories, published. Defaults fill in the gaps.
    """
    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append(
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
    )
    for e in entries:
        arxiv_id = e.get("id", "2401.00001")
        title = e.get("title", "Test paper")
        summary = e.get("summary", "abstract goes here")
        published = e.get("published", "2099-01-01T00:00:00Z")
        authors = e.get("authors", ["Alice"])
        categories = e.get("categories", ["quant-ph"])
        pdf_url = e.get("pdf_url", f"http://arxiv.org/pdf/{arxiv_id}")

        parts.append("<entry>")
        parts.append(f"<id>http://arxiv.org/abs/{arxiv_id}</id>")
        parts.append(f"<title>{title}</title>")
        parts.append(f"<summary>{summary}</summary>")
        parts.append(f"<published>{published}</published>")
        for a in authors:
            parts.append(f"<author><name>{a}</name></author>")
        for c in categories:
            parts.append(f'<category term="{c}"/>')
        parts.append(f'<link title="pdf" href="{pdf_url}"/>')
        parts.append("</entry>")
    parts.append("</feed>")
    return "\n".join(parts).encode("utf-8")


def _make_urlopen_mock(payload: bytes):
    """Build a urlopen replacement that returns the given payload once."""

    def fake_urlopen(req, timeout=None):
        resp = MagicMock()
        resp.read.return_value = payload
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda self, *a: None
        return resp

    return fake_urlopen


# ─── Auto-reset side effects ───────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_budget_between_tests():
    """Defang ``set_budget`` cross-test so a stuck deadline can't fail others."""
    arxiv_search.set_budget(None)
    yield
    arxiv_search.set_budget(None)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """``time.sleep`` is invoked in retry paths — never sleep in tests."""
    monkeypatch.setattr(arxiv_search.time, "sleep", lambda *_a, **_kw: None)


# ─── _arxiv_get: header + retries ──────────────────────────────────


def test_arxiv_get_sends_repo_url_user_agent():
    """User-Agent must include the GitHub repo URL (privacy / etiquette)."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.read.return_value = _atom_response([])
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        arxiv_search._arxiv_get("http://example.test/atom", "test")

    ua = captured["headers"].get("User-agent") or captured["headers"].get("User-Agent")
    assert ua is not None, f"No User-Agent header in request: {captured['headers']}"
    assert "github.com/pquarterman17/arXivScooper" in ua


def test_arxiv_get_retries_on_429_and_eventually_returns_payload():
    """First call returns 429 with Retry-After; second call succeeds."""
    payload = _atom_response([])
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise urllib.error.HTTPError(
                req.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "1"},
                io.BytesIO(b""),
            )
        resp = MagicMock()
        resp.read.return_value = payload
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        result = arxiv_search._arxiv_get("http://example.test/atom", "test")

    assert call_count["n"] == 2
    assert result == payload


def test_arxiv_get_retries_on_500_then_gives_up():
    """3 attempts, all 503 → returns None (not raises)."""

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url,
            503,
            "Service Unavailable",
            {},
            io.BytesIO(b""),
        )

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        result = arxiv_search._arxiv_get("http://example.test/atom", "test", max_retries=3)

    assert result is None


def test_arxiv_get_does_not_retry_on_404():
    """4xx other than 429 should fail immediately — bug surface, not flakiness."""
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        raise urllib.error.HTTPError(
            req.full_url,
            404,
            "Not Found",
            {},
            io.BytesIO(b""),
        )

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        result = arxiv_search._arxiv_get("http://example.test/atom", "test", max_retries=3)

    assert call_count["n"] == 1, "404 should not retry"
    assert result is None


def test_arxiv_get_returns_none_when_budget_exhausted_before_first_attempt():
    """Wall-clock budget kills the request before any urlopen call."""
    # Set a deadline already in the past
    arxiv_search.set_budget(-1)

    def fake_urlopen(req, timeout=None):  # pragma: no cover — must not be called
        raise AssertionError("urlopen should not have been called")

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        result = arxiv_search._arxiv_get("http://example.test/atom", "test")

    assert result is None


# ─── fetch_arxiv_papers: query construction ────────────────────────


def test_combined_query_ors_categories_in_one_request():
    """When the combined query succeeds, only ONE request fires."""
    captured_urls = []

    def fake_urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        resp = MagicMock()
        resp.read.return_value = _atom_response([])
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        arxiv_search.fetch_arxiv_papers(
            ["quant-ph", "cond-mat.supr-con", "cond-mat.mtrl-sci"],
            days_back=1,
            max_results=200,
        )

    assert len(captured_urls) == 1, f"Expected 1 request, got {len(captured_urls)}"
    parsed = urllib.parse.urlparse(captured_urls[0])
    qs = urllib.parse.parse_qs(parsed.query)
    # The OR'd combined query
    assert qs["search_query"] == ["cat:quant-ph OR cat:cond-mat.supr-con OR cat:cond-mat.mtrl-sci"]
    assert qs["sortBy"] == ["submittedDate"]
    assert qs["sortOrder"] == ["descending"]


def test_combined_query_max_results_scales_with_category_count():
    """combined_max = max(max_results, max_results*N//2, 1000) — verify the floor."""
    captured_urls = []

    def fake_urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        resp = MagicMock()
        resp.read.return_value = _atom_response([])
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        # 5 categories, 100 max_results → max(100, 250, 1000) = 1000
        arxiv_search.fetch_arxiv_papers(["a", "b", "c", "d", "e"], max_results=100)

    qs = urllib.parse.parse_qs(urllib.parse.urlparse(captured_urls[0]).query)
    assert qs["max_results"] == ["1000"]


def test_falls_back_to_per_category_when_combined_query_fails():
    """When combined returns garbage XML, each category gets its own request."""
    captured_urls = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        captured_urls.append(req.full_url)
        resp = MagicMock()
        # First call: invalid XML to trigger fallback
        if calls["n"] == 1:
            resp.read.return_value = b"<not-valid-xml>"
        else:
            resp.read.return_value = _atom_response([])
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        arxiv_search.fetch_arxiv_papers(["quant-ph", "cond-mat.supr-con"])

    # 1 combined + 2 per-category = 3 requests
    assert calls["n"] == 3
    # Per-category URLs should NOT contain "OR"
    per_cat_qs = [urllib.parse.parse_qs(urllib.parse.urlparse(u).query) for u in captured_urls[1:]]
    assert per_cat_qs[0]["search_query"] == ["cat:quant-ph"]
    assert per_cat_qs[1]["search_query"] == ["cat:cond-mat.supr-con"]


def test_filters_papers_older_than_cutoff():
    """A paper published before now-days_back should be dropped."""
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    stale = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")

    payload = _atom_response(
        [
            {"id": "2099.00001", "published": fresh},
            {"id": "2099.00002", "published": stale},
        ]
    )

    with patch.object(arxiv_search.urllib.request, "urlopen", _make_urlopen_mock(payload)):
        papers = arxiv_search.fetch_arxiv_papers(["quant-ph"], days_back=1)

    ids = [p["id"] for p in papers]
    assert "2099.00001" in ids
    assert "2099.00002" not in ids


def test_dedupes_papers_by_arxiv_id_across_per_category_responses():
    """Same paper returned in two category responses appears once in the result."""
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    payload = _atom_response(
        [
            {"id": "2099.00001", "published": fresh},
        ]
    )

    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        resp = MagicMock()
        # First (combined) returns invalid → fallback
        if calls["n"] == 1:
            resp.read.return_value = b"<not-valid-xml>"
        else:
            # Each per-category returns the same paper
            resp.read.return_value = payload
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        papers = arxiv_search.fetch_arxiv_papers(
            ["quant-ph", "cond-mat.supr-con"],
            days_back=1,
        )

    ids = [p["id"] for p in papers]
    assert ids.count("2099.00001") == 1


def test_strips_version_suffix_from_arxiv_id():
    """An id like '2099.00001v3' stored as '2099.00001'."""
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    payload = _atom_response([{"id": "2099.00001v3", "published": fresh}])

    with patch.object(arxiv_search.urllib.request, "urlopen", _make_urlopen_mock(payload)):
        papers = arxiv_search.fetch_arxiv_papers(["quant-ph"], days_back=1)

    assert len(papers) == 1
    assert papers[0]["id"] == "2099.00001"


def test_paper_dict_has_canonical_shape():
    """Each returned paper dict has id, title, authors, short_authors,
    abstract, published, categories, pdf_url, abs_url."""
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    payload = _atom_response(
        [
            {
                "id": "2099.00001",
                "title": "A title",
                "summary": "An abstract",
                "authors": ["Alice Smith", "Bob Jones"],
                "categories": ["quant-ph", "cond-mat.supr-con"],
                "published": fresh,
                "pdf_url": "http://arxiv.org/pdf/2099.00001",
            }
        ]
    )

    with patch.object(arxiv_search.urllib.request, "urlopen", _make_urlopen_mock(payload)):
        papers = arxiv_search.fetch_arxiv_papers(["quant-ph"], days_back=1)

    p = papers[0]
    expected_keys = {
        "id",
        "title",
        "authors",
        "short_authors",
        "abstract",
        "published",
        "categories",
        "pdf_url",
        "abs_url",
    }
    assert set(p.keys()) == expected_keys
    assert p["id"] == "2099.00001"
    assert p["title"] == "A title"
    assert p["authors"] == "Alice Smith, Bob Jones"  # joined
    # NB: scq.arxiv.search._make_short_authors uses '&' for 2 authors
    # ("Smith & Jones") and 'et al.' only for 3+. This is intentional
    # divergence from scq.ingest.process.short_author.
    assert p["short_authors"] == "Smith & Jones"
    assert p["abstract"] == "An abstract"
    assert "quant-ph" in p["categories"]
    assert p["abs_url"] == "https://arxiv.org/abs/2099.00001"


def test_raises_fetch_error_when_combined_and_all_per_category_fail():
    """All requests return unparseable XML → ArxivFetchError, not [].

    Getting bytes we cannot parse means we have ZERO usable information
    about what papers exist — a fetch *failure*, indistinguishable in
    outcome from a network outage. It must NOT be reported as "no new
    papers today" (which previously made the digest mail an empty email
    on a transient arXiv hiccup, then re-surface the missed papers the
    next day). See scq.arxiv.digest.main's ArxivFetchError handling.
    """

    def fake_urlopen(req, timeout=None):
        resp = MagicMock()
        resp.read.return_value = b"<not-xml>"
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
        with pytest.raises(arxiv_search.ArxivFetchError):
            arxiv_search.fetch_arxiv_papers(["quant-ph"], days_back=1)


def test_valid_empty_feed_returns_empty_list_without_raising():
    """A successful response with zero entries is a genuine empty result.

    arXiv answered and the feed parsed — it simply had nothing new in the
    window. This must return [] (not raise) so a real quiet day flows
    through normally and the digest skips the email rather than failing.
    """
    with patch.object(
        arxiv_search.urllib.request, "urlopen", _make_urlopen_mock(_atom_response([]))
    ):
        papers = arxiv_search.fetch_arxiv_papers(["quant-ph"], days_back=1)

    assert papers == []
