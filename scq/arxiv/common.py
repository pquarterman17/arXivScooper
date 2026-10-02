"""Small helpers shared by the arXiv fetchers.

Lives apart from ``search`` and ``rss`` so neither has to import the other
(``search`` falls back to ``rss`` when the Atom API is down).
"""

from __future__ import annotations


class ArxivFetchError(RuntimeError):
    """Raised when no arXiv response could be obtained at all.

    Distinguishes a *fetch failure* (rate-limit, timeout, 5xx, or the
    wall-clock budget being exhausted before any page was retrieved) from
    a *genuinely empty result* (arXiv answered, but nothing matched the
    date window). Both used to collapse into an empty list, which let the
    digest mail a misleading "no papers" email on a transient outage.
    """


def _make_short_authors(authors):
    """Generate 'First et al.' or 'First & Second' style short author string."""
    if len(authors) == 0:
        return "Unknown"
    if len(authors) == 1:
        return authors[0].split()[-1]
    if len(authors) == 2:
        return f"{authors[0].split()[-1]} & {authors[1].split()[-1]}"
    return f"{authors[0].split()[-1]} et al."
