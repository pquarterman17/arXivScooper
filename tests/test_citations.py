"""Plan #14 — citation-generator tests for scq.ingest.process.

Covers the four pure-function citation generators:

  - ``make_bibtex(meta)`` — arXiv BibTeX from the canonical meta dict
  - ``make_plain_cite(meta)`` — arXiv plain-text citation (Phys. Rev. style)
  - ``_make_doi_bibtex(...)`` — BibTeX from CrossRef-style DOI metadata
  - ``_make_doi_plain_cite(...)`` — plain-text from CrossRef metadata

Plus the shared ``short_author(authors)`` formatter.

These are the kind of "freeze the output" tests where any silent
behaviour drift fails loudly. They also serve as documentation for the
exact citation format the database stores.
"""

from __future__ import annotations

import pytest

from scq.ingest.process import (
    _make_doi_bibtex,
    _make_doi_plain_cite,
    make_bibtex,
    make_plain_cite,
    short_author,
)

# ─── Fixtures ──────────────────────────────────────────────────────


def _meta(**overrides):
    """A canonical arXiv meta dict mirroring what fetch_arxiv.js writes."""
    base = {
        "arxiv_id": "2401.12345",
        "title": "Tantalum transmons with millisecond coherence",
        "authors": ["Alice Smith", "Bob Jones", "Carol Lee"],
        "published": "2024-01-23T00:00:00Z",
        "categories": ["quant-ph", "cond-mat.supr-con"],
    }
    base.update(overrides)
    return base


# ─── short_author ──────────────────────────────────────────────────


def test_short_author_two_or_more_authors():
    assert short_author(["Alice Smith", "Bob Jones"]) == "Smith et al."
    assert short_author(["A", "B", "C", "D", "E"]) == "A et al."


def test_short_author_single_author_returns_just_lastname():
    assert short_author(["Alice Smith"]) == "Smith"


def test_short_author_empty_returns_unknown():
    assert short_author([]) == "Unknown"


def test_short_author_single_token_lastname():
    """Mononyms / pseudonyms with a single token should still produce something."""
    assert short_author(["Plato"]) == "Plato"


# ─── make_bibtex (arXiv) ───────────────────────────────────────────


def test_bibtex_key_format_is_lastname_year_firstword():
    """Key = lowercase first-author-last + 4-digit year + lowercase first title word."""
    _key, bib = make_bibtex(_meta())
    # Expected: smith2024tantalum
    assert "@article{smith2024tantalum," in bib


def test_bibtex_strips_non_alpha_from_title_first_word():
    """make_bibtex regex strips everything but a-z from the first word."""
    _key, bib = make_bibtex(_meta(title="Z2 lattice gauge theories"))
    # Z2 → z (digit stripped); first word becomes 'z'
    assert "smith2024z," in bib


def test_bibtex_authors_in_last_first_format():
    _key, bib = make_bibtex(_meta(authors=["Alice Smith", "Bob Q. Jones"]))
    assert "Smith, Alice and Jones, Bob Q." in bib


def test_bibtex_doi_field_uses_arxiv_doi_prefix():
    _key, bib = make_bibtex(_meta(arxiv_id="2401.12345"))
    assert "doi       = {10.48550/arXiv.2401.12345}" in bib


def test_bibtex_note_includes_first_category():
    _key, bib = make_bibtex(_meta())
    assert "note      = {arXiv:2401.12345 [quant-ph]}" in bib


def test_bibtex_note_handles_no_categories():
    _key, bib = make_bibtex(_meta(categories=[]))
    assert "note      = {arXiv:2401.12345 []}" in bib


def test_bibtex_returns_two_tuple():
    """First element is the key, second is the entry text."""
    key, bib = make_bibtex(_meta())
    assert key == "smith2024tantalum"
    assert bib.startswith(f"@article{{{key},")


def test_bibtex_year_extracted_from_iso_published():
    _key, bib = make_bibtex(_meta(published="2026-05-02T12:00:00Z"))
    assert "year      = {2026}" in bib


# ─── make_plain_cite (arXiv) ───────────────────────────────────────


def test_plain_cite_arxiv_three_authors_uses_oxford_and():
    cite = make_plain_cite(_meta())
    # Three authors: "A, B, and C"
    assert ", and " in cite
    assert "A. Smith, B. Jones, and C. Lee" in cite


def test_plain_cite_arxiv_two_authors_uses_simple_and():
    cite = make_plain_cite(_meta(authors=["Alice Smith", "Bob Jones"]))
    assert "A. Smith and B. Jones" in cite
    assert ", and " not in cite


def test_plain_cite_arxiv_initials_use_first_letter_of_each_given_name():
    cite = make_plain_cite(_meta(authors=["Bob Q. Jones"]))
    # "Bob Q. Jones" → "B. Q. Jones"
    assert "B. Q. Jones" in cite


def test_plain_cite_arxiv_quote_wraps_title():
    cite = make_plain_cite(_meta(title="Surface oxide loss"))
    assert '"Surface oxide loss,"' in cite


def test_plain_cite_arxiv_includes_arxiv_id_and_category_and_year():
    cite = make_plain_cite(_meta())
    assert "arXiv:2401.12345 [quant-ph] (2024)." in cite


def test_plain_cite_arxiv_handles_empty_authors():
    cite = make_plain_cite(_meta(authors=[]))
    assert "Unknown" in cite


# ─── _make_doi_bibtex (CrossRef) ───────────────────────────────────


def test_doi_bibtex_key_uses_lastname_year_firstword():
    _key, bib = _make_doi_bibtex(
        doi="10.1103/PhysRevB.99.012345",
        title="A great paper",
        authors=["Alice Smith", "Bob Jones"],
        year=2023,
        journal="Phys. Rev. B",
        volume="99",
        pages="012345",
    )
    assert "@article{smith2023a," in bib


def test_doi_bibtex_handles_empty_authors_with_unknown_key():
    _key, bib = _make_doi_bibtex(
        doi="10.1234/x", title="Foo", authors=[],
        year=2024, journal="J", volume="1", pages="1",
    )
    assert "unknown2024foo" in bib


def test_doi_bibtex_handles_empty_title_with_article_placeholder():
    _key, bib = _make_doi_bibtex(
        doi="10.1234/x", title="",
        authors=["Alice Smith"],
        year=2024, journal="J", volume="1", pages="1",
    )
    assert "smith2024article" in bib


def test_doi_bibtex_includes_journal_volume_pages():
    _key, bib = _make_doi_bibtex(
        doi="10.1103/PhysRevB.99.012345",
        title="A title",
        authors=["Alice Smith"],
        year=2023,
        journal="Phys. Rev. B",
        volume="99",
        pages="012345",
    )
    assert "journal   = {Phys. Rev. B}" in bib
    assert "volume    = {99}" in bib
    assert "pages     = {012345}" in bib


# ─── _make_doi_plain_cite ──────────────────────────────────────────


def test_doi_plain_cite_two_authors_simple_and():
    cite = _make_doi_plain_cite(
        authors=["Alice Smith", "Bob Jones"],
        title="Topic",
        journal="Phys. Rev. B",
        volume="99",
        pages="012345",
        year=2023,
        doi="10.1103/PhysRevB.99.012345",
    )
    assert "A. Smith and B. Jones" in cite
    assert "Phys. Rev. B 99, 012345 (2023)" in cite
    assert "https://doi.org/10.1103/PhysRevB.99.012345" in cite


def test_doi_plain_cite_three_authors_oxford_and():
    cite = _make_doi_plain_cite(
        authors=["A. Smith", "B. Jones", "C. Lee"],
        title="T", journal="J", volume="1", pages="1",
        year=2024, doi="10.1/x",
    )
    assert ", and " in cite


def test_doi_plain_cite_empty_pages_drops_trailing_comma():
    cite = _make_doi_plain_cite(
        authors=["Alice Smith"], title="T",
        journal="Phys. Rev. B", volume="99", pages="",
        year=2024, doi="10.1/x",
    )
    # When pages is empty: "Phys. Rev. B 99 (2024)" — no trailing ", "
    assert "Phys. Rev. B 99 (2024)" in cite
    assert "99," not in cite or ", (" not in cite


def test_doi_plain_cite_quote_wraps_title():
    cite = _make_doi_plain_cite(
        authors=["A"], title="My title",
        journal="J", volume="1", pages="1",
        year=2024, doi="10.1/x",
    )
    assert '"My title,"' in cite


# ─── Round-trip / consistency ──────────────────────────────────────


@pytest.mark.parametrize("year_input,expected_year_field", [
    ("2024-01-23T00:00:00Z", "2024"),
    ("2026-05-02", "2026"),
    ("1999-12-31T23:59:59Z", "1999"),
])
def test_arxiv_year_extracted_from_various_iso_formats(year_input, expected_year_field):
    _key, bib = make_bibtex(_meta(published=year_input))
    assert f"year      = {{{expected_year_field}}}" in bib
    plain = make_plain_cite(_meta(published=year_input))
    assert f"({expected_year_field})." in plain
