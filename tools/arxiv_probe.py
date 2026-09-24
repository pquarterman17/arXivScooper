#!/usr/bin/env python3
"""TEMPORARY diagnostic — never merged. Reconstruct the 9/23 digest ranking
from run 157's artifact and rescore it with left-boundary keyword matching."""

from __future__ import annotations

import glob
import html
import re

import scq.arxiv.search as S

TARGET = "2609.26714"
EMAIL_CAP = 15

files = glob.glob("art/**/*.html", recursive=True)
print("artifact files:", files)
doc = open(files[0], encoding="utf-8").read()

cards = re.findall(
    r'<div class="paper-card".*?score-badge [^"]*">([^<]*)</span>.*?'
    r'<a href="[^"]*" target="_blank">(.*?)</a></div>\s*<div class="paper-meta">(.*?)</div>\s*'
    r'<div class="paper-abstract"[^>]*>(.*?)</div>',
    doc,
    re.S,
)
papers = []
for score, title, meta, abstract in cards:
    aid = re.search(r"(\d{4}\.\d{4,5})", meta).group(1)
    parts = [x.strip() for x in html.unescape(re.sub(r"<[^>]+>", "", meta)).split("·")]
    papers.append(
        {
            "id": aid,
            "title": html.unescape(title).strip(),
            "abstract": html.unescape(abstract).strip(),
            "authors": parts[0] if parts else "",
            "categories": [c.strip() for c in (parts[2] if len(parts) > 2 else "").split(",")],
            "orig": float(score),
        }
    )
print(f"{len(papers)} cards parsed")
orig = sorted(papers, key=lambda p: -p["orig"])
emailed = [p for p in orig if p["orig"] >= 5][:EMAIL_CAP]
rank = next((i for i, p in enumerate(orig, 1) if p["id"] == TARGET), None)
print(
    f"\nORIGINAL: {TARGET} rank={rank}; in email top {EMAIL_CAP}: "
    f"{any(p['id'] == TARGET for p in emailed)}; >=5 count={sum(p['orig'] >= 5 for p in orig)}"
)
print(f"email cutoff score = {emailed[-1]['orig'] if emailed else None}")


def run(label):
    ps = [dict(p) for p in papers]
    ranked = S.rank_papers(ps, mode="smart")
    r = next((i for i, p in enumerate(ranked, 1) if p["id"] == TARGET), None)
    print(f"\n===== {label}: {len(ranked)} kept; {TARGET} rank={r}")
    for i, p in enumerate(ranked[:25], 1):
        mark = "*" if i <= EMAIL_CAP else " "
        print(f"{mark}{i:3} {p['relevance_score']:7.1f} {p['id']} {p['title'][:70]}")
        print(f"          {p['matched_keywords'][:8]}")
    return {p["id"]: p["relevance_score"] for p in ranked}


_fixed = S._count_keyword
S._count_keyword = lambda kw, text: text.lower().count(kw.lower())
before = run("OLD substring")
S._count_keyword = _fixed
after = run("NEW left-boundary")

changed = [
    (k, before.get(k, 0), after.get(k, 0))
    for k in set(before) | set(after)
    if abs(before.get(k, 0) - after.get(k, 0)) > 0.01
]
print(
    f"\n{len(changed)} scores changed; dropped below threshold: "
    f"{sum(1 for k in before if k not in after)}; newly included: "
    f"{sum(1 for k in after if k not in before)}"
)
