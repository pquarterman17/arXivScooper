"""Email composition + delivery for the arXiv digest (plan #13).

Two entry points:

  - :func:`load_email_recipients` — resolves recipients from
    ``data/user_config/digest.json`` (canonical), then a legacy
    ``email_recipients.json`` (deprecated), then the ``SCQ_EMAIL_TO``
    env var. Returns a list of ``{email, name, frequency}`` dicts.

  - :func:`send_email_digest` — composes a multipart HTML+text email
    summarising the ranked papers and sends it via Gmail SMTP. The
    SMTP App Password resolves through :mod:`scq.config.secrets`
    (OS keyring) when present; ``SCQ_EMAIL_APP_PASSWORD`` env var is
    the CI fallback.

Network-IO heavy and stateful (process-level email config). Tests
should monkeypatch ``smtplib.SMTP_SSL`` to avoid real network calls.
"""

from __future__ import annotations

import json
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from scq.arxiv.search import ARXIV_CATEGORIES

# Email config.
# - Sender / recipient defaults still use env vars.
# - The Gmail App Password resolves through scq.config.secrets so the OS
#   keyring is honored when set; CI keeps using SCQ_EMAIL_APP_PASSWORD env var.
# - EMAIL_TO is a *default* recipient for when no user_config and no
#   legacy file are present; recipients are otherwise loaded by
#   load_email_recipients(). No hardcoded address here so a fresh checkout
#   never ships a real email back to the previous maintainer.
EMAIL_TO = os.environ.get("SCQ_EMAIL_TO", "")
try:
    from scq.config import secrets as _secrets  # type: ignore[import-not-found]

    EMAIL_FROM = _secrets.get("email_from") or ""
    EMAIL_APP_PASSWORD = _secrets.get("email_app_password") or ""
except ImportError:
    EMAIL_FROM = os.environ.get("SCQ_EMAIL_FROM", "")
    EMAIL_APP_PASSWORD = os.environ.get("SCQ_EMAIL_APP_PASSWORD", "")

# Repo root used by the legacy email_recipients.json fallback.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Email Digest ───


def _load_email_recipients():
    """Load enabled recipient records from the digest config.

    Resolution order:
      1. data/user_config/digest.json `recipients` (the canonical location)
      2. legacy email_recipients.json at the repo root (deprecated; warn)
      3. SCQ_EMAIL_TO env var (single recipient, daily frequency)

    Returns a list of ``{email, name, frequency}`` dicts; only enabled
    entries are included. Frequency defaults to ``daily``.
    """
    recipients: list[dict] = []

    # 1. user_config/digest.json via the new config system
    try:
        from scq.config.user import load_config  # type: ignore[import-not-found]

        result = load_config("digest")
        for r in result.data.get("recipients", []) or []:
            if not isinstance(r, dict) or "email" not in r:
                continue
            if not r.get("enabled", True):
                continue
            recipients.append(
                {
                    "email": r["email"],
                    "name": r.get("name", ""),
                    "frequency": r.get("frequency", "daily"),
                }
            )
    except Exception as e:  # noqa: BLE001 — keep the digest workflow robust
        print(f"  [recipients] could not read digest config: {e}")

    # 2. legacy email_recipients.json — print a one-line nudge if it's still around
    if not recipients:
        legacy = os.path.join(BASE_DIR, "email_recipients.json")
        if os.path.isfile(legacy):
            print(
                "  [recipients] reading legacy email_recipients.json — "
                "migrate to data/user_config/digest.json (see plan #15) and remove this file"
            )
            try:
                with open(legacy) as f:
                    data = json.load(f)
                for r in data.get("recipients", []):
                    if r.get("enabled", True):
                        recipients.append(
                            {
                                "email": r["email"],
                                "name": r.get("name", ""),
                                "frequency": r.get("frequency", "daily"),
                            }
                        )
            except (json.JSONDecodeError, KeyError):
                pass

    # 3. SCQ_EMAIL_TO env var (used in CI when no user_config override is
    #    checked in). Supports a comma- or semicolon-separated list so the
    #    nightly GitHub-hosted run — which never sees the gitignored
    #    digest.json — can still reach every recipient, not just one.
    if not recipients and EMAIL_TO:
        for addr in re.split(r"[,;]", EMAIL_TO):
            addr = addr.strip()
            if addr:
                recipients.append({"email": addr, "name": "", "frequency": "daily"})
    return recipients


def send_email_digest(papers, digest_date, frequency="daily"):
    """Send a summary email with top papers and quick-action links."""
    if not EMAIL_FROM or not EMAIL_APP_PASSWORD:
        print("  Email skipped: set SCQ_EMAIL_FROM and SCQ_EMAIL_APP_PASSWORD env vars")
        print("  (Use a Gmail App Password: https://myaccount.google.com/apppasswords)")
        return False

    recipients = _load_email_recipients()
    # Filter by frequency (daily recipients get daily, weekly get weekly, "both" gets both)
    recipients = [r for r in recipients if r["frequency"] == frequency or r["frequency"] == "both"]
    if not recipients:
        print(f"  No {frequency} email recipients configured")
        return False

    top_papers = [p for p in papers if p["relevance_score"] >= 5][:15]
    starred = [p for p in top_papers if p["relevance_score"] >= 20]

    # Build email body with quick-action links
    body_html = f"""
<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #333;">
  <h2 style="color: #1a73e8;">SCQ arXiv Digest — {digest_date}</h2>
  <p style="color: #666;">{len(papers)} new papers, {len(starred)} high relevance, {len(top_papers)} total relevant.</p>
  <hr style="border: none; border-top: 1px solid #ddd;">
"""

    for p in top_papers:
        score = p["relevance_score"]
        color = "#2e7d32" if score >= 20 else ("#f57f17" if score >= 10 else "#757575")
        star_icon = "&#9733; " if score >= 20 else ""
        keywords = ", ".join(p["matched_keywords"][:4])
        body_html += f"""
  <div style="margin: 16px 0; padding: 12px; border-left: 3px solid {color}; background: #f8f9fa;">
    <div style="font-weight: 600; margin-bottom: 4px;">
      {star_icon}<a href="{p["abs_url"]}" style="color: #1a73e8; text-decoration: none;">{p["title"]}</a>
      <span style="color: {color}; font-size: 12px; font-weight: 700;">[{score}]</span>
    </div>
    <div style="font-size: 13px; color: #666; margin-bottom: 4px;">
      {p["short_authors"]} &middot; {p["published"][:10]}
    </div>
    <div style="font-size: 12px; color: #888; margin-bottom: 6px;">Keywords: {keywords}</div>
    <div style="font-size: 12px;">
      <a href="{p["abs_url"]}" style="color: #1a73e8; margin-right: 12px;">Abstract</a>
      <a href="{p["pdf_url"]}" style="color: #1a73e8; margin-right: 12px;">PDF</a>
    </div>
  </div>
"""

    body_html += f"""
  <hr style="border: none; border-top: 1px solid #ddd;">
  <p style="font-size: 13px; color: #555; text-align: center;">
    <b>Open the full digest HTML to triage papers</b> — add to reading list, star, ignore, and tag.<br>
    <span style="font-size: 11px; color: #999;">File: digests/digest_{digest_date}.html</span>
  </p>
  <p style="font-size: 11px; color: #999; text-align: center;">
    Categories: {", ".join(ARXIV_CATEGORIES)}<br>
    Manage recipients in paper_database.html Settings or email_recipients.json
  </p>
</div>
"""

    # Plain text fallback
    plain = f"SCQ arXiv Digest — {digest_date}\n"
    plain += f"{len(papers)} papers, {len(top_papers)} relevant\n\n"
    for p in top_papers:
        star = "★ " if p["relevance_score"] >= 20 else ""
        plain += f"{star}[{p['relevance_score']}] {p['title']}\n"
        plain += f"    {p['short_authors']} — {p['abs_url']}\n\n"
    plain += f"\nOpen digests/digest_{digest_date}.html to triage papers.\n"

    sent_count = 0
    for recipient in recipients:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"SCQ Digest: {len(top_papers)} relevant papers — {digest_date}"
        msg["From"] = EMAIL_FROM
        msg["To"] = recipient["email"]

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        try:
            # 30s socket timeout — without this, smtplib defaults to None
            # (block forever), which is a hang risk if Gmail is slow.
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
                server.send_message(msg)
            print(f"  Email sent to {recipient['email']}")
            sent_count += 1
        except Exception as e:
            print(f"  Email to {recipient['email']} failed: {e}")

    return sent_count > 0


# Public alias — keeps the historical name available without the leading
# underscore so callers can import it cleanly.
load_email_recipients = _load_email_recipients
