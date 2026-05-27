"""Tests for the patents package (plan: patent-scraping Phase 1).

Covers the pure seams — number parsing, provider response parsing,
summary prompt/parse — plus a store roundtrip on an in-memory DB and the
CLI argument routing. No test touches the network: the provider's HTTP
leg is injected with a stub returning captured-shaped JSON.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq.db.migrations import apply_pending
from scq.patents import cli as patents_cli
from scq.patents import relevance as patent_relevance
from scq.patents import store, summarize
from scq.patents.normalize import Patent, parse_patent_number, split_independent
from scq.patents.providers import patentsview

# ─── number parsing ───


@pytest.mark.parametrize(
    "raw,canonical,country,kind",
    [
        ("US10,374,134 B2", "US10374134B2", "US", "B2"),
        ("US 10374134B2", "US10374134B2", "US", "B2"),
        ("10374134", "US10374134", "US", ""),
        ("EP1234567A1", "EP1234567A1", "EP", "A1"),
    ],
)
def test_parse_patent_number(raw, canonical, country, kind):
    info = parse_patent_number(raw)
    assert info["canonical"] == canonical
    assert info["country"] == country
    assert info["kind_code"] == kind


def test_parse_application_number_flags_application():
    info = parse_patent_number("US2020/0012345A1")
    assert info["is_application"] is True
    assert info["doc_number"] == "20200012345"


def test_parse_patent_number_rejects_empty():
    with pytest.raises(ValueError):
        parse_patent_number("   ")


def test_parse_patent_number_rejects_no_digits():
    with pytest.raises(ValueError):
        parse_patent_number("USABC")


# ─── independent-claim heuristic ───


def test_split_independent_uses_flag_then_heuristic():
    claims = [
        {
            "num": 1,
            "text": "A superconducting qubit comprising a tantalum pad.",
            "is_independent": False,
        },
        {
            "num": 2,
            "text": "The qubit of claim 1 wherein the pad is alpha-phase.",
            "is_independent": False,
        },
    ]
    # Neither flagged independent → heuristic: claim 2 references "claim 1".
    indep = split_independent(claims)
    assert claims[0]["text"] in indep
    assert claims[1]["text"] not in indep


# ─── Patent dataclass derived props ───


def test_independent_claims_falls_back_to_claim_one():
    p = Patent(number="US1", claims=[{"num": 1, "text": "A widget.", "is_independent": False}])
    assert p.independent_claims == ["A widget."]


def test_short_inventors():
    assert Patent(number="US1", inventors=["Jay Gambetta"]).short_inventors == "Gambetta"
    assert Patent(number="US1", inventors=["A Smith", "B Jones"]).short_inventors == "Smith & Jones"
    assert Patent(number="US1", inventors=["A X", "B Y", "C Z"]).short_inventors == "X et al."


# ─── provider parsing ───

_PATENT_PAYLOAD = {
    "patents": [
        {
            "patent_id": "10374134",
            "patent_title": "Superconducting qubit with tantalum",
            "patent_abstract": "A qubit comprising a tantalum capacitor pad.",
            "patent_date": "2019-08-06",
            "patent_earliest_application_date": "2017-01-10",
            "assignees": [{"assignee_organization": "International Business Machines"}],
            "inventors": [
                {"inventor_name_first": "Jay", "inventor_name_last": "Gambetta"},
                {"inventor_name_first": "Jerry", "inventor_name_last": "Chow"},
            ],
            "cpc_current": [
                {"cpc_group_id": "H10N60/12", "cpc_subclass_id": "H10N60"},
                {"cpc_group_id": "G06N10/40", "cpc_subclass_id": "G06N10"},
            ],
        }
    ]
}

_CLAIMS_PAYLOAD = {
    "g_claims": [
        {
            "patent_id": "10374134",
            "claim_sequence": 0,
            "claim_text": "A superconducting qubit comprising a tantalum pad.",
            "claim_dependent": None,
        },
        {
            "patent_id": "10374134",
            "claim_sequence": 1,
            "claim_text": "The qubit of claim 1 wherein the pad is alpha-phase.",
            "claim_dependent": 1,
        },
    ]
}


def test_parse_patent_response():
    info = parse_patent_number("US10374134B2")
    p = patentsview.parse_patent_response(_PATENT_PAYLOAD, number_info=info)
    assert p.title == "Superconducting qubit with tantalum"
    assert p.assignee == "International Business Machines"
    assert p.inventors == ["Jay Gambetta", "Jerry Chow"]
    assert "H10N60/12" in p.cpc_codes
    assert p.grant_date == "2019-08-06"
    assert p.source == "patentsview"


def test_parse_claims_response_marks_independence():
    claims = patentsview.parse_claims_response(_CLAIMS_PAYLOAD)
    assert len(claims) == 2
    assert claims[0]["is_independent"] is True
    assert claims[1]["is_independent"] is False


def test_fetch_patent_with_injected_http():
    calls = []

    def fake_http(url, headers):
        calls.append(url)
        assert headers["X-Api-Key"] == "KEY"
        return _CLAIMS_PAYLOAD if "g_claim" in url else _PATENT_PAYLOAD

    p = patentsview.fetch_patent("US10374134B2", api_key="KEY", http=fake_http)
    assert p.number == "US10374134B2"
    assert len(p.claims) == 2
    assert p.independent_claims == ["A superconducting qubit comprising a tantalum pad."]
    assert len(calls) == 2  # patent + claims endpoints


def test_api_base_default_and_override(monkeypatch):
    monkeypatch.delenv("SCQ_PATENTSVIEW_API_BASE", raising=False)
    url, _ = patentsview.build_patent_request("10374134", "KEY")
    assert url.startswith("https://search.patentsview.org/api/v1/patent/")

    monkeypatch.setenv("SCQ_PATENTSVIEW_API_BASE", "https://api.uspto.gov/v1/")
    url2, _ = patentsview.build_claims_request("10374134", "KEY")
    # Trailing slash is stripped; host swap takes effect at call time.
    assert url2.startswith("https://api.uspto.gov/v1/g_claim/")


def test_fetch_patent_requires_api_key():
    with pytest.raises(ValueError):
        patentsview.fetch_patent("US10374134B2", api_key="")


def test_fetch_patent_raises_on_empty_result():
    with pytest.raises(LookupError):
        patentsview.fetch_patent("US999B2", api_key="KEY", http=lambda u, h: {"patents": []})


# ─── Google Patents provider (HTML scrape) ───

from scq.patents.providers import google  # noqa: E402

# Markup mirrors a real patents.google.com page (validated against US6285999
# on 2026-05-26): date schemes are "dateSubmitted"/"issue", and CPC codes
# render as <span itemprop="Code"> with the full hierarchy listed.
_GOOGLE_HTML = """<html><head>
<meta name="DC.title" content="Superconducting qubit with tantalum">
<meta scheme="inventor" name="DC.contributor" content="Jay Gambetta">
<meta scheme="inventor" name="DC.contributor" content="Jerry Chow">
<meta scheme="assignee" name="DC.contributor" content="International Business Machines">
<meta scheme="dateSubmitted" name="DC.date" content="2017-01-10">
<meta scheme="issue" name="DC.date" content="2019-08-06">
<meta name="DC.description" content="A qubit comprising a tantalum capacitor pad.">
</head><body>
<ul><li><span itemprop="Code">H</span></li>
<li><span itemprop="Code">H10</span></li>
<li><span itemprop="Code">H10N60/12</span></li>
<li><span itemprop="Code">G06N10/40</span></li></ul>
<section itemprop="claims"><div class="claim">1. A superconducting qubit comprising a tantalum pad.
2. The qubit of claim 1 wherein the pad is alpha-phase.</div></section>
</body></html>"""


def test_google_build_request_uses_canonical_number():
    url, headers = google.build_request("10,374,134 B2")
    assert url == "https://patents.google.com/patent/US10374134B2/en"
    assert "User-Agent" in headers


def test_google_parse_html_biblio():
    info = parse_patent_number("US10374134B2")
    p = google.parse_html(_GOOGLE_HTML, number_info=info)
    assert p.title == "Superconducting qubit with tantalum"
    assert p.inventors == ["Jay Gambetta", "Jerry Chow"]
    assert p.assignee == "International Business Machines"
    assert p.filing_date == "2017-01-10"
    assert p.grant_date == "2019-08-06"
    assert p.abstract.startswith("A qubit comprising")
    assert p.source == "google"


def test_google_parse_html_extracts_full_cpc_codes():
    info = parse_patent_number("US10374134B2")
    p = google.parse_html(_GOOGLE_HTML, number_info=info)
    # Only full leaf codes (with a slash); the bare "H"/"H10" hierarchy rows
    # are skipped.
    assert p.cpc_codes == ["H10N60/12", "G06N10/40"]


def test_google_parse_html_claims_and_independence():
    info = parse_patent_number("US10374134B2")
    p = google.parse_html(_GOOGLE_HTML, number_info=info)
    assert len(p.claims) == 2
    assert p.claims[0]["num"] == 1
    # Claim 1 is independent; claim 2 references "claim 1".
    assert p.independent_claims == ["A superconducting qubit comprising a tantalum pad."]


def test_google_fetch_patent_with_injected_http():
    p = google.fetch_patent("US10374134B2", http=lambda u, h: _GOOGLE_HTML)
    assert p.number == "US10374134B2"
    assert p.assignee == "International Business Machines"


def test_google_fetch_patent_raises_when_unparseable():
    with pytest.raises(LookupError):
        google.fetch_patent("US10374134B2", http=lambda u, h: "<html><body>nope</body></html>")


def test_google_fetch_translates_404_to_lookup_error():
    import urllib.error

    def http_404(url, headers):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    with pytest.raises(LookupError):
        google.fetch_patent("US00000000B2", http=http_404)


def test_google_fetch_ignores_api_key_kwarg():
    # The CLI passes provider-agnostic kwargs; google must tolerate api_key.
    p = google.fetch_patent("US10374134B2", http=lambda u, h: _GOOGLE_HTML, fetch_claims=True)
    assert p.title


# ─── relevance scoring (CPC + assignee + keywords) ───

_REL_CFG = {
    "titleMultiplier": 2.0,
    "minScoreToInclude": 5,
    "effectiveKeywords": {"transmon": 9.0, "tantalum": 8.0},
    "keywordToProfiles": {"transmon": ["coherence"], "tantalum": ["materials"]},
    "cpcBoosts": {"G06N10": 10.0, "H10N60": 9.0},
    "assigneeBoosts": {"International Business Machines": 6.0},
}


def test_score_patent_combines_keywords_cpc_assignee():
    p = {
        "title": "Transmon qubit",
        "abstract": "A tantalum pad reduces loss.",
        "claims": [{"text": "A transmon comprising tantalum."}],
        "cpc_codes": ["G06N10/40", "H10N60/12"],
        "assignee": "International Business Machines Corp",
    }
    score = patent_relevance.score_patent(p, _REL_CFG)
    # CPC: G06N10 (+10) + H10N60 (+9) = 19; assignee +6; plus keyword hits.
    assert score > 25
    assert p["matched_cpc"] == ["G06N10", "H10N60"]
    assert p["matched_assignees"] == ["International Business Machines"]
    assert "transmon" in p["matched_keywords"]
    assert "tantalum" in p["matched_keywords"]
    assert p["relevance_score"] == max(score, 0)


def test_score_patent_cpc_prefix_match_counts_once():
    p = {"title": "x", "abstract": "", "claims": [], "cpc_codes": ["G06N10/40", "G06N10/60"]}
    patent_relevance.score_patent(p, _REL_CFG)
    # Two codes share the G06N10 prefix → the prefix boost counts once.
    assert p["matched_cpc"] == ["G06N10"]
    assert p["relevance_score"] == 10.0


def test_score_patent_no_signals_is_zero():
    p = {"title": "Unrelated widget", "abstract": "", "claims": [], "cpc_codes": ["A01B1/00"], "assignee": "Acme"}
    assert patent_relevance.score_patent(p, _REL_CFG) == 0
    assert p["matched_cpc"] == []


def test_score_patent_keywords_use_word_boundaries():
    # "MBE" must NOT match inside "number"; "TiN" must NOT match "destination".
    cfg = {
        "titleMultiplier": 2.0,
        "minScoreToInclude": 5,
        "effectiveKeywords": {"MBE": 8.0, "TiN": 8.0},
        "keywordToProfiles": {},
        "cpcBoosts": {},
        "assigneeBoosts": {},
    }
    p = {
        "title": "Method for node ranking",
        "abstract": "A large number of documents at a destination.",
        "claims": [],
        "cpc_codes": [],
    }
    assert patent_relevance.score_patent(p, cfg) == 0
    assert p["matched_keywords"] == []
    # But a real whole-word hit still scores.
    p2 = {"title": "MBE growth of TiN films", "abstract": "", "claims": [], "cpc_codes": []}
    patent_relevance.score_patent(p2, cfg)
    assert set(p2["matched_keywords"]) == {"MBE", "TiN"}


def test_score_patent_claims_as_strings():
    p = {"title": "", "abstract": "", "claims": ["A transmon device."], "cpc_codes": []}
    patent_relevance.score_patent(p, _REL_CFG)
    assert "transmon" in p["matched_keywords"]


def test_effective_config_surfaces_cpc_and_assignee_boosts():
    from scq.arxiv.search import _build_effective_config

    eff = _build_effective_config(
        {
            "titleMultiplier": 2.0,
            "minScoreToInclude": 5,
            "cpcBoosts": {"G06N10": 10},
            "assigneeBoosts": {"Google": 5},
            "profiles": {},
        }
    )
    assert eff["cpcBoosts"] == {"G06N10": 10.0}
    assert eff["assigneeBoosts"] == {"Google": 5.0}


# ─── store roundtrip ───


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    apply_pending(c)
    yield c
    c.close()


def _sample_patent() -> Patent:
    info = parse_patent_number("US10374134B2")
    p = patentsview.parse_patent_response(_PATENT_PAYLOAD, number_info=info)
    patentsview.merge_claims(p, patentsview.parse_claims_response(_CLAIMS_PAYLOAD))
    return p


def test_upsert_and_get(conn):
    p = _sample_patent()
    store.upsert_patent(conn, p)
    rec = store.get_patent(conn, "US10374134B2")
    assert rec is not None
    assert rec["assignee"] == "International Business Machines"
    assert isinstance(rec["claims"], list) and len(rec["claims"]) == 2
    assert isinstance(rec["cpc_codes"], list)
    assert rec["plain_summary"] == ""  # not summarized yet


def test_upsert_is_idempotent_and_preserves_summary(conn):
    p = _sample_patent()
    store.upsert_patent(conn, p)
    store.store_summary(conn, p.number, plain_summary="A tantalum transmon.")
    # Re-fetch (e.g. provider refresh) must not wipe the summary.
    store.upsert_patent(conn, p)
    rec = store.get_patent(conn, p.number)
    assert rec["plain_summary"] == "A tantalum transmon."
    assert conn.execute("SELECT COUNT(*) FROM patents").fetchone()[0] == 1


def test_store_summary_partial(conn):
    store.upsert_patent(conn, _sample_patent())
    assert store.store_summary(conn, "US10374134B2", protected_scope="Covers X.") is True
    rec = store.get_patent(conn, "US10374134B2")
    assert rec["protected_scope"] == "Covers X."
    assert rec["prior_art_note"] == ""


def test_store_summary_noop_returns_false(conn):
    store.upsert_patent(conn, _sample_patent())
    assert store.store_summary(conn, "US10374134B2") is False


def test_list_patents_returns_summary_lite_rows(conn):
    store.upsert_patent(conn, _sample_patent())
    rows = store.list_patents(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["number"] == "US10374134B2"
    assert r["has_summary"] is False
    assert isinstance(r["cpc_codes"], list)
    assert "claims" not in r  # heavy columns omitted from list payload


def test_list_patents_fts_query(conn):
    store.upsert_patent(conn, _sample_patent())
    assert len(store.list_patents(conn, query="tantalum")) == 1
    assert store.list_patents(conn, query="graphene") == []


def test_list_patents_reflects_summary(conn):
    store.upsert_patent(conn, _sample_patent())
    store.store_summary(conn, "US10374134B2", plain_summary="A tantalum transmon.")
    assert store.list_patents(conn)[0]["has_summary"] is True


def test_fts_search_finds_patent(conn):
    store.upsert_patent(conn, _sample_patent())
    hits = conn.execute(
        "SELECT number FROM patents_fts WHERE patents_fts MATCH 'tantalum'"
    ).fetchall()
    assert ("US10374134B2",) in hits


# ─── summarize helpers ───


def test_build_summary_prompt_surfaces_independent_claims(conn):
    store.upsert_patent(conn, _sample_patent())
    rec = store.get_patent(conn, "US10374134B2")
    prompt = summarize.build_summary_prompt(rec)
    assert "tantalum pad" in prompt
    assert "INDEPENDENT CLAIMS (1)" in prompt
    assert "1 dependent claim" in prompt


def test_parse_summary_response_handles_fenced_json():
    reply = '```json\n{"plain_summary": "Does X.", "protected_scope": "Covers Y.", "prior_art_note": "Builds on Z."}\n```'
    out = summarize.parse_summary_response(reply)
    assert out == {
        "plain_summary": "Does X.",
        "protected_scope": "Covers Y.",
        "prior_art_note": "Builds on Z.",
    }


def test_parse_summary_response_ignores_unknown_keys():
    out = summarize.parse_summary_response('{"plain_summary": "X", "bogus": 1}')
    assert out == {"plain_summary": "X"}


def test_parse_summary_response_raises_without_json():
    with pytest.raises(ValueError):
        summarize.parse_summary_response("no json here")


def test_summarize_patent_with_injected_llm():
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return '{"plain_summary": "Does X.", "protected_scope": "Covers Y.", "prior_art_note": "Builds on Z."}'

    rec = {"number": "US1", "title": "T", "independent_claims": ["A widget."], "claims": [{}]}
    out = summarize.summarize_patent(rec, fake_llm)
    assert "A widget." in captured["prompt"]  # prompt built from the patent
    assert out["plain_summary"] == "Does X."
    assert out["prior_art_note"] == "Builds on Z."


def test_cli_summarize_print_prompt(db_file, capsys):
    c = db_file()
    store.upsert_patent(c, _sample_patent())
    c.close()
    rc = patents_cli.main(["summarize", "US10374134B2", "--print-prompt"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "INDEPENDENT CLAIMS" in out


def test_cli_summarize_no_llm_falls_back_to_prompt(db_file, monkeypatch, capsys):
    c = db_file()
    store.upsert_patent(c, _sample_patent())
    c.close()
    # No LLM available → return code 2 and the prompt is printed for manual use.
    monkeypatch.setattr(patents_cli, "_anthropic_llm", lambda model: None, raising=True)
    rc = patents_cli.main(["summarize", "US10374134B2"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "INDEPENDENT CLAIMS" in out


def test_cli_summarize_with_fake_llm_stores(db_file, monkeypatch, capsys):
    c = db_file()
    store.upsert_patent(c, _sample_patent())
    c.close()
    monkeypatch.setattr(
        patents_cli,
        "_anthropic_llm",
        lambda model: (lambda prompt: '{"plain_summary": "A tantalum transmon patent."}'),
        raising=True,
    )
    rc = patents_cli.main(["summarize", "US10374134B2"])
    assert rc == 0
    # Verify it persisted.
    c2 = db_file()
    rec = store.get_patent(c2, "US10374134B2")
    c2.close()
    assert rec["plain_summary"] == "A tantalum transmon patent."


# ─── CLI routing ───


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    """A migrated file-backed DB that scq.db.connection.connect() opens fresh.

    Using a real file (not :memory:) lets the CLI open and close its own
    connection through the patched connect() exactly as it does in prod.
    """
    path = tmp_path / "patents_test.db"
    seed = sqlite3.connect(path)
    apply_pending(seed)
    seed.close()

    def _connect(**kw):
        c = sqlite3.connect(path)
        c.execute("PRAGMA foreign_keys = ON")
        return c

    monkeypatch.setattr("scq.db.connection.connect", _connect, raising=True)
    return _connect


def test_cli_show_missing_returns_1(db_file):
    rc = patents_cli.main(["show", "US10374134B2"])
    assert rc == 1


def test_cli_show_existing(db_file, capsys):
    c = db_file()
    store.upsert_patent(c, _sample_patent())
    c.close()
    rc = patents_cli.main(["show", "US10374134B2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "International Business Machines" in out


def test_cli_fetch_defaults_to_google(tmp_path, monkeypatch):
    # Default source is google (keyless) — no api_key needed.
    monkeypatch.setattr(google, "_default_http", lambda u, h: _GOOGLE_HTML, raising=True)
    out_file = tmp_path / "US10374134B2_patent.json"
    monkeypatch.setattr(patents_cli, "_inbox_json_path", lambda canon: out_file, raising=True)
    rc = patents_cli.main(["fetch", "US10374134B2"])
    assert rc == 0
    assert out_file.exists()
    saved = json.loads(out_file.read_text("utf-8"))
    assert saved["assignee"] == "International Business Machines"
    assert saved["source"] == "google"


def test_cli_no_subcommand_prints_help():
    assert patents_cli.main([]) == 1


# ─── assignee monitoring (#8 scaffold) ───


def test_search_by_assignee_parses_and_requires_key():
    payload = {
        "patents": [
            {
                "patent_id": "10374134",
                "patent_title": "Qubit",
                "patent_date": "2019-08-06",
                "assignees": [{"assignee_organization": "International Business Machines"}],
            }
        ]
    }
    rows = patentsview.search_by_assignee("IBM", api_key="KEY", http=lambda u, h: payload)
    assert rows == [
        {
            "number": "10374134",
            "title": "Qubit",
            "assignee": "International Business Machines",
            "grant_date": "2019-08-06",
        }
    ]
    with pytest.raises(ValueError):
        patentsview.search_by_assignee("IBM", api_key="")


def test_build_assignee_search_includes_date_filter():
    url, _ = patentsview.build_assignee_search_request("IBM", "KEY", since="2026-01-01")
    assert "_gte" in url and "patent_date" in url


def test_cli_monitor_requires_assignee():
    assert patents_cli.main(["monitor"]) == 2


def test_cli_monitor_dedups_and_reports(db_file, monkeypatch, capsys):
    # Seed one already-stored patent so it's deduped out.
    c = db_file()
    store.upsert_patent(c, _sample_patent())  # US10374134B2
    c.close()

    def fake_search(name, *, api_key, since=None, http=None):
        return [
            {"number": "10374134", "title": "Already stored", "assignee": name, "grant_date": ""},
            {"number": "11111111", "title": "Brand new qubit patent", "assignee": name, "grant_date": ""},
        ]

    monkeypatch.setattr(patentsview, "search_by_assignee", fake_search, raising=True)
    rc = patents_cli.main(["monitor", "--assignee", "IBM"])
    out = capsys.readouterr().out
    assert rc == 0
    # US10374134B2 is stored (number "10374134") → deduped; 11111111 is new.
    assert "11111111" in out
    assert "1 new patent" in out


def test_cli_monitor_dormant_without_key(db_file, monkeypatch, capsys):
    def no_key_search(name, *, api_key, since=None, http=None):
        raise ValueError("PatentsView requires an API key.")

    monkeypatch.setattr(patentsview, "search_by_assignee", no_key_search, raising=True)
    rc = patents_cli.main(["monitor", "--assignee", "IBM"])
    assert rc == 2


def test_cli_patents_routes_through_top_level():
    from scq.cli import _PASSTHROUGH_COMMANDS

    assert "patents" in _PASSTHROUGH_COMMANDS
