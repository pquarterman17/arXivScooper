"""Canonical ``Patent`` contract + patent-number parsing.

Every provider (PatentsView, later EPO/Google) normalizes its raw
response into a :class:`Patent` so that storage, summarization, and the
UI never need to know which source a patent came from. This mirrors the
project-wide data-contract rule (every parser returns one shape).

Patent numbers are messy in the wild — ``US10,374,134 B2``,
``US 10374134B2``, ``10374134``, ``US2020/0012345A1`` (an application).
:func:`parse_patent_number` normalizes any of these into the canonical
``<COUNTRY><DIGITS><KIND>`` form used as the DB primary key, and reports
the pieces (country, digits, kind code, application-vs-grant) that
downstream providers need to build their API queries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# A claim: its number, full text, and whether it's independent. Independent
# claims are the legal heart of a patent — dependent claims only narrow them.
ClaimDict = dict  # {"num": int, "text": str, "is_independent": bool}


@dataclass
class Patent:
    """The canonical patent record. All providers converge on this shape."""

    number: str  # canonical "<COUNTRY><DIGITS><KIND>" e.g. "US10374134B2"
    country: str = "US"
    doc_number: str = ""  # digits only
    kind_code: str = ""
    is_application: bool = False

    title: str = ""
    abstract: str = ""

    assignee: str = ""
    inventors: list[str] = field(default_factory=list)

    filing_date: str = ""  # ISO YYYY-MM-DD
    grant_date: str = ""
    pub_date: str = ""

    claims: list[ClaimDict] = field(default_factory=list)
    cpc_codes: list[str] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)
    cited_by: list[str] = field(default_factory=list)

    url: str = ""
    source: str = ""

    @property
    def independent_claims(self) -> list[str]:
        """Texts of the independent claims — the actual legal scope.

        Falls back to the first claim if none are explicitly flagged
        independent (some providers omit the flag); claim 1 is always
        independent.
        """
        indep = [c["text"] for c in self.claims if c.get("is_independent")]
        if indep:
            return indep
        return [self.claims[0]["text"]] if self.claims else []

    @property
    def short_inventors(self) -> str:
        """ "Gambetta et al." style short string from the inventor list."""
        names = self.inventors
        if not names:
            return ""
        last = names[0].split()[-1] if names[0].split() else names[0]
        if len(names) == 1:
            return last
        if len(names) == 2:
            second = names[1].split()[-1] if names[1].split() else names[1]
            return f"{last} & {second}"
        return f"{last} et al."


# ─── Patent-number parsing ───

# Country prefix (2 letters), digits (optionally with separators / a year
# slash for applications), and an optional kind code (letter + optional digit).
_NUMBER_RE = re.compile(
    r"""
    ^\s*
    (?P<country>[A-Za-z]{2})?      # optional country prefix
    \s*
    (?P<body>[\d,\s/]+)            # digits, possibly with , / and spaces
    \s*
    (?P<kind>[A-Za-z]\d?)?         # optional kind code, e.g. B2, A1
    \s*$
    """,
    re.VERBOSE,
)


def parse_patent_number(raw: str) -> dict:
    """Parse a free-form patent number into canonical pieces.

    Returns a dict with ``country``, ``doc_number`` (digits only),
    ``kind_code``, ``is_application``, and ``canonical`` (the
    ``<COUNTRY><DIGITS><KIND>`` string used as the DB key).

    Examples
    --------
    >>> parse_patent_number("US10,374,134 B2")["canonical"]
    'US10374134B2'
    >>> parse_patent_number("10374134")["country"]
    'US'
    >>> parse_patent_number("US2020/0012345A1")["is_application"]
    True

    Raises
    ------
    ValueError
        If no digit sequence can be found.
    """
    if not raw or not raw.strip():
        raise ValueError("empty patent number")

    m = _NUMBER_RE.match(raw)
    if not m:
        raise ValueError(f"unrecognized patent number: {raw!r}")

    country = (m.group("country") or "US").upper()
    body = re.sub(r"[,\s]", "", m.group("body") or "")
    kind = (m.group("kind") or "").upper()

    # Application numbers often carry a year/serial slash (2020/0012345) or
    # an A-series kind code; granted patents use B-series. Strip the slash
    # for the digit run but use it (and the kind) as the application signal.
    has_slash = "/" in body
    digits = body.replace("/", "")
    if not digits.isdigit():
        raise ValueError(f"no digit run in patent number: {raw!r}")

    is_application = has_slash or kind.startswith("A") or len(digits) >= 11

    canonical = f"{country}{digits}{kind}"
    return {
        "country": country,
        "doc_number": digits,
        "kind_code": kind,
        "is_application": is_application,
        "canonical": canonical,
    }


def split_independent(claims: list[ClaimDict]) -> list[str]:
    """Return the texts of independent claims from a claim list.

    A claim is independent if it does not reference another claim
    ("claim 1", "any preceding claim", etc.). Providers that supply an
    explicit ``is_independent`` flag should set it; this is the fallback
    heuristic used when they don't.
    """
    out: list[str] = []
    ref_re = re.compile(r"\bclaim[s]?\s+\d|\bany\s+(?:one\s+)?of\b|preceding\s+claim", re.I)
    for c in claims:
        text = c.get("text", "")
        if c.get("is_independent") or not ref_re.search(text):
            out.append(text)
    return out


def today_iso() -> str:
    """Today's date as an ISO string (kept here so providers/store agree)."""
    return date.today().isoformat()
