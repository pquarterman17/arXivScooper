"""Digest rows must never lose their title, whatever arXiv puts in it.

Regression: titles went into the email as raw HTML, so one containing "<" and a
letter (e.g. "<Tc") was parsed as a tag that swallowed the closing link, leaving
only the score. Empty titles also rendered as a blank link.
"""

from __future__ import annotations

import re

from scq.arxiv import email as email_mod
from scq.arxiv.common import display_title
from scq.arxiv.render import generate_html_digest


def _paper(title, score=24.0, pid="2610.00001"):
    return {
        "id": pid,
        "title": title,
        "authors": "A. Smith, B. Jones",
        "short_authors": "Smith et al.",
        "abstract": "An abstract.",
        "published": "2026-10-13T00:00:00+00:00",
        "categories": ["quant-ph"],
        "pdf_url": f"https://arxiv.org/pdf/{pid}",
        "abs_url": f"https://arxiv.org/abs/{pid}",
        "relevance_score": score,
        "matched_keywords": ["superconducting", "STM"],
    }


def _capture_email(monkeypatch, papers):
    sent = []
    monkeypatch.setattr(email_mod, "EMAIL_FROM", "a@b.test")
    monkeypatch.setattr(email_mod, "EMAIL_APP_PASSWORD", "pw")
    monkeypatch.setattr(
        email_mod,
        "_load_email_recipients",
        lambda: [{"email": "to@b.test", "name": "", "frequency": "daily"}],
    )
    monkeypatch.setattr(
        email_mod, "_deliver", lambda r, s, plain, h: sent.append((plain, h)) or True
    )
    assert email_mod.send_email_digest(papers, "2026-10-14") is True
    return sent[0]


def test_display_title_falls_back_to_the_arxiv_id():
    assert display_title({"id": "2610.00001", "title": ""}) == (
        "arXiv:2610.00001 (title unavailable)"
    )
    assert display_title({"id": "2610.00001", "title": "   "}).startswith("arXiv:2610.00001")
    assert display_title({"id": "x", "title": " Real title "}) == "Real title"


def test_email_escapes_angle_brackets_in_titles(monkeypatch):
    plain, body = _capture_email(monkeypatch, [_paper("<Tc suppression in Nb & Ta films")])
    assert "&lt;Tc suppression in Nb &amp; Ta films</a>" in body
    # The row's closing markup survives: link closed, score badge intact.
    assert re.search(r"</a>\s*<span[^>]*>\[24.0\]</span>", body)
    assert "<Tc suppression" in plain  # plain text stays unescaped


def test_email_shows_a_title_even_when_the_feed_entry_had_none(monkeypatch):
    plain, body = _capture_email(monkeypatch, [_paper("")])
    assert "arXiv:2610.00001 (title unavailable)</a>" in body
    assert "arXiv:2610.00001 (title unavailable)" in plain


def test_html_digest_escapes_and_falls_back(tmp_path):
    out = tmp_path / "digest.html"
    generate_html_digest(
        [_paper("<Tc in Nb & Ta"), _paper("", pid="2610.00002")], "2026-10-14", str(out)
    )
    page = out.read_text()
    assert ">&lt;Tc in Nb &amp; Ta</a>" in page
    assert "arXiv:2610.00002 (title unavailable)</a>" in page
