"""--resend re-mails exactly the papers a past run sent, and nothing else."""

from __future__ import annotations

import pytest

from scq.arxiv import digest, state


def _p(pid, score=30.0):
    return {"id": pid, "title": f"Paper {pid}", "relevance_score": score}


def test_filter_sent_on_keeps_only_that_days_papers():
    sent = {"a": "2026-10-14", "b": "2026-10-13", "c": "2026-10-14"}
    papers = [_p("a"), _p("b"), _p("c"), _p("d")]
    assert [p["id"] for p in state.filter_sent_on(papers, sent, "2026-10-14")] == ["a", "c"]
    assert state.filter_sent_on(papers, sent, "2026-01-01") == []


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Run digest.main() with the network, ranking and email stubbed out."""
    st = tmp_path / "digest_state.json"
    monkeypatch.setenv("SCQ_DIGEST_STATE_PATH", str(st))
    monkeypatch.setattr(digest, "DIGEST_DIR", str(tmp_path / "digests"))
    fetched = [_p("a"), _p("b"), _p("c")]
    monkeypatch.setattr(digest, "fetch_arxiv_papers", lambda *a, **k: list(fetched))
    monkeypatch.setattr(digest, "rank_papers", lambda ps: ps)
    monkeypatch.setattr(digest, "generate_html_digest", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(
        digest,
        "send_email_digest",
        lambda papers, d, context=None: sent.append((papers, d)) or True,
    )

    def go(*argv, prior=None):
        state.save_sent_ids(prior or {}, st)
        digest.main(["--days", "7", *argv])
        return sent

    go.state_path = st
    return go


def test_resend_mails_the_papers_sent_that_day_and_leaves_state_alone(run):
    prior = {"a": "2026-10-14", "c": "2026-10-14", "b": "2026-10-13"}
    sent = run("--resend", "2026-10-14", prior=prior)
    papers, label = sent[0]
    assert [p["id"] for p in papers] == ["a", "c"]
    assert label == "2026-10-14"
    assert state.load_sent_ids(run.state_path) == prior


def test_resend_with_nothing_recorded_fails_instead_of_mailing_an_empty_note(run):
    with pytest.raises(SystemExit) as exc:
        run("--resend", "2026-10-14", prior={"b": "2026-10-13"})
    assert exc.value.code == 1
