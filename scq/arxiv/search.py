"""arXiv API client + relevance scoring (plan #13).

Two responsibilities:

  1. ``fetch_arxiv_papers(categories, days_back, max_results)`` — query the
     arXiv Atom API for recent papers in the given categories. Uses one
     OR-combined request when possible (cheaper rate-limit-wise), falls
     back to per-category requests with polite delays. Honors a wall-
     clock budget set via ``set_budget(seconds)`` so a slow arXiv can't
     hang the GH Actions runner indefinitely.

  2. ``rank_papers(papers)`` / ``score_paper(paper)`` — score papers
     against config-driven keyword profiles (title hits worth
     ``titleMultiplier``x abstract hits) and return them sorted descending.
     Falls back to ``_FALLBACK_KEYWORDS`` when the config system is
     unavailable.

Pure logic — no DOM, no email side-effects, no DB writes. Suitable for
unit testing with a mocked HTTP layer.
"""

from __future__ import annotations

import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET


class ArxivFetchError(RuntimeError):
    """Raised when no arXiv response could be obtained at all.

    Distinguishes a *fetch failure* (rate-limit, timeout, 5xx, or the
    wall-clock budget being exhausted before any page was retrieved) from
    a *genuinely empty result* (arXiv answered, but nothing matched the
    date window). Both used to collapse into an empty list, which let the
    digest mail a misleading "no papers" email on a transient outage.
    """


# ─── Configuration ───

ARXIV_CATEGORIES = [
    "quant-ph",
    "cond-mat.supr-con",
    "cond-mat.mtrl-sci",
    "cond-mat.mes-hall",  # mesoscopic / nanoscale — catches qubit & resonator device work
    "physics.app-ph",  # applied physics — catches device fabrication papers
]

# Fallback keyword weights used when the relevance config cannot be loaded.
# Negative weights penalise off-topic papers that share generic keywords.
# Tuned for superconducting-quantum-computing materials research.
_FALLBACK_KEYWORDS = {
    # ── Materials & fabrication (primary focus) ──
    "superconducting qubit": 10,
    "loss tangent": 10,
    "dielectric loss": 10,
    "materials loss": 9,
    "surface oxide": 9,
    "surface treatment": 9,
    "internal quality": 9,
    "tantalum": 9,
    "niobium": 8,
    "aluminum oxide": 8,
    "sapphire": 7,
    "silicon substrate": 7,
    "high-resistivity silicon": 8,
    "TiN": 8,
    "NbTiN": 8,
    "josephson junction": 8,
    "quality factor": 8,
    "thin film": 7,
    "coplanar waveguide": 8,
    "CPW": 7,
    "microwave resonator": 8,
    "kinetic inductance": 7,
    "superinductor": 8,
    "fabrication": 5,
    "substrate": 5,
    # ── Qubit coherence & design (close second) ──
    "transmon": 9,
    "fluxonium": 9,
    "coherence": 8,
    "T1": 8,
    "T2": 8,
    "two-level system": 8,
    "TLS": 7,
    "decoherence": 7,
    "dephasing": 7,
    "quasiparticle": 7,
    "charge noise": 7,
    "flux noise": 7,
    "energy relaxation": 7,
    "purcell": 6,
    "relaxation": 5,
    "noise": 3,
    # ── Characterization techniques ──
    "XPS": 7,
    "x-ray photoelectron": 7,
    "ARXPS": 8,
    "EELS": 7,
    "electron energy loss": 7,
    "TEM": 5,
    "STEM": 6,
    "AFM": 5,
    "STM": 5,
    "ellipsometry": 7,
    "x-ray reflectivity": 7,
    "XRR": 7,
    "SIMS": 7,
    "secondary ion mass": 7,
    "transport measurement": 6,
    "sheet resistance": 6,
    "residual resistivity ratio": 7,
    "RRR": 6,
    # ── Growth & deposition ──
    "sputtering": 7,
    "magnetron sputtering": 8,
    "molecular beam epitaxy": 8,
    "MBE": 7,
    "epitaxial": 6,
    "atomic layer deposition": 7,
    "ALD": 6,
    "evaporation": 4,
    "e-beam evaporation": 7,
    "Dolan bridge": 8,
    # ── Readout & amplification ──
    "parametric amplif": 7,
    "JPA": 7,
    "TWPA": 7,
    "dispersive readout": 7,
    "quantum-limited": 6,
    # ── Gates & control ──
    "gate fidelity": 7,
    "optimal control": 6,
    "DRAG": 6,
    "leakage": 5,
    "cross-resonance": 6,
    # ── Resonators ──
    "superconducting resonator": 8,
    "microwave cavity": 6,
    "3D cavity": 7,
    # ── General SCQ ──
    "superconducting circuit": 8,
    "circuit QED": 7,
    "cQED": 7,
    "quantum processor": 4,
    "quantum computing": 2,
    # ── Negative: quantum algorithms (not hardware) ──
    "variational quantum eigensolver": -6,
    "VQE": -5,
    "QAOA": -6,
    "quantum approximate optimization": -6,
    "Grover": -4,
    "quantum advantage": -5,
    "quantum supremacy": -5,
    "quantum machine learning": -5,
    "quantum neural network": -5,
    "quantum chemistry": -4,
    "quantum simulation": -3,
    "variational ansatz": -5,
    "barren plateau": -5,
}

# Keep KEYWORD_WEIGHTS as a public alias for backwards-compat (e.g. tests that
# import it directly). Points at the same object as _FALLBACK_KEYWORDS.
KEYWORD_WEIGHTS = _FALLBACK_KEYWORDS

ARXIV_API = "http://arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Wall-clock budget (set by `set_budget(seconds)`). When the deadline passes,
# network calls return None instead of starting another attempt — keeps the
# script from chewing through the GH Actions job timeout when arXiv is slow.
# A 2026-04-29 incident hung the runner for 15 min on a single hung connection.
_BUDGET_DEADLINE = None
_HTTP_TIMEOUT = 45  # per-request socket timeout (sec) — large feeds are slow to render
_MAX_BACKOFF = 120  # cap any single retry wait (sec)
# arXiv's PatentSearch/Atom API caps a single request at 2000 results and
# 429s/times-out aggressively above that. Never ask for more in one request.
_ARXIV_MAX_PER_REQUEST = 2000
# Default retry budget per request. arXiv rate-limits the shared GitHub
# Actions egress IPs hard (chronic HTTP 429), and a 429 burst can take a
# couple of minutes to clear. The old default of 3 surrendered after ~35s
# and wasted the 600s wall-clock budget; 6 attempts with the larger backoff
# cap lets a single request outlast a throttle window while the budget guard
# still prevents the runner from overrunning its timeout.
_DEFAULT_MAX_RETRIES = 6

# ─── Relevance config cache ───

# Loaded once per process on first call to _load_relevance_config().
# None = not yet loaded; dict = previously loaded (may be fallback).
_RELEVANCE_CONFIG_CACHE: dict | None = None


def _load_relevance_config() -> dict:
    """Return the effective relevance config, loaded once per process.

    Merges ship defaults with user overrides. Each profile in the user
    override is merged on top of the matching defaults profile: the user
    can set ``focus`` and add/override individual keywords without
    restating the entire profile.

    Falls back to a synthetic config built from ``_FALLBACK_KEYWORDS`` on
    any error so the digest pipeline never hard-stops due to a config
    problem.
    """
    global _RELEVANCE_CONFIG_CACHE
    if _RELEVANCE_CONFIG_CACHE is not None:
        return _RELEVANCE_CONFIG_CACHE

    try:
        from scq.config.user import load_config

        result = load_config("relevance")
        cfg = result.data
        if result.errors:
            import logging

            logging.getLogger(__name__).warning(
                "relevance config has validation errors (%d); proceeding anyway: %s",
                len(result.errors),
                result.errors,
            )
        _RELEVANCE_CONFIG_CACHE = _build_effective_config(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"  [relevance] config unreadable, using built-in keywords: {exc}")
        _RELEVANCE_CONFIG_CACHE = _fallback_effective_config()

    return _RELEVANCE_CONFIG_CACHE


def _build_effective_config(cfg: dict) -> dict:
    """Compute the flattened effective-keywords dict from a merged config."""
    title_mult = float(cfg.get("titleMultiplier", 2.0))
    min_score = float(cfg.get("minScoreToInclude", 5))
    author_boosts: dict[str, float] = {
        k: float(v) for k, v in (cfg.get("authorBoosts") or {}).items()
    }
    # Patent-only boost maps (used by scq.patents.relevance.score_patent).
    cpc_boosts: dict[str, float] = {k: float(v) for k, v in (cfg.get("cpcBoosts") or {}).items()}
    assignee_boosts: dict[str, float] = {
        k: float(v) for k, v in (cfg.get("assigneeBoosts") or {}).items()
    }

    profiles: dict[str, dict] = cfg.get("profiles") or {}
    effective_keywords: dict[str, float] = {}
    keyword_to_profiles: dict[str, list[str]] = {}

    for profile_name, profile in profiles.items():
        focus = float(profile.get("focus", 1.0))
        if focus == 0.0:
            continue  # entire profile silenced
        for kw, weight in (profile.get("keywords") or {}).items():
            eff = float(weight) * focus
            # Last-profile wins for duplicate keywords (consistent with _deep_merge)
            effective_keywords[kw] = eff
            keyword_to_profiles[kw] = keyword_to_profiles.get(kw, []) + [profile_name]

    return {
        "titleMultiplier": title_mult,
        "minScoreToInclude": min_score,
        "authorBoosts": author_boosts,
        "cpcBoosts": cpc_boosts,
        "assigneeBoosts": assignee_boosts,
        "effectiveKeywords": effective_keywords,
        "keywordToProfiles": keyword_to_profiles,
    }


def _fallback_effective_config() -> dict:
    """Synthetic effective config built from the hardcoded _FALLBACK_KEYWORDS."""
    return {
        "titleMultiplier": 2.0,
        "minScoreToInclude": 5,
        "authorBoosts": {},
        "cpcBoosts": {},
        "assigneeBoosts": {},
        "effectiveKeywords": dict(_FALLBACK_KEYWORDS),
        "keywordToProfiles": {kw: ["fallback"] for kw in _FALLBACK_KEYWORDS},
    }


def invalidate_relevance_cache() -> None:
    """Force reload of relevance config on next score_paper() call.

    Useful in tests and after the user edits relevance.json mid-session.
    """
    global _RELEVANCE_CONFIG_CACHE
    _RELEVANCE_CONFIG_CACHE = None


def set_budget(seconds: float | None) -> None:
    """Set a wall-clock deadline. Pass ``None`` to disable budgeting."""
    global _BUDGET_DEADLINE
    _BUDGET_DEADLINE = (time.monotonic() + seconds) if seconds is not None else None


def _budget_remaining():
    """Seconds left in the wall-clock budget, or None if no budget is set."""
    if _BUDGET_DEADLINE is None:
        return None
    return _BUDGET_DEADLINE - time.monotonic()


def _budget_exceeded():
    rem = _budget_remaining()
    return rem is not None and rem <= 0


def _arxiv_get(url, label, max_retries=_DEFAULT_MAX_RETRIES):
    """Fetch a URL from arXiv with polite retries.

    Retries on HTTP 429, 5xx, socket timeouts, and transient URL errors. Honors
    the server's Retry-After header when present; otherwise uses exponential
    backoff with jitter, capped at _MAX_BACKOFF.

    Aborts (returns None) if the wall-clock budget set in main() is exhausted —
    so a slow/hung arXiv can't run the GH Actions job clock out.
    """
    for attempt in range(max_retries):
        if _budget_exceeded():
            print(f"  Aborting {label}: time budget exhausted")
            return None
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)"
                },
            )
            resp = urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
            return resp.read()
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or 500 <= e.code < 600
            if not retryable or attempt == max_retries - 1:
                print(f"  Warning: Failed to fetch {label}: {e}")
                return None
            retry_after = e.headers.get("Retry-After") if e.headers else None
            try:
                wait = float(retry_after) if retry_after else 0
            except ValueError:
                wait = 0
            if wait <= 0:
                wait = min(_MAX_BACKOFF, 5 * (2**attempt))
            wait += random.uniform(0, wait * 0.25)  # jitter
            wait = _clamp_wait(wait)
            if wait is None:
                print(f"  Aborting {label}: time budget exhausted before retry")
                return None
            print(
                f"  HTTP {e.code} on {label}, retrying in {wait:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})..."
            )
            time.sleep(wait)
        except (TimeoutError, urllib.error.URLError) as e:
            if attempt == max_retries - 1:
                print(f"  Warning: Failed to fetch {label}: {e}")
                return None
            wait = min(_MAX_BACKOFF, 5 * (2**attempt))
            wait += random.uniform(0, wait * 0.25)
            wait = _clamp_wait(wait)
            if wait is None:
                print(f"  Aborting {label}: time budget exhausted before retry")
                return None
            print(
                f"  Network error on {label} ({e}), retrying in {wait:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})..."
            )
            time.sleep(wait)
        except Exception as e:
            print(f"  Warning: Failed to fetch {label}: {e}")
            return None
    return None


def _clamp_wait(wait):
    """Trim a sleep so we don't sleep past the deadline. Returns None if no
    budget is left at all."""
    rem = _budget_remaining()
    if rem is None:
        return wait
    if rem <= 0:
        return None
    return min(wait, rem)


def fetch_arxiv_papers(categories, days_back=1, max_results=200):
    """Fetch recent papers from arXiv API for the given categories.

    Uses a single OR'd query across all categories so we burn one rate-limit
    budget rather than five. Falls back to per-category requests (with a polite
    inter-request delay) if the combined query fails.
    """
    papers = []
    seen_ids = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    # Combined OR query — one request for all categories
    combined_query = " OR ".join(f"cat:{c}" for c in categories)
    # Scale the page size with the category count so a wide window is covered,
    # but never exceed arXiv's single-request ceiling: larger requests are the
    # ones that read-timeout and draw the hardest rate-limiting.
    combined_max = max(max_results, max_results * len(categories) // 2, 1000)
    combined_max = min(combined_max, _ARXIV_MAX_PER_REQUEST)
    params = {
        "search_query": combined_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(combined_max),
    }
    url = ARXIV_API + "?" + urllib.parse.urlencode(params)
    xml_data = _arxiv_get(url, "combined query")

    roots = []
    if xml_data is not None:
        try:
            roots.append(ET.fromstring(xml_data))
        except ET.ParseError as e:
            print(f"  Warning: Failed to parse combined response: {e}")
            xml_data = None

    if xml_data is None:
        # Fallback: per-category with polite 3s delay between requests
        print("  Falling back to per-category fetches...")
        for i, cat in enumerate(categories):
            if _budget_exceeded():
                print(
                    f"  Skipping remaining categories ({len(categories) - i} left): time budget exhausted"
                )
                break
            if i > 0:
                # Be extra polite under fallback: we only land here when the
                # combined query already failed, which usually means we are
                # being throttled. A longer inter-request gap (vs. arXiv's 3s
                # floor) gives the rate limiter room to recover.
                time.sleep(5)
            params = {
                "search_query": f"cat:{cat}",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": str(min(max_results, _ARXIV_MAX_PER_REQUEST)),
            }
            cat_url = ARXIV_API + "?" + urllib.parse.urlencode(params)
            cat_xml = _arxiv_get(cat_url, cat)
            if cat_xml is None:
                continue
            try:
                roots.append(ET.fromstring(cat_xml))
            except ET.ParseError as e:
                print(f"  Warning: Failed to parse {cat}: {e}")

    # No usable response from either the combined query or any per-category
    # fallback means we never reached arXiv (rate-limit / timeout / 5xx /
    # budget exhausted). Signal that distinctly so the caller does NOT mail
    # an empty digest that looks like "nothing was published today".
    if not roots:
        raise ArxivFetchError(
            "arXiv returned no usable response (combined query and all "
            "per-category fallbacks failed - likely rate-limit, timeout, "
            "5xx, or exhausted network budget)"
        )

    for root in roots:
        for entry in root.findall("atom:entry", ARXIV_NS):
            # Parse published date
            published_str = entry.findtext("atom:published", "", ARXIV_NS)
            try:
                published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            if published < cutoff:
                continue

            # Extract arXiv ID
            id_url = entry.findtext("atom:id", "", ARXIV_NS)
            arxiv_id = id_url.split("/abs/")[-1] if "/abs/" in id_url else id_url.split("/")[-1]
            # Remove version suffix
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)

            if arxiv_id in seen_ids:
                continue
            seen_ids.add(arxiv_id)

            # Extract metadata
            title = entry.findtext("atom:title", "", ARXIV_NS).strip().replace("\n", " ")
            title = re.sub(r"\s+", " ", title)

            summary = entry.findtext("atom:summary", "", ARXIV_NS).strip()
            summary = re.sub(r"\s+", " ", summary)

            authors = []
            for author in entry.findall("atom:author", ARXIV_NS):
                name = author.findtext("atom:name", "", ARXIV_NS)
                if name:
                    authors.append(name)

            categories_list = [
                tag.get("term", "") for tag in entry.findall("atom:category", ARXIV_NS)
            ]

            # PDF link
            pdf_url = ""
            for link in entry.findall("atom:link", ARXIV_NS):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")

            papers.append(
                {
                    "id": arxiv_id,
                    "title": title,
                    "authors": ", ".join(authors),
                    "short_authors": _make_short_authors(authors),
                    "abstract": summary,
                    "published": published.isoformat(),
                    "categories": categories_list,
                    "pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
                    "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                }
            )

    for cat in categories:
        n = sum(1 for p in papers if cat in p.get("categories", []))
        print(f"  {cat}: {n} papers")

    return papers


def _make_short_authors(authors):
    """Generate 'First et al.' or 'First & Second' style short author string."""
    if len(authors) == 0:
        return "Unknown"
    if len(authors) == 1:
        return authors[0].split()[-1]
    if len(authors) == 2:
        return f"{authors[0].split()[-1]} & {authors[1].split()[-1]}"
    return f"{authors[0].split()[-1]} et al."


# ─── Relevance Scoring ───


def _get_ranking_mode() -> str:
    """Read the rankingMode field from the digest config (defaults to 'smart').

    Always reads fresh from the config system — not cached — so mode changes
    take effect within the same process without requiring a restart.
    """
    try:
        from scq.config.user import load_config

        result = load_config("digest")
        return result.data.get("rankingMode", "smart")
    except Exception:  # noqa: BLE001
        return "smart"


def _score_paper_simple(paper: dict) -> float:
    """Score using the flat ``_FALLBACK_KEYWORDS`` dict (pre-profile algorithm).

    Identical to the scoring logic that existed before the relevance-config
    overhaul: title hits count 2x abstract hits, no author boosts, no
    profile focus multipliers. ``paper`` is mutated in-place.
    """
    title_lower = paper["title"].lower()
    text_lower = (paper["title"] + " " + paper["abstract"]).lower()

    score: float = 0.0
    matched_keywords: list[str] = []

    for keyword, weight in _FALLBACK_KEYWORDS.items():
        kw_lower = keyword.lower()
        title_hits = title_lower.count(kw_lower)
        abstract_hits = text_lower.count(kw_lower) - title_hits
        if title_hits > 0 or abstract_hits > 0:
            kw_score = (title_hits * 2 + abstract_hits) * weight
            score += kw_score
            if weight > 0:
                matched_keywords.append(keyword)

    paper["relevance_score"] = max(score, 0)
    paper["matched_keywords"] = matched_keywords
    paper["matched_profiles"] = []
    return score


def _score_paper_smart(paper: dict) -> float:
    """Score using config-driven keyword profiles with author boosts.

    Populates ``paper["relevance_score"]``, ``paper["matched_keywords"]``,
    and ``paper["matched_profiles"]`` as side-effects. Returns the raw
    (pre-floor) score so callers can inspect it before clamping.

    Scoring formula per keyword:
        (title_hits * titleMultiplier + abstract_hits) * effective_weight

    where effective_weight = base_weight * profile.focus.

    After keyword scoring, any author whose name (case-insensitive) is a
    substring of ``authorBoosts`` keys receives the corresponding bonus.
    """
    cfg = _load_relevance_config()
    effective_keywords: dict[str, float] = cfg["effectiveKeywords"]
    keyword_to_profiles: dict[str, list[str]] = cfg["keywordToProfiles"]
    title_mult: float = cfg["titleMultiplier"]
    author_boosts: dict[str, float] = cfg["authorBoosts"]

    title_lower = paper["title"].lower()
    abstract_lower = paper["abstract"].lower()

    score: float = 0.0
    matched_keywords: list[str] = []
    matched_profiles: set[str] = set()

    for keyword, eff_weight in effective_keywords.items():
        kw_lower = keyword.lower()
        title_hits = title_lower.count(kw_lower)
        abstract_hits = abstract_lower.count(kw_lower)
        if title_hits > 0 or abstract_hits > 0:
            kw_score = (title_hits * title_mult + abstract_hits) * eff_weight
            score += kw_score
            if eff_weight > 0:
                matched_keywords.append(keyword)
                for pname in keyword_to_profiles.get(keyword, []):
                    matched_profiles.add(pname)

    # Author boosts
    authors_lower = paper.get("authors", "").lower()
    for author_substr, boost in author_boosts.items():
        if author_substr.lower() in authors_lower:
            score += boost

    paper["relevance_score"] = max(score, 0)
    paper["matched_keywords"] = matched_keywords
    paper["matched_profiles"] = sorted(matched_profiles)
    return score


def score_paper(paper: dict) -> float:
    """Score a paper's relevance, dispatching to the active ranking mode.

    Reads ``rankingMode`` from the digest config on each call (not cached)
    so mode changes take effect without restarting the process.

    Populates ``paper["relevance_score"]``, ``paper["matched_keywords"]``,
    and ``paper["matched_profiles"]`` as side-effects. Returns the raw
    (pre-floor) score.

    Modes:
        "smart"  — profile-based scoring with focus multipliers and author
                   boosts (default, current behavior).
        "simple" — flat ``_FALLBACK_KEYWORDS`` matching, fixed 2x title
                   multiplier, no author boosts, no profiles.
    """
    mode = _get_ranking_mode()
    if mode == "simple":
        return _score_paper_simple(paper)
    return _score_paper_smart(paper)


def rank_papers(papers: list[dict], mode: str | None = None) -> list[dict]:
    """Score, filter, and sort papers by relevance.

    Parameters
    ----------
    papers:
        List of paper dicts (mutated in-place with relevance fields).
    mode:
        Optional override for the ranking mode (``"simple"`` or ``"smart"``).
        When ``None`` (default), the mode is read from the digest config.

    Papers whose ``relevance_score`` falls below ``minScoreToInclude``
    from the relevance config are dropped here. ``digest.py``'s
    ``minRelevanceScore`` acts as a secondary filter on top of this.
    """
    # Resolve mode once for the entire batch — avoids repeated config reads
    # and ensures all papers in one run are scored the same way.
    resolved_mode = mode if mode is not None else _get_ranking_mode()

    for p in papers:
        if resolved_mode == "simple":
            _score_paper_simple(p)
        else:
            _score_paper_smart(p)

    cfg = _load_relevance_config()
    min_score: float = cfg["minScoreToInclude"]

    papers = [p for p in papers if p.get("relevance_score", 0) >= min_score]
    papers.sort(key=lambda p: p["relevance_score"], reverse=True)
    return papers
