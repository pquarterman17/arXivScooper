#!/usr/bin/env python3
"""Where is arXiv rejecting us right now — the API, RSS, or neither?

First thing to run when a digest run fails. On 2026-09-18 the Atom API origin
(/api/query) returned HTTP 406 to every request for hours while Fastly kept
serving a few cached URLs, so ad-hoc checks looked fine while every scheduled
digest failed. rss.arxiv.org was up the whole time. This checks both, so you
can tell an API outage (the digest falls back to RSS on its own) from a total
outage (nothing to do but wait) from a local network problem.

Exit 0 if any source answered, 1 if none did.

Run: python tools/arxiv_probe.py
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"
CATS = ["quant-ph", "cond-mat.supr-con"]


def _status(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, len(r.read())
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, 0


def main():
    from scq.arxiv.rss import RSS_BASE
    from scq.arxiv.search import _api_bases

    # A realistic query: a tiny one can be served from cache and hide an
    # origin that is rejecting everything the digest actually asks for.
    qs = urllib.parse.urlencode(
        {
            "search_query": "cat:quant-ph",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": "200",
        }
    )

    api_ok = rss_ok = False
    codes = []

    print("Atom API:")
    for base in _api_bases():
        st, n = _status(f"{base}?{qs}")
        codes.append(st)
        api_ok = api_ok or st == 200
        print(f"  {urllib.parse.urlparse(base).netloc}: {st} ({n} bytes)")

    print("RSS:")
    for cat in CATS:
        st, n = _status(f"{RSS_BASE}/{cat}")
        codes.append(st)
        rss_ok = rss_ok or st == 200
        print(f"  {cat}: {st} ({n} bytes)")

    print()
    if api_ok and rss_ok:
        print("Both sources are up. A digest failure now is not arXiv availability.")
    elif rss_ok:
        print(
            "The Atom API is rejecting us but RSS is up — the digest falls back to\n"
            "RSS automatically, so it will still send today's papers. Nothing to fix."
        )
    elif api_ok:
        print("RSS is down but the API works — the digest uses the API first anyway.")
    elif all(isinstance(c, str) for c in codes):
        print(
            "Nothing reached arXiv at all (network errors, not HTTP responses).\n"
            "Check connectivity/proxy from this machine."
        )
    else:
        print(
            f"Neither source answered (codes: {sorted(set(map(str, codes)))}).\n"
            "Wait it out: the next scheduled run recovers the missed papers via\n"
            "the overlapping lookback window."
        )
    return 0 if (api_ok or rss_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
