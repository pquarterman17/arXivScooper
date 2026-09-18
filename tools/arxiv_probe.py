#!/usr/bin/env python3
"""Is arXiv's API rejecting us right now?

Troubleshooting aid for the digest. arXiv's edge intermittently answers
/api/query with HTTP 406 for windows of several minutes, rejecting every
client equally (measured 2026-09-18: urllib, raw http.client with curl's
exact headers, and any User-Agent all 406 together, then 45/45 succeed from
the same client minutes later). The digest rides this out with backoff; this
script tells you whether a window is open *now*, which is the first thing to
check when a run fails.

Exit 0 if any host answered, 1 if every attempt was rejected.

Run: python tools/arxiv_probe.py [attempts]
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from scq.arxiv.search import _api_bases

UA = "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"
QS = urllib.parse.urlencode({"search_query": "cat:quant-ph", "max_results": "1"})


def _status(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    attempts = int(argv[0]) if argv else 3

    any_ok = False
    all_codes: list = []
    for base in _api_bases():
        host = urllib.parse.urlparse(base).netloc
        codes = []
        for i in range(attempts):
            if i:
                time.sleep(2)
            codes.append(_status(f"{base}?{QS}"))
        ok = sum(1 for c in codes if c == 200)
        any_ok = any_ok or ok > 0
        all_codes.extend(codes)
        print(f"  {host}: {ok}/{attempts} ok  {codes}")

    if any_ok:
        print("\narXiv is answering. A digest failure now is not this.")
        return 0

    # Distinguish "arXiv said no" from "we never got there" — on a machine
    # with no route to arxiv.org (a sandbox, an offline laptop) every attempt
    # also fails, and calling that a rejection window would be a wrong answer.
    if any(c == 406 for c in all_codes):
        print(
            "\nEvery attempt was rejected with 406 — a rejection window is open.\n"
            "This clears on its own, usually within minutes. The digest retries\n"
            "with backoff across both hosts; the next scheduled run recovers the\n"
            "missed papers via the overlapping lookback window."
        )
    elif all(isinstance(c, str) for c in all_codes):
        print(
            "\nNo attempt reached arXiv at all (network error, not an HTTP\n"
            "response). Check connectivity/proxy from this machine — this is\n"
            "not the 406 rejection window."
        )
    else:
        print(f"\nNo attempt succeeded. Codes seen: {sorted(set(map(str, all_codes)))}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
