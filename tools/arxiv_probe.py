#!/usr/bin/env python3
"""Round 6: is rss.arxiv.org a usable fallback while /api/query is 406ing?

Round 5 showed the API origin rejecting everything except one cached URL
(max_results=25 returned a byte-identical 49982 every pass, while
max_results=1 - a strictly smaller request - 406'd). That is a Fastly cache
hit in front of a failing origin, and the digest's combined query is unique
to this project so it never gets a cache hit.

Round 1 saw rss.arxiv.org answer 200 at a moment the API was 406ing. If the
RSS feeds are up and carry the fields the digest needs (id, title, abstract,
authors, date, categories), they are a real fallback source.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

import feedparser

UA = "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"
CATS = ["quant-ph", "cond-mat.supr-con", "cond-mat.mtrl-sci", "cond-mat.mes-hall"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, b""


print("=== API right now (control) ===")
api = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
    {
        "search_query": "cat:quant-ph",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": "200",
    }
)
st, body = fetch(api)
print(f"  api max_results=200 -> {st} ({len(body)} bytes)")

print("\n=== RSS feeds ===")
for cat in CATS:
    st, body = fetch(f"https://rss.arxiv.org/rss/{cat}")
    if st != 200:
        print(f"  {cat}: {st}")
        continue
    feed = feedparser.parse(body)
    n = len(feed.entries)
    print(f"  {cat}: 200, {len(body)} bytes, {n} entries")
    if n and cat == CATS[0]:
        e = feed.entries[0]
        print("    --- first entry field check ---")
        for field in ("id", "link", "title", "summary", "author", "published", "updated"):
            val = getattr(e, field, None)
            shown = (str(val)[:90] + "...") if val and len(str(val)) > 90 else val
            print(f"    {field}: {shown!r}")
        tags = [t.get("term") for t in getattr(e, "tags", [])]
        print(f"    tags: {tags}")
