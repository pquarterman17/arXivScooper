"""PatentsView (USPTO) provider — the first patent source.

PatentsView's Search API (v1, ``search.patentsview.org``) exposes US
patents and published applications as JSON. It requires a free API key
sent in the ``X-Api-Key`` header (register at
https://patentsview.org/apis/keyrequest). Patent bibliographic data and
claim fulltext live at two endpoints, so a full record is two calls:

  - ``/api/v1/patent/``  — title, abstract, dates, assignee, inventors, CPC
  - ``/api/v1/g_claim/`` — granted-claim fulltext (one row per claim)

The HTTP leg is injectable (``http`` parameter) so tests run without the
network and the browser can route through the ``/api/patents`` proxy.
Request *building* and response *parsing* are split into pure functions
so both can be unit-tested against captured JSON.

Field names follow PatentsView v1; parsing is deliberately tolerant
(``.get`` everywhere) so a schema tweak degrades a field rather than
crashing the ingest.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from ..normalize import Patent, parse_patent_number, split_independent

API_BASE = "https://search.patentsview.org/api/v1"
SOURCE = "patentsview"

# Fields requested from the patent endpoint. Keep narrow — PatentsView
# bills nothing but large field sets are slower and noisier to parse.
_PATENT_FIELDS = [
    "patent_id",
    "patent_title",
    "patent_abstract",
    "patent_date",  # grant date
    "patent_earliest_application_date",
    "assignees.assignee_organization",
    "inventors.inventor_name_first",
    "inventors.inventor_name_last",
    "cpc_current.cpc_subclass_id",
    "cpc_current.cpc_group_id",
]

_CLAIM_FIELDS = ["patent_id", "claim_sequence", "claim_text", "claim_dependent"]

HttpFn = Callable[[str, dict], dict]


# ─── request building (pure) ───


def build_patent_request(doc_number: str, api_key: str) -> tuple[str, dict]:
    """Build the (url, headers) for the bibliographic patent query."""
    q = json.dumps({"patent_id": doc_number})
    f = json.dumps(_PATENT_FIELDS)
    url = f"{API_BASE}/patent/?q={urllib.parse.quote(q)}&f={urllib.parse.quote(f)}"
    return url, _headers(api_key)


def build_claims_request(doc_number: str, api_key: str) -> tuple[str, dict]:
    """Build the (url, headers) for the granted-claims fulltext query."""
    q = json.dumps({"patent_id": doc_number})
    f = json.dumps(_CLAIM_FIELDS)
    o = json.dumps({"size": 500})  # plenty for any single patent's claims
    url = (
        f"{API_BASE}/g_claim/?q={urllib.parse.quote(q)}"
        f"&f={urllib.parse.quote(f)}&o={urllib.parse.quote(o)}"
    )
    return url, _headers(api_key)


def _headers(api_key: str) -> dict:
    return {
        "X-Api-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "arXivScooper/1.0 (+https://github.com/pquarterman17/arXivScooper)",
    }


# ─── response parsing (pure) ───


def parse_patent_response(payload: dict, *, number_info: dict) -> Patent:
    """Turn a ``/patent/`` JSON payload into a partial :class:`Patent`.

    Claims are filled separately by :func:`merge_claims`. ``number_info``
    is the dict from :func:`parse_patent_number` for the requested number.
    """
    patents = payload.get("patents") or []
    rec = patents[0] if patents else {}

    inventors = []
    for inv in rec.get("inventors") or []:
        first = (inv.get("inventor_name_first") or "").strip()
        last = (inv.get("inventor_name_last") or "").strip()
        full = f"{first} {last}".strip()
        if full:
            inventors.append(full)

    assignees = rec.get("assignees") or []
    assignee = ""
    if assignees:
        assignee = (assignees[0].get("assignee_organization") or "").strip()

    cpc_codes = []
    for c in rec.get("cpc_current") or []:
        code = c.get("cpc_group_id") or c.get("cpc_subclass_id")
        if code and code not in cpc_codes:
            cpc_codes.append(code)

    return Patent(
        number=number_info["canonical"],
        country=number_info["country"],
        doc_number=number_info["doc_number"],
        kind_code=number_info["kind_code"],
        is_application=number_info["is_application"],
        title=(rec.get("patent_title") or "").strip(),
        abstract=(rec.get("patent_abstract") or "").strip(),
        assignee=assignee,
        inventors=inventors,
        filing_date=(rec.get("patent_earliest_application_date") or "").strip(),
        grant_date=(rec.get("patent_date") or "").strip(),
        pub_date=(rec.get("patent_date") or "").strip(),
        cpc_codes=cpc_codes,
        url=f"https://patents.google.com/patent/{number_info['canonical']}",
        source=SOURCE,
    )


def parse_claims_response(payload: dict) -> list[dict]:
    """Turn a ``/g_claim/`` JSON payload into the canonical claims list.

    PatentsView marks a claim's dependency via ``claim_dependent`` (the
    sequence number of the claim it depends on, or null/0 for independent
    claims). We translate that into the ``is_independent`` flag.
    """
    rows = payload.get("g_claims") or payload.get("claims") or []
    claims: list[dict] = []
    for row in rows:
        text = (row.get("claim_text") or "").strip()
        if not text:
            continue
        seq = row.get("claim_sequence")
        dependent = row.get("claim_dependent")
        is_independent = not dependent  # None, 0, "" → independent
        claims.append(
            {
                "num": (seq + 1) if isinstance(seq, int) else (seq or len(claims) + 1),
                "text": text,
                "is_independent": bool(is_independent),
            }
        )
    claims.sort(key=lambda c: c["num"])
    return claims


def merge_claims(patent: Patent, claims: list[dict]) -> Patent:
    """Attach claims to a patent, backfilling the independent flag if absent."""
    patent.claims = claims
    # If the provider gave no explicit independence flags, fall back to the
    # text heuristic so independent_claims still resolves sensibly.
    if claims and not any(c.get("is_independent") for c in claims):
        indep_texts = set(split_independent(claims))
        for c in claims:
            c["is_independent"] = c["text"] in indep_texts
    return patent


# ─── network leg (injectable) ───


def _default_http(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_patent(
    number: str,
    *,
    api_key: str,
    http: HttpFn | None = None,
    fetch_claims: bool = True,
) -> Patent:
    """Fetch a full patent record from PatentsView.

    ``http`` is an injectable ``(url, headers) -> parsed_json`` callable;
    when ``None`` a urllib-based default is used. Tests pass a stub.

    Raises ``LookupError`` if the patent number returns no record.
    """
    if not api_key:
        raise ValueError(
            "PatentsView requires an API key. Set the 'patentsview_api_key' "
            "secret (scq config set-secret patentsview_api_key) or pass api_key."
        )
    do_http = http or _default_http
    info = parse_patent_number(number)

    url, headers = build_patent_request(info["doc_number"], api_key)
    payload = do_http(url, headers)
    if not (payload.get("patents") or []):
        raise LookupError(f"PatentsView returned no record for {number!r}")
    patent = parse_patent_response(payload, number_info=info)

    if fetch_claims:
        c_url, c_headers = build_claims_request(info["doc_number"], api_key)
        try:
            claims_payload = do_http(c_url, c_headers)
            merge_claims(patent, parse_claims_response(claims_payload))
        except (urllib.error.URLError, LookupError, ValueError):
            # Claims are best-effort: a bib record with no claims is still
            # worth storing. The summarizer simply has less to work with.
            pass

    return patent
