#!/usr/bin/env python3
"""TEMPORARY diagnostic — never merged. Build a relevance-tuning corpus from
every downloaded digest artifact (art/) plus today's full RSS batch."""

from __future__ import annotations

import glob
import html
import json
import os
import re

from scq.arxiv.rss import fetch_rss_papers

corpus = {}
for path in sorted(glob.glob("art/**/*.html", recursive=True)):
    doc = open(path, encoding="utf-8").read()
    day = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    cards = re.findall(
        r'<div class="paper-card".*?score-badge [^"]*">([^<]*)</span>.*?'
        r'<a href="[^"]*" target="_blank">(.*?)</a></div>\s*<div class="paper-meta">(.*?)</div>\s*'
        r'<div class="paper-abstract"[^>]*>(.*?)</div>',
        doc,
        re.S,
    )
    for _score, title, meta, abstract in cards:
        m = re.search(r"(\d{4}\.\d{4,5})", meta)
        if not m:
            continue
        parts = [x.strip() for x in html.unescape(re.sub(r"<[^>]+>", "", meta)).split("·")]
        corpus.setdefault(
            m.group(1),
            {
                "id": m.group(1),
                "title": html.unescape(title).strip(),
                "abstract": html.unescape(abstract).strip(),
                "authors": parts[0] if parts else "",
                "categories": [c.strip() for c in (parts[2] if len(parts) > 2 else "").split(",")],
                "day": day.group(1) if day else "?",
                "src": "digest",
            },
        )
    print(f"{path}: {len(cards)} cards")

cats = ["quant-ph", "cond-mat.supr-con", "cond-mat.mtrl-sci", "cond-mat.mes-hall", "physics.app-ph"]
rss = fetch_rss_papers(cats, days_back=1)
for p in rss:
    corpus.setdefault(
        p["id"],
        {
            "id": p["id"],
            "title": p["title"],
            "abstract": p["abstract"],
            "authors": p.get("authors", ""),
            "categories": p.get("categories", []),
            "day": "rss-today",
            "src": "rss",
        },
    )
print(f"rss papers: {len(rss)}; corpus total: {len(corpus)}")
os.makedirs("corpus", exist_ok=True)
json.dump(list(corpus.values()), open("corpus/corpus.json", "w"), ensure_ascii=False)
