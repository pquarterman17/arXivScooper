"""Relevance scoring for patents (plan #9).

Mirrors the paper-scoring logic in ``scq.arxiv.search`` but adds the two
patent-specific signals the config now carries:

  - **CPC boosts** — classification codes are the most precise SCQ filter.
    A patent gets ``cpcBoosts[prefix]`` points if any of its CPC codes
    starts with ``prefix`` (so ``G06N10`` matches ``G06N10/40``). Each
    prefix contributes at most once.
  - **Assignee boosts** — the patent analogue of author boosts: a
    substring match on the assignee org adds points.

Keyword scoring runs over the patent's title + abstract + claim text using
the same profile machinery as papers (title hits worth ``titleMultiplier``
× body hits). The shared relevance config is loaded via
``scq.arxiv.search._load_relevance_config`` so papers and patents stay in
one config file.

Pure logic — no DB, no network. ``score_patent`` mutates the passed dict
in place (sets ``relevance_score``/``matched_keywords``/``matched_cpc``/
``matched_assignees``) and returns the raw score.
"""

from __future__ import annotations

import re


def _count_words(needle: str, haystack: str) -> int:
    """Count word-boundary occurrences of ``needle`` in ``haystack``.

    Boundaries are non-word characters, so "TiN" matches "TiN film" but not
    "destination". Multi-word/hyphenated keywords work because the boundary
    anchors sit at the ends of the whole phrase.
    """
    if not needle:
        return 0
    return len(re.findall(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack))


def _claim_text(patent: dict) -> str:
    """Concatenate claim text from a stored patent dict (claims = list)."""
    claims = patent.get("claims") or []
    parts = []
    for c in claims:
        if isinstance(c, dict):
            parts.append(c.get("text", ""))
        elif isinstance(c, str):
            parts.append(c)
    return " ".join(parts)


def score_patent(patent: dict, cfg: dict | None = None) -> float:
    """Score a patent's relevance. Mutates ``patent`` in place; returns raw score.

    ``patent`` keys used: ``title``, ``abstract``, ``claims`` (list of
    {text} dicts or strings), ``cpc_codes`` (list of str), ``assignee``.
    ``cfg`` is an effective-config dict (as built by
    ``_build_effective_config``); when None it is loaded fresh.
    """
    if cfg is None:
        from scq.arxiv.search import _load_relevance_config

        cfg = _load_relevance_config()

    effective_keywords: dict[str, float] = cfg["effectiveKeywords"]
    keyword_to_profiles: dict[str, list[str]] = cfg.get("keywordToProfiles", {})
    title_mult: float = cfg["titleMultiplier"]
    cpc_boosts: dict[str, float] = cfg.get("cpcBoosts", {})
    assignee_boosts: dict[str, float] = cfg.get("assigneeBoosts", {})

    title_lower = (patent.get("title") or "").lower()
    body_lower = ((patent.get("abstract") or "") + " " + _claim_text(patent)).lower()

    score = 0.0
    matched_keywords: list[str] = []
    matched_profiles: set[str] = set()

    for keyword, eff_weight in effective_keywords.items():
        kw = keyword.lower()
        # Word-boundary match, NOT substring: patent claim text is long and
        # full of common words, so a naive .count() would match acronyms
        # spuriously (e.g. "MBE" inside "number", "TiN" inside "destination").
        title_hits = _count_words(kw, title_lower)
        body_hits = _count_words(kw, body_lower)
        if title_hits > 0 or body_hits > 0:
            score += (title_hits * title_mult + body_hits) * eff_weight
            if eff_weight > 0:
                matched_keywords.append(keyword)
                for pname in keyword_to_profiles.get(keyword, []):
                    matched_profiles.add(pname)

    # CPC boosts — prefix match, each configured prefix counts at most once.
    codes = patent.get("cpc_codes") or []
    matched_cpc: list[str] = []
    for prefix, pts in cpc_boosts.items():
        if any(str(code).startswith(prefix) for code in codes):
            score += pts
            matched_cpc.append(prefix)

    # Assignee boosts — substring match on the assignee org.
    assignee_lower = (patent.get("assignee") or "").lower()
    matched_assignees: list[str] = []
    for substr, pts in assignee_boosts.items():
        if substr.lower() in assignee_lower:
            score += pts
            matched_assignees.append(substr)

    patent["relevance_score"] = max(score, 0)
    patent["matched_keywords"] = matched_keywords
    patent["matched_profiles"] = sorted(matched_profiles)
    patent["matched_cpc"] = matched_cpc
    patent["matched_assignees"] = matched_assignees
    return score
