#!/usr/bin/env python3
"""Round 5: is the 406 keyed on max_results (request size)?

Round 4 got 45/45 with max_results=1 at 11:42. The digest, using
max_results=1000-2000, then got nothing for a full 600s at 11:49-11:59.
The probes and the digest differ mainly in size, and a size-keyed rejection
would explain why every scheduled run fails while ad-hoc probes pass.

Sweeps sizes back-to-back, three interleaved passes so a time-varying
outage cannot masquerade as a size effect.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://export.arxiv.org/api/query"
UA = "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"
SIZES = [1, 25, 100, 200, 500, 1000, 2000]


def code(params):
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, len(r.read())
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, 0


by_size = {s: [] for s in SIZES}
for p in range(3):
    print(f"--- pass {p + 1} ---")
    for s in SIZES:
        st, n = code(
            {
                "search_query": "cat:quant-ph",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": str(s),
            }
        )
        by_size[s].append(st)
        print(f"  max_results={s:<5} -> {st} ({n} bytes)")
        time.sleep(3)

print("\n--- the digest's exact combined query ---")
combined = {
    "search_query": "cat:quant-ph OR cat:cond-mat.supr-con OR cat:cond-mat.mtrl-sci "
    "OR cat:cond-mat.mes-hall OR cat:physics.app-ph",
    "sortBy": "submittedDate",
    "sortOrder": "descending",
    "max_results": "2000",
}
print(f"  combined/2000 -> {code(combined)}")
combined["max_results"] = "200"
print(f"  combined/200  -> {code(combined)}")

print("\n=== VERDICT ===")
print(json.dumps({str(k): v for k, v in by_size.items()}, indent=2))
