#!/usr/bin/env python3
"""Round 3: is the arXiv /api/query 406 a TLS-handshake fingerprint?

Rounds 1-2 established:
  - urllib 406s with every header profile, including curl's exact set
  - raw http.client sending curl's 3 headers byte-for-byte still 406s
  - requests (urllib3) and the curl binary both get 200

Identical headers, different outcome, both Python => the rejection keys on
the TLS ClientHello, not on HTTP. urllib3 and curl both negotiate ALPN;
stdlib ssl.create_default_context() does not. This tests that directly, so
the fix can stay on the stdlib if a plain SSLContext tweak is enough.

Run: python tools/arxiv_probe.py
"""

from __future__ import annotations

import json
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

URL = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
    {"search_query": "cat:quant-ph", "max_results": "1"}
)
UA = "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"


def attempt(label, ctx):
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            body = r.read()
            print(f"  [OK  ] {label}: {r.status}, {len(body)} bytes")
            return True
    except urllib.error.HTTPError as e:
        print(f"  [FAIL] {label}: {e.code} {e.reason}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  [ERR ] {label}: {type(e).__name__}: {e}")
        return False


results = {}

print("=== control ===")
results["default-context"] = attempt("stdlib default context", ssl.create_default_context())

print("\n=== ALPN variants ===")
ctx = ssl.create_default_context()
ctx.set_alpn_protocols(["http/1.1"])
results["alpn-http11"] = attempt("ALPN http/1.1", ctx)

ctx = ssl.create_default_context()
ctx.set_alpn_protocols(["h2", "http/1.1"])
results["alpn-h2-http11"] = attempt("ALPN h2,http/1.1", ctx)

print("\n=== ALPN + urllib3-ish ciphers ===")
URLLIB3_CIPHERS = (
    "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:ECDH+AESGCM:"
    "DH+AESGCM:ECDH+AES:DH+AES:RSA+AESGCM:RSA+AES:!aNULL:!eNULL:!MD5:!DSS"
)
try:
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["http/1.1"])
    ctx.set_ciphers(URLLIB3_CIPHERS)
    results["alpn-plus-ciphers"] = attempt("ALPN + urllib3 ciphers", ctx)
except Exception as e:  # noqa: BLE001
    print(f"  [ERR ] cipher set failed: {e}")

print("\n=== references ===")
try:
    subprocess.run(
        ["pip", "install", "-q", "requests"], check=False, capture_output=True, timeout=120
    )
    import requests  # type: ignore[import-untyped]

    r = requests.get(URL, timeout=30, headers={"User-Agent": UA})
    print(f"  [{'OK  ' if r.status_code == 200 else 'FAIL'}] requests: {r.status_code}")
    results["requests"] = r.status_code == 200
except Exception as e:  # noqa: BLE001
    print(f"  [ERR ] requests: {e}")

print("\n=== VERDICT ===")
print(json.dumps({k: ("pass" if v else "fail") for k, v in results.items()}, indent=2))
