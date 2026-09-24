#!/usr/bin/env python3
"""TEMPORARY diagnostic — never merged. Find the Nb-strain resonator paper
the digest missed, and show exactly how the live ranker scored it."""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request

from scq.arxiv.search import rank_papers

UA = "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"
DIGEST_CATS = ["quant-ph", "cond-mat.supr-con", "cond-mat.mtrl-sci", "cond-mat.mes-hall"]
EXTRA_CATS = ["physics.app-ph"]
TITLE_HIT = re.compile(r"\b(Nb|niobium|strain|strained|stress)\b", re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, ""


# 1. Scan the past week's listings, primaries + cross-lists.
found = {}
for cat in DIGEST_CATS + EXTRA_CATS:
    st, body = get(f"https://arxiv.org/list/{cat}/pastweek?show=2000")
    ids = re.findall(r'href\s*=\s*"/abs/(\d{4}\.\d{4,5})"', body)
    titles = re.findall(r"list-title[^>]*>\s*<span[^>]*>Title:</span>\s*(.*?)</div>", body, re.S)
    print(f"list {cat:18} HTTP {st}  ids={len(ids)} titles={len(titles)}")
    for aid, t in zip(ids, titles, strict=False):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t))).strip()
        if TITLE_HIT.search(t):
            found.setdefault(aid, {"title": t, "lists": []})["lists"].append(cat)

print(f"\n{len(found)} candidate title(s)\n")

# 2. Pull each candidate's abstract + categories, then score with the live ranker.
papers = []
for aid, info in found.items():
    st, body = get(f"https://arxiv.org/abs/{aid}")
    m = re.search(r'<meta name="citation_abstract" content="(.*?)"', body, re.S)
    abstract = html.unescape(m.group(1)) if m else ""
    subj = re.search(r'<td class="tablecell subjects">(.*?)</td>', body, re.S)
    cats = re.findall(r"\(([a-z\-]+(?:\.[A-Za-z\-]+)?)\)", subj.group(1)) if subj else []
    date = re.search(r'<meta name="citation_date" content="([^"]+)"', body)
    authors = re.findall(r'<meta name="citation_author" content="([^"]+)"', body)
    papers.append(
        {
            "id": aid,
            "title": info["title"],
            "abstract": abstract,
            "authors": ", ".join(authors),
            "categories": cats,
            "listed_in": info["lists"],
            "date": date.group(1) if date else "?",
        }
    )

for p in rank_papers(papers):
    in_digest_cats = any(c in DIGEST_CATS for c in p["categories"])
    print(f"=== {p['id']}  score={p['relevance_score']:.1f}  date={p['date']}")
    print(f"    {p['title']}")
    print(f"    categories={p['categories']}  fetched_by_digest={in_digest_cats}")
    print(f"    matched={p['matched_keywords']}")
    if re.search(r"strain", p["title"] + p["abstract"], re.I) and re.search(
        r"\b(Nb|niobium)\b", p["title"] + p["abstract"], re.I
    ):
        print("    >>> Nb + strain match: full abstract follows")
        print("    " + re.sub(r"\s+", " ", p["abstract"])[:1400])
    print()
