"""RSS fallback source — parsing and the API-failure handoff.

Background: on 2026-09-18 the arXiv Atom API origin returned HTTP 406 to
every request for hours (Fastly masked it for a few cached URLs, which is why
ad-hoc probes sometimes passed while every scheduled digest failed), while
rss.arxiv.org served the same announcements normally. The digest now drops to
RSS rather than emailing nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scq.arxiv import rss as rss_mod
from scq.arxiv import search as arxiv_search


def _rss(entries):
    """Build an arXiv-shaped RSS feed. `entries` are (id, title, authors, cat)."""
    items = []
    for arxiv_id, title, authors, cat, date in entries:
        items.append(
            f"""<item>
  <title>{title}</title>
  <link>https://arxiv.org/abs/{arxiv_id}</link>
  <description>arXiv:{arxiv_id}v1 Announce Type: new
Abstract: A study of {title} and related effects.</description>
  <dc:creator>{authors}</dc:creator>
  <category>{cat}</category>
  <pubDate>{date}</pubDate>
  <guid>oai:arXiv.org:{arxiv_id}v1</guid>
</item>"""
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        "<channel><title>test</title><link>https://arxiv.org</link>"
        "<description>d</description>\n" + "\n".join(items) + "\n</channel></rss>"
    ).encode("utf-8")


TODAY = datetime.now(timezone.utc).strftime("%a, %d %b %Y 00:00:00 +0000")
OLD = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%a, %d %b %Y 00:00:00 +0000")


def test_parses_entries_into_the_standard_paper_shape():
    feed = _rss([("2609.19147", "Tantalum resonators", "A. Smith, B. Jones", "quant-ph", TODAY)])
    papers = rss_mod.fetch_rss_papers(["quant-ph"], fetcher=lambda *_a, **_kw: feed)

    assert len(papers) == 1
    p = papers[0]
    assert p["id"] == "2609.19147"
    assert p["title"] == "Tantalum resonators"
    assert p["authors"] == "A. Smith, B. Jones"
    assert p["short_authors"]
    assert p["abs_url"] == "https://arxiv.org/abs/2609.19147"
    assert p["pdf_url"] == "https://arxiv.org/pdf/2609.19147"
    assert "quant-ph" in p["categories"]


def test_strips_the_announce_type_preamble_from_abstracts():
    """Feed summaries start with 'arXiv:ID Announce Type: new\\nAbstract: ...'."""
    feed = _rss([("2609.00001", "Qubit coherence", "X. Doe", "quant-ph", TODAY)])
    papers = rss_mod.fetch_rss_papers(["quant-ph"], fetcher=lambda *_a, **_kw: feed)

    abstract = papers[0]["abstract"]
    assert abstract.startswith("A study of"), abstract
    assert "Announce Type" not in abstract
    assert not abstract.lower().startswith("abstract:")


def test_drops_entries_older_than_the_window():
    feed = _rss(
        [
            ("2609.00002", "Fresh", "A", "quant-ph", TODAY),
            ("2501.00003", "Stale", "B", "quant-ph", OLD),
        ]
    )
    papers = rss_mod.fetch_rss_papers(["quant-ph"], days_back=2, fetcher=lambda *_a, **_kw: feed)
    assert [p["id"] for p in papers] == ["2609.00002"]


def test_deduplicates_across_categories():
    """A cross-listed paper appears in several feeds; it must be emitted once."""
    feed = _rss([("2609.00004", "Cross-listed", "A", "quant-ph", TODAY)])
    papers = rss_mod.fetch_rss_papers(
        ["quant-ph", "cond-mat.supr-con"], fetcher=lambda *_a, **_kw: feed
    )
    assert len(papers) == 1


def test_one_unreachable_feed_does_not_lose_the_others():
    feed = _rss([("2609.00005", "Survivor", "A", "cond-mat.supr-con", TODAY)])

    def fetcher(url, label, **_kw):
        return None if "quant-ph" in url else feed

    papers = rss_mod.fetch_rss_papers(["quant-ph", "cond-mat.supr-con"], fetcher=fetcher)
    assert [p["id"] for p in papers] == ["2609.00005"]


# ─── handoff from the failing API ──────────────────────────────────


def test_fetch_arxiv_papers_falls_back_to_rss_when_the_api_gives_nothing():
    """The whole point: a 406ing API must not mean an empty digest."""
    feed = _rss([("2609.00006", "Recovered via RSS", "A. Smith", "quant-ph", TODAY)])

    def fake_get(url, label, **_kw):
        return feed if "rss.arxiv.org" in url else None

    with patch.object(arxiv_search, "_arxiv_get", fake_get):
        papers = arxiv_search.fetch_arxiv_papers(["quant-ph"], days_back=3, max_results=10)

    assert [p["id"] for p in papers] == ["2609.00006"]


def test_still_raises_when_both_the_api_and_rss_are_empty():
    """A total outage must still fail loudly rather than mail an empty digest."""
    with patch.object(arxiv_search, "_arxiv_get", lambda *_a, **_kw: None):
        with pytest.raises(arxiv_search.ArxivFetchError):
            arxiv_search.fetch_arxiv_papers(["quant-ph"], days_back=3, max_results=10)


def test_rss_fallback_runs_even_when_the_api_spent_the_whole_budget():
    """Regression for run 149: the fallback must not inherit a spent budget.

    The API burning all 600s is the exact case the fallback exists for, so
    sharing that budget made it abort before issuing a single request.
    """
    feed = _rss([("2609.00007", "Recovered", "A. Smith", "quant-ph", TODAY)])

    def fake_urlopen(req, timeout=None):
        if "rss.arxiv.org" not in req.full_url:
            raise AssertionError("API should not be reached in this test")

        class _Resp:
            status = 200
            headers = {}

            def read(self):
                return feed

        return _Resp()

    try:
        arxiv_search.set_budget(0)  # budget already exhausted
        with patch.object(arxiv_search.urllib.request, "urlopen", fake_urlopen):
            papers = arxiv_search.fetch_arxiv_papers(["quant-ph"], days_back=3, max_results=10)
    finally:
        arxiv_search.set_budget(None)

    assert [p["id"] for p in papers] == ["2609.00007"]


def test_reserve_budget_restores_the_outer_deadline():
    """The extension is scoped: it must not leak past the fallback."""
    arxiv_search.set_budget(0)
    spent = arxiv_search._budget_remaining()
    with arxiv_search._reserve_budget(60):
        assert arxiv_search._budget_remaining() > 30
    assert arxiv_search._budget_remaining() <= spent
    arxiv_search.set_budget(None)
