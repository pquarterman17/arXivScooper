"""Google Patents provider — keyless HTML scrape.

Google Patents has no official API, so this provider fetches the public
patent page (``patents.google.com/patent/<number>/en``) and parses it.
The upside is zero setup: no API key, no account. The downsides are that
it's ToS-gray (fine for personal, low-volume research use) and brittle —
if Google changes the page, parsing degrades.

Reliability split:
  - **Bibliographic data** comes from the server-rendered ``<meta>`` tags
    (``DC.title``, ``DC.contributor`` with scheme inventor/assignee,
    ``DC.date`` with scheme filing/publication). These are stable and
    parsed via :class:`html.parser.HTMLParser`.
  - **Claims + abstract** live in the page body, which is the fragile
    part. Extraction is best-effort: a missing claims block yields an
    empty list and the summarizer simply has less to work with, rather
    than crashing the ingest.

The HTTP leg is injectable (``http`` parameter, ``(url, headers) -> str``)
so tests parse captured HTML with no network and the request can be
routed elsewhere if needed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from html.parser import HTMLParser

from ..normalize import Patent, parse_patent_number, split_independent

SOURCE = "google"
PATENT_URL = "https://patents.google.com/patent/{number}/en"

# A browser-ish UA — Google serves the SEO-rendered HTML (with meta tags +
# claims) to normal user agents.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
        "arXivScooper/1.0 (+https://github.com/pquarterman17/arXivScooper)"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

HttpFn = Callable[[str, dict], str]


# ─── request building (pure) ───


def build_request(number: str) -> tuple[str, dict]:
    """Build the (url, headers) for a patent's Google Patents page."""
    info = parse_patent_number(number)
    return PATENT_URL.format(number=info["canonical"]), dict(_HEADERS)


# ─── HTML parsing (pure) ───


class _MetaParser(HTMLParser):
    """Collect the scholarly ``<meta>`` tags Google renders for each patent.

    Builds:
      - ``self.meta``      : list of (name, scheme, content) for DC.* tags
      - ``self.simple``    : {name: content} for plain name=content metas
    """

    def __init__(self) -> None:
        super().__init__()
        self.meta: list[tuple[str, str, str]] = []
        self.simple: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        a = {k: (v or "") for k, v in attrs}
        name = a.get("name", "")
        content = a.get("content", "")
        if not name or not content:
            return
        if name.startswith("DC.") or name.startswith("citation_"):
            self.meta.append((name, a.get("scheme", ""), content))
        else:
            self.simple[name] = content


def _collect_meta(parser: _MetaParser, name: str, scheme: str | None = None) -> list[str]:
    out = []
    for n, sch, content in parser.meta:
        if n == name and (scheme is None or sch == scheme):
            out.append(content)
    return out


def _first(parser: _MetaParser, name: str, scheme: str | None = None) -> str:
    vals = _collect_meta(parser, name, scheme)
    return vals[0] if vals else ""


# Claims block: Google wraps claims in a section flagged with itemprop.
# We grab the section, strip tags, and split on leading claim numbers.
_CLAIMS_SECTION_RE = re.compile(
    r'<section[^>]*itemprop=["\']claims["\'][^>]*>(.*?)</section>', re.I | re.S
)
_ABSTRACT_SECTION_RE = re.compile(
    r'<(?:section|div)[^>]*itemprop=["\']abstract["\'][^>]*>(.*?)</(?:section|div)>', re.I | re.S
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _strip_tags(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    # Unescape the few entities Google uses in claim text.
    for ent, ch in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(ent, ch)
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _parse_claims(html: str) -> list[dict]:
    """Best-effort claim extraction from the claims section HTML."""
    m = _CLAIMS_SECTION_RE.search(html)
    if not m:
        return []
    text = _strip_tags(m.group(1))
    # Claims are numbered "1. ...", "2. ...". Split on a number-dot at the
    # start of a claim. Tolerant of the whole block being one line.
    parts = re.split(r"(?:(?<=\s)|^)(\d{1,3})\s*\.\s+", text)
    claims: list[dict] = []
    # re.split with a capture group yields [pre, num, body, num, body, ...]
    it = iter(parts[1:] if parts and not parts[0].strip() else parts)
    pending_num = None
    for chunk in it:
        if pending_num is None and chunk.isdigit():
            pending_num = int(chunk)
            continue
        if pending_num is not None:
            body = chunk.strip()
            if body:
                claims.append({"num": pending_num, "text": body, "is_independent": False})
            pending_num = None
    return claims


def parse_html(html: str, *, number_info: dict) -> Patent:
    """Parse a Google Patents page into a :class:`Patent`."""
    mp = _MetaParser()
    mp.feed(html)

    title = _first(mp, "DC.title") or mp.simple.get("citation_title", "")
    inventors = _collect_meta(mp, "DC.contributor", "inventor")
    assignees = _collect_meta(mp, "DC.contributor", "assignee")
    assignee = assignees[0] if assignees else ""

    filing = _first(mp, "DC.date", "dateApplicationFiling") or _first(mp, "DC.date", "dateFiling")
    grant = _first(mp, "DC.date", "datePublication") or _first(mp, "DC.date", "dateGranted")

    abstract = _first(mp, "DC.description") or mp.simple.get("description", "")
    if not abstract:
        am = _ABSTRACT_SECTION_RE.search(html)
        if am:
            abstract = _strip_tags(am.group(1))

    claims = _parse_claims(html)
    # No explicit independence flags in scraped claims → use the text heuristic.
    if claims:
        indep = set(split_independent(claims))
        for c in claims:
            c["is_independent"] = c["text"] in indep

    p = Patent(
        number=number_info["canonical"],
        country=number_info["country"],
        doc_number=number_info["doc_number"],
        kind_code=number_info["kind_code"],
        is_application=number_info["is_application"],
        title=title.strip(),
        abstract=abstract.strip(),
        assignee=assignee.strip(),
        inventors=[i.strip() for i in inventors if i.strip()],
        filing_date=filing.strip(),
        grant_date=grant.strip(),
        pub_date=grant.strip(),
        claims=claims,
        url=PATENT_URL.format(number=number_info["canonical"]),
        source=SOURCE,
    )
    return p


# ─── network leg (injectable) ───


def _default_http(url: str, headers: dict) -> str:
    import urllib.request

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_patent(number: str, *, http: HttpFn | None = None, **_ignored) -> Patent:
    """Fetch + parse a patent from Google Patents (no API key).

    ``http`` is an injectable ``(url, headers) -> html_text`` callable;
    when ``None`` a urllib default is used. ``**_ignored`` swallows
    provider-agnostic kwargs (e.g. ``api_key``) the CLI passes uniformly.

    Raises ``LookupError`` if the page has no recognizable patent title.
    """
    do_http = http or _default_http
    info = parse_patent_number(number)
    url, headers = build_request(number)
    html = do_http(url, headers)
    patent = parse_html(html, number_info=info)
    if not patent.title:
        raise LookupError(
            f"Google Patents returned no parseable record for {number!r} "
            f"(page may be missing, or its layout changed)."
        )
    return patent
