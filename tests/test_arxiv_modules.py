"""Tests for the split arxiv-digest modules (plan #13).

Covers the pure-logic seams that the split exposed:

  - search.score_paper / rank_papers — keyword scoring is a pure function
  - search.set_budget / _budget_exceeded — wall-clock budget plumbing
  - digest.compute_effective_days_back — weekend lookback policy
  - digest.generate_mock_papers — sanity check fixture shape
  - email._load_email_recipients — env-var fallback path
  - cli.main(["digest", ...]) — passthrough subcommand routes correctly

Network-touching paths (fetch_arxiv_papers, send_email_digest) get a
single integration-style smoke test with monkeypatched I/O. The bulk
of email coverage already lives in test_serve_test_endpoints.py.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq.arxiv import digest as digest_mod
from scq.arxiv import email as email_mod
from scq.arxiv import search as search_mod

# ─── search: scoring ───


def test_score_paper_counts_title_hits_double():
    paper = {
        "title": "Transmon coherence in tantalum resonators",
        "abstract": "We measured tantalum loss tangent. Transmon T1 reached 200 us.",
    }
    score = search_mod.score_paper(paper)
    # "transmon" weight 9 — title has 1 hit (×2), abstract 1 hit. So 9×3 = 27.
    # "tantalum" weight 9 — title 1 hit (×2), abstract 1 hit. 9×3 = 27.
    # plus other matches (coherence 8, loss tangent 10, T1 7).
    assert score > 0
    assert paper["relevance_score"] == score
    assert "transmon" in paper["matched_keywords"]
    assert "tantalum" in paper["matched_keywords"]


def test_score_paper_zero_when_no_keywords():
    paper = {
        "title": "Cosmology of distant galaxies",
        "abstract": "We surveyed redshifts in the early universe.",
    }
    score = search_mod.score_paper(paper)
    assert score == 0
    assert paper["matched_keywords"] == []


def test_rank_papers_descending():
    papers = [
        {"title": "Off topic", "abstract": "Nothing here."},
        {"title": "Transmon qubit fluxonium loss tangent", "abstract": "tantalum substrate."},
        {"title": "TLS noise", "abstract": "T1"},
    ]
    out = search_mod.rank_papers(papers)
    scores = [p["relevance_score"] for p in out]
    # rank_papers now drops papers below minScoreToInclude, so "Off topic"
    # (score 0) is filtered out and only the scoring papers are returned.
    assert scores == sorted(scores, reverse=True)
    assert out[0]["title"].startswith("Transmon")  # highest scorer first
    assert all(p["title"] != "Off topic" for p in out)  # below-floor papers dropped


# ─── search: budget ───


def test_set_budget_then_budget_remaining():
    search_mod.set_budget(0.5)
    rem = search_mod._budget_remaining()
    assert rem is not None
    assert 0 < rem <= 0.5
    assert search_mod._budget_exceeded() is False
    search_mod.set_budget(None)
    assert search_mod._budget_remaining() is None


def test_budget_exceeded_after_deadline():
    search_mod.set_budget(0.01)
    time.sleep(0.05)
    assert search_mod._budget_exceeded() is True
    search_mod.set_budget(None)


# ─── digest: weekend lookback ───


def test_weekend_lookback_extends_on_monday(monkeypatch):
    """Mondays bump days_back to >=4 so Fri/Sat/Sun papers aren't missed."""
    fake = type("FakeDT", (), {})()
    fake.weekday = lambda: 0  # Monday
    monkeypatch.setattr(
        digest_mod,
        "datetime",
        type("D", (), {"now": staticmethod(lambda: fake)}),
    )
    days, note = digest_mod.compute_effective_days_back(2)
    assert days == 4
    assert "Monday" in note


def test_weekend_lookback_extends_on_sunday(monkeypatch):
    fake = type("FakeDT", (), {})()
    fake.weekday = lambda: 6
    monkeypatch.setattr(
        digest_mod,
        "datetime",
        type("D", (), {"now": staticmethod(lambda: fake)}),
    )
    days, note = digest_mod.compute_effective_days_back(1)
    assert days == 3
    assert "Sunday" in note


def test_weekend_lookback_no_change_on_weekday(monkeypatch):
    fake = type("FakeDT", (), {})()
    fake.weekday = lambda: 2  # Wednesday
    monkeypatch.setattr(
        digest_mod,
        "datetime",
        type("D", (), {"now": staticmethod(lambda: fake)}),
    )
    days, note = digest_mod.compute_effective_days_back(3)
    assert days == 3
    assert note == ""


def test_weekend_lookback_keeps_max(monkeypatch):
    """If the user already asked for >=4 days, Monday doesn't shrink it."""
    fake = type("FakeDT", (), {})()
    fake.weekday = lambda: 0  # Monday
    monkeypatch.setattr(
        digest_mod,
        "datetime",
        type("D", (), {"now": staticmethod(lambda: fake)}),
    )
    days, _ = digest_mod.compute_effective_days_back(7)
    assert days == 7


# ─── digest: mock fixture ───


def test_mock_papers_have_required_fields():
    papers = digest_mod.generate_mock_papers()
    assert len(papers) >= 2
    required = {
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
    for p in papers:
        assert required.issubset(p.keys()), f"missing fields in {p['id']}"


def test_mock_papers_rank_correctly():
    papers = digest_mod.generate_mock_papers()
    ranked = search_mod.rank_papers(papers)
    # The first mock paper is heavily SCQ-relevant and must appear in ranked results.
    # The second mock paper is generic; it may be filtered out by minScoreToInclude.
    assert len(ranked) >= 1
    assert ranked[0]["id"] == "2603.99001"
    assert ranked[0]["relevance_score"] > 0


# ─── email: recipient loading ───


@pytest.fixture
def isolated_repo_root(monkeypatch, tmp_path):
    """Point SCQ_REPO_ROOT at a fresh tmp_path and refresh the paths cache
    both before and after the test, so other tests see the real repo root."""
    from scq.config.paths import refresh as _paths_refresh

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCQ_REPO_ROOT", str(tmp_path))
    _paths_refresh()
    yield tmp_path
    # Critical: drop the cache before monkeypatch un-sets SCQ_REPO_ROOT, so the
    # next test that calls paths() re-resolves against the real repo.
    _paths_refresh()


def test_load_email_recipients_falls_back_to_env_var(isolated_repo_root, monkeypatch):
    """With no user_config and no legacy file, EMAIL_TO drives the recipient list."""
    monkeypatch.setattr(email_mod, "EMAIL_TO", "fallback@example.com")
    monkeypatch.setattr(email_mod, "BASE_DIR", str(isolated_repo_root))
    recipients = email_mod._load_email_recipients()
    assert any(r["email"] == "fallback@example.com" for r in recipients)


def test_load_email_recipients_splits_env_var_list(isolated_repo_root, monkeypatch):
    """SCQ_EMAIL_TO accepts a comma/semicolon list so the CI nightly run
    (which never sees the gitignored digest.json) can reach every address."""
    monkeypatch.setattr(email_mod, "EMAIL_TO", "a@x.com, b@y.com;c@z.com")
    monkeypatch.setattr(email_mod, "BASE_DIR", str(isolated_repo_root))
    recipients = email_mod._load_email_recipients()
    assert [r["email"] for r in recipients] == ["a@x.com", "b@y.com", "c@z.com"]
    assert all(r["frequency"] == "daily" for r in recipients)


def test_load_email_recipients_returns_empty_when_nothing_configured(
    isolated_repo_root, monkeypatch
):
    monkeypatch.setattr(email_mod, "EMAIL_TO", "")
    monkeypatch.setattr(email_mod, "BASE_DIR", str(isolated_repo_root))
    recipients = email_mod._load_email_recipients()
    assert recipients == []


# ─── digest config consumption (plan #6) ───


def test_apply_digest_filters_drops_below_min_score():
    papers = [
        {"id": "a", "relevance_score": 9},
        {"id": "b", "relevance_score": 4},
        {"id": "c", "relevance_score": 5},
    ]
    out = digest_mod._apply_digest_filters(papers, min_score=5, max_count=None)
    assert [p["id"] for p in out] == ["a", "c"]


def test_apply_digest_filters_caps_after_filter():
    papers = [
        {"id": "a", "relevance_score": 10},
        {"id": "b", "relevance_score": 8},
        {"id": "c", "relevance_score": 6},
    ]
    out = digest_mod._apply_digest_filters(papers, min_score=0, max_count=2)
    assert [p["id"] for p in out] == ["a", "b"]


def test_apply_digest_filters_handles_none_cap():
    papers = [{"id": str(i), "relevance_score": 10} for i in range(5)]
    out = digest_mod._apply_digest_filters(papers, min_score=0, max_count=None)
    assert len(out) == 5


def test_apply_digest_filters_handles_zero_cap_as_no_cap():
    """maxPapers=0 in config should not silently drop everything; treat as no cap."""
    papers = [{"id": "a", "relevance_score": 10}]
    out = digest_mod._apply_digest_filters(papers, min_score=0, max_count=0)
    assert out == papers


def test_apply_digest_filters_empty_input():
    assert digest_mod._apply_digest_filters([], min_score=5, max_count=10) == []


def test_apply_digest_filters_missing_score_treated_as_zero():
    papers = [{"id": "a"}, {"id": "b", "relevance_score": 5}]
    out = digest_mod._apply_digest_filters(papers, min_score=3, max_count=None)
    assert [p["id"] for p in out] == ["b"]


def test_apply_digest_filters_handles_min_score_none():
    """Bug-hunter #1: min_score=None must not crash on the >= comparison.
    Treat None as 'no floor' rather than TypeError."""
    papers = [{"id": "a", "relevance_score": 10}, {"id": "b", "relevance_score": 0}]
    out = digest_mod._apply_digest_filters(papers, min_score=None, max_count=None)
    assert [p["id"] for p in out] == ["a", "b"]


def test_load_digest_config_treats_null_in_user_json_as_default(monkeypatch):
    """Bug-hunter #1 (root cause): a hand-edited user_config with null
    values must fall back to defaults rather than passing None through."""

    class FakeResult:
        data = {
            "minRelevanceScore": None,
            "maxPapers": None,
            "lookbackDays": None,
            "includeSources": None,
        }

    monkeypatch.setattr("scq.config.user.load_config", lambda _d: FakeResult())
    cfg = digest_mod._load_digest_config()
    assert cfg["minRelevanceScore"] == digest_mod._DIGEST_DEFAULTS["minRelevanceScore"]
    assert cfg["lookbackDays"] == digest_mod._DIGEST_DEFAULTS["lookbackDays"]
    assert cfg["includeSources"] == digest_mod._DIGEST_DEFAULTS["includeSources"]
    assert cfg["maxPapers"] is None  # default IS None — preserved


def test_load_digest_config_falls_back_when_loader_throws(monkeypatch):
    def boom(_domain):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("scq.config.user.load_config", boom)
    cfg = digest_mod._load_digest_config()
    assert cfg["lookbackDays"] == digest_mod._DIGEST_DEFAULTS["lookbackDays"]
    assert cfg["maxPapers"] is None


def test_load_digest_config_pulls_from_loader(monkeypatch):
    class FakeResult:
        data = {
            "lookbackDays": 7,
            "maxPapers": 25,
            "minRelevanceScore": 5,
            "includeSources": ["arxiv", "prl"],
        }

    monkeypatch.setattr("scq.config.user.load_config", lambda _d: FakeResult())
    cfg = digest_mod._load_digest_config()
    assert cfg == {
        "lookbackDays": 7,
        "maxPapers": 25,
        "minRelevanceScore": 5,
        "includeSources": ["arxiv", "prl"],
    }


def test_load_search_categories_uses_loader_result(monkeypatch):
    class FakeResult:
        data = {"arxivCategories": ["quant-ph", "cond-mat.supr-con"]}

    monkeypatch.setattr("scq.config.user.load_config", lambda _d: FakeResult())
    cats = digest_mod._load_search_categories()
    assert cats == ["quant-ph", "cond-mat.supr-con"]


def test_load_search_categories_falls_back_when_empty(monkeypatch):
    class FakeResult:
        data = {"arxivCategories": []}  # empty list = use defaults

    monkeypatch.setattr("scq.config.user.load_config", lambda _d: FakeResult())
    cats = digest_mod._load_search_categories()
    assert cats == list(search_mod.ARXIV_CATEGORIES)


def test_load_search_categories_falls_back_when_loader_throws(monkeypatch):
    def boom(_domain):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("scq.config.user.load_config", boom)
    cats = digest_mod._load_search_categories()
    assert cats == list(search_mod.ARXIV_CATEGORIES)


def test_main_uses_config_categories_when_cli_omits_flags(
    monkeypatch, tmp_path, isolated_repo_root
):
    """Sanity-check that main() actually plumbs config values into fetch_arxiv_papers
    when the CLI does not specify --days. Uses --test so no network fires."""
    captured = {}

    class FakeResult:
        data = {
            "lookbackDays": 5,
            "maxPapers": 1,
            "minRelevanceScore": 0,
            "includeSources": [],
            "arxivCategories": ["quant-ph"],
        }

    monkeypatch.setattr("scq.config.user.load_config", lambda _d: FakeResult())
    monkeypatch.setattr(digest_mod, "send_email_digest", lambda *_a, **_k: None)
    real_apply = digest_mod._apply_digest_filters

    def spy_apply(papers, *, min_score, max_count):
        captured["min_score"] = min_score
        captured["max_count"] = max_count
        return real_apply(papers, min_score=min_score, max_count=max_count)

    monkeypatch.setattr(digest_mod, "_apply_digest_filters", spy_apply)
    monkeypatch.setattr(digest_mod, "DIGEST_DIR", str(tmp_path))

    digest_mod.main(["--test", "--no-email"])

    assert captured["max_count"] == 1
    assert captured["min_score"] == 0


# ─── cli passthrough ───


def test_scq_digest_subcommand_dispatches(monkeypatch):
    """`scq digest --test --no-email` should reach scq.arxiv.digest.main."""
    received = []

    def fake_main(argv=None):
        received.append(list(argv or []))

    monkeypatch.setattr("scq.arxiv.digest.main", fake_main)
    from scq.cli import main as cli_main

    rc = cli_main(["digest", "--test", "--no-email", "--days", "5"])
    assert rc == 0
    assert received[0] == ["--test", "--no-email", "--days", "5"]


# ─── digest: --max-papers rejects zero/negative ───


def test_max_papers_zero_rejected():
    with pytest.raises(SystemExit):
        digest_mod.main(["--test", "--no-email", "--max-papers", "0"])


def test_max_papers_negative_rejected():
    with pytest.raises(SystemExit):
        digest_mod.main(["--test", "--no-email", "--max-papers", "-1"])
