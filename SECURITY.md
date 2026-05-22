# Security policy

This is a hobby research project, not commercial software, but security
reports are still welcome and taken seriously.

## Reporting a vulnerability

If you find a security issue:

- **Do NOT open a public GitHub issue.** Open issues are immediately visible
  to scrapers and would expose users running the tool locally before the
  patch lands.
- **Use GitHub's private vulnerability reporting:**
  <https://github.com/pquarterman17/arXivScooper/security/advisories/new>
  (or click the "Security" tab on the repo page → "Report a vulnerability").
  Reports are private to the maintainer and the reporter; nothing is public
  until an advisory is published.
- Include in the report: the vulnerable file/function, repro steps, and
  impact (data disclosure, code execution, denial of service, etc.).

I aim to acknowledge within 48 hours and ship a fix within 30 days for
plausible reports. The repo is single-maintainer best-effort — no SLA.

## Threat model

This tool is designed to be run **locally on a single user's machine**:

- The HTTP server (`scq/server.py`, launched via `python -m scq serve`) binds to `127.0.0.1`. It
  is not designed to be exposed to the network.
- The SQLite DB lives in `data/arxiv_scooper.db` on the user's filesystem.
- arXiv API access goes through a local proxy that adds a User-Agent;
  responses are XML parsed via `DOMParser`.
- No authentication / authorization layer — single user.

Out of scope for security reports:
- Issues that require running the server with `--host 0.0.0.0` (don't do that)
- Issues in third-party CDN dependencies (sql.js, PDF.js) — report upstream
- Social-engineering attacks against the user

In scope:
- Path traversal in `scq/server.py` or any future HTTP endpoint
- XML / JSON parser issues that allow remote code in the browser
- Injection in SQL helpers (`run`, `query`, etc.) — services should use
  parameterized queries everywhere
- Hardcoded secrets in committed files
- Insecure handling of SMTP credentials in the digest workflow

## Disclosure timeline

Once fixed:

- A CVE is requested if the issue is severe enough to warrant one.
- The fix commit message references `[SECURITY]`.
- Reporters are credited in the commit (with permission) or in a future
  CHANGELOG, whichever fits.
