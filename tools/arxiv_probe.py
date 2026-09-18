#!/usr/bin/env python3
"""Diagnose what the arXiv API actually rejects, from the machine that runs it.

The digest started failing with a blanket HTTP 406 on every request. Guessing
at the cause from a sandbox that cannot reach arxiv.org burned a cycle, so this
probes the real endpoint from the runner and prints the evidence: status,
response headers, and the error body, which is where the edge usually explains
itself.

Run: python tools/arxiv_probe.py
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

SIMPLE = {"search_query": "cat:quant-ph", "max_results": "1"}
FULL = {
    "search_query": "cat:quant-ph OR cat:cond-mat.supr-con",
    "sortBy": "submittedDate",
    "sortOrder": "descending",
    "max_results": "2000",
}

HOSTS = {
    "arxiv-https": "https://arxiv.org/api/query",
    "export-https": "https://export.arxiv.org/api/query",
    "export-http": "http://export.arxiv.org/api/query",
}

PROFILES = {
    "bare": {},
    "ua-only": {"User-Agent": "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"},
    "profile0": {
        "User-Agent": "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)",
        "Accept": "application/atom+xml,application/xml;q=0.9,text/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    },
    "curl-like": {"User-Agent": "curl/8.5.0", "Accept": "*/*"},
    "browser": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    },
}


def probe(label, url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            print(
                f"  [OK   ] {label}: {resp.status}, {len(body)} bytes, "
                f"ctype={resp.headers.get('Content-Type')}"
            )
            return True
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()[:400]
        except Exception:
            pass
        server = e.headers.get("Server") if e.headers else "?"
        cf = e.headers.get("cf-mitigated") if e.headers else None
        print(f"  [HTTP ] {label}: {e.code} {e.reason} | server={server} | cf-mitigated={cf}")
        if body:
            print(f"           body: {body.decode('utf-8', 'replace')[:400]!r}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  [ERR  ] {label}: {type(e).__name__}: {e}")
        return False


def main():
    print("=== 1. header profiles (arxiv.org, simple query) ===")
    for name, hdrs in PROFILES.items():
        probe(name, HOSTS["arxiv-https"] + "?" + urllib.parse.urlencode(SIMPLE), hdrs)

    print("\n=== 2. hosts (profile0, simple query) ===")
    for name, base in HOSTS.items():
        probe(name, base + "?" + urllib.parse.urlencode(SIMPLE), PROFILES["profile0"])

    print("\n=== 3. query shape (export-https, profile0) ===")
    base = HOSTS["export-https"]
    probe("simple", base + "?" + urllib.parse.urlencode(SIMPLE), PROFILES["profile0"])
    probe("full-2000", base + "?" + urllib.parse.urlencode(FULL), PROFILES["profile0"])
    probe(
        "full-100",
        base + "?" + urllib.parse.urlencode({**FULL, "max_results": "100"}),
        PROFILES["profile0"],
    )
    probe(
        "no-sort",
        base + "?" + urllib.parse.urlencode({k: v for k, v in FULL.items() if k != "sortBy"}),
        PROFILES["profile0"],
    )
    probe(
        "unencoded-space",
        base + "?search_query=cat:quant-ph+OR+cat:cond-mat.supr-con&max_results=5",
        PROFILES["profile0"],
    )

    print("\n=== 4. alternate endpoints ===")
    probe("rss-quant-ph", "http://rss.arxiv.org/rss/quant-ph", PROFILES["profile0"])
    probe("abs-page", "https://arxiv.org/abs/2401.00001", PROFILES["profile0"])


if __name__ == "__main__":
    main()
