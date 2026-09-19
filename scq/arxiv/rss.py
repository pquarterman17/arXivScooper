"""RSS fallback source for the daily digest.

``scq.arxiv.search`` queries the Atom API at ``/api/query``. On 2026-09-18
that origin was returning HTTP 406 to every request while Fastly kept serving
a handful of cached URLs — which is why the digest failed for days while
ad-hoc probes appeared to work. ``rss.arxiv.org`` stayed up throughout and
carries the same announcement data, so it is the fallback the digest drops to
when the API gives nothing.

Trade-off, on purpose: an RSS feed is the *latest announcement batch* for a
category (roughly one publishing day), not an arbitrary date range. So this
cannot honor a wide ``days_back``; it recovers today's papers rather than the
whole window. For a digest that runs daily on top of cross-run dedup that is
the right shape, and it beats sending nothing.

Shape-compatible with :func:`scq.arxiv.search.fetch_arxiv_papers` — same paper
dicts, so ranking, filtering, rendering and email are unchanged.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

RSS_BASE = "https://rss.arxiv.org/rss"

# Feed summaries are prefixed with announce metadata, e.g.
# "arXiv:2609.19147v1 Announce Type: new \nAbstract: We present ..."
_ANNOUNCE_RE = re.compile(r"^arXiv:\S+\s+Announce Type:[^\n]*\n?", re.IGNORECASE)
_ABSTRACT_RE = re.compile(r"^Abstract:\s*", re.IGNORECASE)
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")


def _clean_abstract(summary):
    """Strip the announce-type preamble the RSS feed prepends to abstracts."""
    text = _ANNOUNCE_RE.sub("", summary or "")
    text = _ABSTRACT_RE.sub("", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def _entry_id(entry):
    """Pull the bare arXiv ID (no version suffix) out of an RSS entry."""
    for value in (
        getattr(entry, "id", "") or "",
        getattr(entry, "link", "") or "",
        getattr(entry, "summary", "") or "",
    ):
        match = _ARXIV_ID_RE.search(value)
        if match:
            return match.group(1)
    return ""


def _entry_authors(entry):
    """RSS puts all authors in one comma-separated ``author`` string."""
    raw = getattr(entry, "author", "") or ""
    return [a.strip() for a in raw.split(",") if a.strip()]


def _entry_published(entry):
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_rss_papers(categories, days_back=1, fetcher=None):
    """Return papers from the per-category RSS feeds.

    ``fetcher`` is the callable used to retrieve a URL, defaulting to
    :func:`scq.arxiv.search._arxiv_get`; it must return bytes or None. Injected
    so tests exercise parsing without touching the network, and so the caller's
    retry/budget policy is reused rather than duplicated.
    """
    import feedparser

    from scq.arxiv.search import _arxiv_get, _make_short_authors

    get = fetcher or _arxiv_get
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    papers = []
    seen = set()
    for cat in categories:
        raw = get(f"{RSS_BASE}/{cat}", f"RSS {cat}")
        if not raw:
            continue
        try:
            feed = feedparser.parse(raw)
        except Exception as e:  # noqa: BLE001 — a bad feed must not sink the rest
            print(f"  Warning: could not parse RSS for {cat}: {e}")
            continue

        for entry in getattr(feed, "entries", []):
            arxiv_id = _entry_id(entry)
            if not arxiv_id or arxiv_id in seen:
                continue

            published = _entry_published(entry)
            # Feeds carry the current announcement batch; drop anything older
            # than the window, but keep entries whose date we could not read
            # rather than silently losing them.
            if published is not None and published < cutoff:
                continue

            seen.add(arxiv_id)
            authors = _entry_authors(entry)
            title = re.sub(r"\s+", " ", (getattr(entry, "title", "") or "").strip())
            tags = [t.get("term", "") for t in getattr(entry, "tags", []) or []]

            papers.append(
                {
                    "id": arxiv_id,
                    "title": title,
                    "authors": ", ".join(authors),
                    "short_authors": _make_short_authors(authors),
                    "abstract": _clean_abstract(getattr(entry, "summary", "")),
                    "published": (published or datetime.now(timezone.utc)).isoformat(),
                    "categories": tags or [cat],
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                    "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                }
            )

    return papers
