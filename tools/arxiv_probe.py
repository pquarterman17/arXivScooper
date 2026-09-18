#!/usr/bin/env python3
"""Round 4: how intermittent is the 406, and does retrying beat it?

Round 3 overturned the fingerprint theory: the stdlib default SSL context
passed after failing identically in rounds 1 and 2. So the 406 is transient,
not a property of the client. That changes the fix from "send different
headers" to "retry properly" - but only if the failure rate is low enough
that a bounded retry actually converges. This measures it.

Run: python tools/arxiv_probe.py
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"
HOSTS = {
    "arxiv.org": "https://arxiv.org/api/query",
    "export.arxiv.org": "https://export.arxiv.org/api/query",
}
QS = urllib.parse.urlencode({"search_query": "cat:quant-ph", "max_results": "1"})
N = 15


def one(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return -1


summary = {}
for host, base in HOSTS.items():
    codes = []
    for _i in range(N):
        codes.append(one(f"{base}?{QS}"))
        time.sleep(1)
    ok = sum(1 for c in codes if c == 200)
    summary[f"urllib {host}"] = f"{ok}/{N} ok  codes={codes}"
    print(f"  urllib {host}: {ok}/{N} ok  {codes}")

# Does an immediate retry recover a 406, or does the block persist?
print("\n=== immediate-retry recovery on export ===")
recovered = attempts = 0
for _i in range(10):
    base = HOSTS["export.arxiv.org"]
    if one(f"{base}?{QS}") != 200:
        attempts += 1
        for delay in (1, 2, 4):
            time.sleep(delay)
            if one(f"{base}?{QS}") == 200:
                recovered += 1
                break
    time.sleep(1)
summary["retry_recovery"] = f"{recovered}/{attempts} initial failures recovered within 3 retries"
print(f"  {summary['retry_recovery']}")

print("\n=== requests, same volume ===")
try:
    subprocess.run(
        ["pip", "install", "-q", "requests"], check=False, capture_output=True, timeout=120
    )
    import requests  # type: ignore[import-untyped]

    codes = []
    for _i in range(N):
        try:
            codes.append(
                requests.get(
                    f"{HOSTS['export.arxiv.org']}?{QS}", timeout=30, headers={"User-Agent": UA}
                ).status_code
            )
        except Exception:  # noqa: BLE001
            codes.append(-1)
        time.sleep(1)
    ok = sum(1 for c in codes if c == 200)
    summary["requests export"] = f"{ok}/{N} ok  codes={codes}"
    print(f"  requests: {ok}/{N} ok  {codes}")
except Exception as e:  # noqa: BLE001
    print(f"  [SKIP] {e}")

print("\n=== VERDICT ===")
print(json.dumps(summary, indent=2))
