"""Tests for the patents package (plan: patent-scraping Phase 1).

Covers the pure seams — number parsing, provider response parsing,
summary prompt/parse — plus a store roundtrip on an in-memory DB and the
CLI argument routing. No test touches the network: the provider's HTTP
leg is injected with a stub returning captured-shaped JSON.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq.db.migrations import apply_pending
from scq.patents import cli as patents_cli
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


def test_cli_no_subcommand_prints_help():
    assert patents_cli.main([]) == 1


def test_cli_patents_routes_through_top_level():
    from scq.cli import _PASSTHROUGH_COMMANDS

    assert "patents" in _PASSTHROUGH_COMMANDS
