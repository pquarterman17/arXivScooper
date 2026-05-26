"""Claim → plain-English summary support.

The actual legalese-to-English translation is done by an LLM. In Phase 1
that's Claude, driven by the ``summarize-patent`` skill (human in the
loop). Phase 2 layers an automated call into the digest pipeline. Both
paths share the two helpers here:

  - :func:`build_summary_prompt` — assemble the patent text into a single
    prompt asking for the three summary fields. One source of truth for
    *what* we ask, so the skill and the future automation stay in sync.
  - :func:`parse_summary_response` — pull the three fields back out of a
    structured (JSON) model reply.

Keeping these DOM-free and side-effect-free makes them unit-testable and
reusable from either driver. Storage is :func:`scq.patents.store.store_summary`.
"""

from __future__ import annotations

import json

# The three fields every patent summary captures (the user's Phase 1 pick).
SUMMARY_FIELDS = ("plain_summary", "protected_scope", "prior_art_note")

_INSTRUCTIONS = """\
You are translating patent legalese into plain English for a researcher.
Read the patent below and return a JSON object with exactly these keys:

  "plain_summary":   2-3 plain sentences: what the invention actually does,
                     stripped of legal hedging. No marketing language.
  "protected_scope": A plain-English reading of the INDEPENDENT claims —
                     the real legal boundary, not the abstract. State what
                     someone would have to do to infringe.
  "prior_art_note":  What the patent builds on or distinguishes itself from,
                     per its own background/statements. If the patent does
                     not say, write "Not stated in the patent." Do not
                     speculate beyond what the text supports.

Return ONLY the JSON object, no prose around it.
"""


def build_summary_prompt(patent: dict) -> str:
    """Build the LLM prompt for a patent dict (as returned by store.get_patent).

    Independent claims are surfaced first and in full because they carry the
    legal scope; remaining claims are summarized as a count to keep the
    prompt focused (and cheaper when automated).
    """
    indep = patent.get("independent_claims") or []
    all_claims = patent.get("claims") or []
    dep_count = max(len(all_claims) - len(indep), 0)

    lines = [
        _INSTRUCTIONS,
        "",
        f"PATENT: {patent.get('number', '')}",
        f"TITLE: {patent.get('title', '')}",
        f"ASSIGNEE: {patent.get('assignee', '')}",
        f"FILED: {patent.get('filing_date', '')}   GRANTED: {patent.get('grant_date', '')}",
        "",
        "ABSTRACT:",
        patent.get("abstract", "") or "(none)",
        "",
        f"INDEPENDENT CLAIMS ({len(indep)}):",
    ]
    for i, text in enumerate(indep, 1):
        lines.append(f"  [{i}] {text}")
    if not indep:
        lines.append("  (none captured)")
    lines.append("")
    lines.append(f"(plus {dep_count} dependent claim(s) not shown)")
    return "\n".join(lines)


def parse_summary_response(reply: str) -> dict:
    """Extract the three summary fields from a model reply.

    Tolerant of replies wrapped in ```json fences or surrounded by prose:
    finds the first ``{`` … last ``}`` and parses that. Returns a dict with
    only the recognized SUMMARY_FIELDS keys (missing keys omitted).
    """
    text = reply.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in summary reply")
    obj = json.loads(text[start : end + 1])
    return {k: str(obj[k]).strip() for k in SUMMARY_FIELDS if k in obj and obj[k] is not None}
