"""Tests for the cross-run digest dedup state (scq.arxiv.state).

Pure file/dict logic — no network. Each test points the state file at a
tmp_path so the committed data/digest_state.json is never touched.
"""

from __future__ import annotations

from datetime import date

import scq.arxiv.state as state


def _papers(*ids):
    return [{"id": i, "title": f"Paper {i}"} for i in ids]


def test_load_missing_file_returns_empty(tmp_path):
    """A non-existent state file degrades to 'remember nothing'."""
    assert state.load_sent_ids(tmp_path / "nope.json") == {}


def test_load_corrupt_file_returns_empty(tmp_path):
    """A malformed JSON file must not crash the digest."""
    p = tmp_path / "digest_state.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert state.load_sent_ids(p) == {}


def test_save_and_load_round_trip(tmp_path):
    p = tmp_path / "digest_state.json"
    sent = {"2401.00001": "2026-05-30", "2401.00002": "2026-05-29"}
    state.save_sent_ids(sent, p)
    assert state.load_sent_ids(p) == sent


def test_filter_unsent_drops_known_ids():
    sent = {"2401.00001": "2026-05-30"}
    result = state.filter_unsent(_papers("2401.00001", "2401.00002"), sent)
    assert [p["id"] for p in result] == ["2401.00002"]


def test_filter_unsent_keeps_paper_without_id():
    """Fail-open: a paper missing the id key is kept, not silently dropped."""
    sent = {"2401.00001": "2026-05-30"}
    papers = [{"title": "no id here"}, {"id": "2401.00001"}]
    result = state.filter_unsent(papers, sent)
    assert result == [{"title": "no id here"}]


def test_record_sent_stamps_new_ids_and_preserves_first_date():
    sent = {"2401.00001": "2026-05-01"}
    state.record_sent(_papers("2401.00001", "2401.00002"), sent, date_str="2026-05-30")
    # Existing ID keeps its original first-sent date
    assert sent["2401.00001"] == "2026-05-01"
    # New ID stamped with the run date
    assert sent["2401.00002"] == "2026-05-30"


def test_prune_drops_entries_older_than_keep_days():
    today = date(2026, 5, 30)
    sent = {
        "old": "2026-01-01",   # ~149 days ago → dropped
        "fresh": "2026-05-29",  # 1 day ago → kept
    }
    state.prune(sent, keep_days=60, today=today)
    assert "old" not in sent
    assert "fresh" in sent


def test_prune_keeps_unparseable_dates():
    """Fail-open: a hand-edited bad date is kept, not erased."""
    today = date(2026, 5, 30)
    sent = {"weird": "not-a-date"}
    state.prune(sent, keep_days=1, today=today)
    assert "weird" in sent


def test_state_path_honors_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom_state.json"
    monkeypatch.setenv("SCQ_DIGEST_STATE_PATH", str(target))
    assert state.state_path() == target
