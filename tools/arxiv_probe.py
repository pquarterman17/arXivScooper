#!/usr/bin/env python3
"""Find an HTTP client that arXiv's /api/query endpoint accepts.

Round 1 established: urllib gets 406 on /api/query with every header profile
(including curl's exact User-Agent + Accept), while curl gets 200 on the same
URL, and urllib gets 200 on rss.arxiv.org and /abs/ pages. So the rejection is
path-scoped and keyed on something structural about the Python client, not on
the headers we choose.

This round discriminates between the remaining causes:
  - header casing / Connection / Accept-Encoding  -> raw http.client matching
    curl byte-for-byte at the HTTP layer will pass
  - TLS fingerprint (JA3)                          -> only a non-Python TLS
    stack (the curl binary) will pass

Run: python tools/arxiv_probe.py
"""

from __future__ import annotations

import http.client
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

HOST = "export.arxiv.org"
PATH = "/api/query?" + urllib.parse.urlencode({"search_query": "cat:quant-ph", "max_results": "1"})
URL = f"https://{HOST}{PATH}"


def report(label, status, detail=""):
    flag = "OK  " if status == 200 else "FAIL"
    print(f"  [{flag}] {label}: {status} {detail}")
    return status == 200


def try_urllib(label, headers):
    req = urllib.request.Request(URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return report(label, r.status, f"{len(r.read())} bytes")
    except urllib.error.HTTPError as e:
        return report(label, e.code, e.reason)
    except Exception as e:  # noqa: BLE001
        return report(label, -1, f"{type(e).__name__}: {e}")


def try_raw_httpclient(label, headers, skip_accept_encoding=True):
    """Exactly control which headers go on the wire, and their casing/order."""
    try:
        conn = http.client.HTTPSConnection(HOST, timeout=30)
        conn.putrequest("GET", PATH, skip_host=True, skip_accept_encoding=skip_accept_encoding)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return report(label, resp.status, f"{len(body)} bytes")
    except Exception as e:  # noqa: BLE001
        return report(label, -1, f"{type(e).__name__}: {e}")


def try_curl(label, extra=()):
    try:
        out = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", *extra, URL],
            capture_output=True,
            text=True,
            timeout=40,
        )
        return report(label, int(out.stdout.strip() or 0))
    except Exception as e:  # noqa: BLE001
        return report(label, -1, f"{type(e).__name__}: {e}")


CURL_HEADERS = {"Host": HOST, "User-Agent": "curl/8.5.0", "Accept": "*/*"}
POLITE = "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"

print("=== A. urllib variants ===")
results = {}
results["urllib-keepalive"] = try_urllib(
    "urllib + Connection: keep-alive",
    {"User-Agent": POLITE, "Accept": "*/*", "Connection": "keep-alive"},
)
results["urllib-identity"] = try_urllib(
    "urllib + Accept-Encoding: identity",
    {"User-Agent": POLITE, "Accept": "*/*", "Accept-Encoding": "identity"},
)

print("\n=== B. raw http.client (exact curl header set on the wire) ===")
results["raw-curl-clone"] = try_raw_httpclient("raw, curl's 3 headers", CURL_HEADERS)
results["raw-polite"] = try_raw_httpclient("raw, polite UA", {**CURL_HEADERS, "User-Agent": POLITE})
results["raw-with-conn-close"] = try_raw_httpclient(
    "raw + Connection: close", {**CURL_HEADERS, "Connection": "close"}
)
results["raw-with-accept-encoding"] = try_raw_httpclient(
    "raw + Accept-Encoding (urllib casing)",
    {**CURL_HEADERS, "Accept-encoding": "identity"},
    skip_accept_encoding=False,
)

print("\n=== C. requests / urllib3 ===")
try:
    subprocess.run(
        ["pip", "install", "-q", "requests"], check=False, capture_output=True, timeout=120
    )
    import requests  # type: ignore[import-untyped]

    try:
        r = requests.get(URL, timeout=30, headers={"User-Agent": POLITE})
        results["requests"] = report("requests", r.status_code, f"{len(r.content)} bytes")
    except Exception as e:  # noqa: BLE001
        results["requests"] = report("requests", -1, f"{type(e).__name__}: {e}")
except Exception as e:  # noqa: BLE001
    print(f"  [SKIP] requests unavailable: {e}")

print("\n=== D. curl binary ===")
results["curl"] = try_curl("curl default")
results["curl-polite-ua"] = try_curl("curl + polite UA", ["-A", POLITE])
try:
    v = subprocess.run(["curl", "--version"], capture_output=True, text=True, timeout=20)
    print(f"  curl: {v.stdout.splitlines()[0] if v.stdout else '?'}")
except Exception:
    pass

print("\n=== VERDICT ===")
print(json.dumps({k: ("pass" if v else "fail") for k, v in results.items()}, indent=2))
