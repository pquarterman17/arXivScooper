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

import contextlib
import functools
import gzip
import os
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

# Primary API host. HTTPS is mandatory: the old ``http://`` URL was answered
# with a redirect by arXiv's edge and, since ~2026-09, a bare HTTP+no-Accept
# request is rejected outright with "406 Not Acceptable" (see _HEADER_PROFILES).
ARXIV_API = "https://arxiv.org/api/query"
# Failover hosts, tried in order after ARXIV_API exhausts its retries.
# ``arxiv.org`` stays first because ``export.arxiv.org`` is unroutable from
# the maintainer's home network (Fastly CDN issue, see CLAUDE.md); on GitHub
# Actions the reverse is true, so keeping both means whichever host is
# reachable from the current network wins without any per-machine config.
ARXIV_API_MIRRORS = ("https://export.arxiv.org/api/query",)
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Request header profiles, tried in order. arXiv sits behind an edge/WAF that
# returns 406 (not 403/429) when a client sends no ``Accept`` header at all —
# which is exactly what bare ``urllib.request`` does. Profile 0 is the polite,
# spec-correct client arXiv's API terms ask for; profile 1 is a wider-Accept
# retry for edges that also dislike the unfamiliar product token. Neither
# impersonates a browser; both identify the project and link the repo.
_HEADER_PROFILES = (
    {
        "User-Agent": "SCQDigest/1.0 (+https://github.com/pquarterman17/arXivScooper)",
        "Accept": "application/atom+xml,application/xml;q=0.9,text/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (compatible; SCQDigest/1.0; "
            "+https://github.com/pquarterman17/arXivScooper)"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
    },
)

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
# 8 attempts on the 5*2^n ladder (capped at _MAX_BACKOFF) spans roughly
# 8 minutes of backoff, which covers the multi-minute 406 windows measured on
# 2026-09-18. The wall-clock budget still bounds the total.
_DEFAULT_MAX_RETRIES = 8
# Floor on the budget slice handed to a single API host, so slicing never
# starves a host below one connect+read round trip.
_MIN_HOST_BUDGET = 30

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
    # keyword -> anchors, for keywords whose (winning) profile has ``requires``
    keyword_requires: dict[str, tuple[str, ...]] = {}
    profile_requires: dict[str, list[str]] = {}

    for profile_name, profile in profiles.items():
        focus = float(profile.get("focus", 1.0))
        if focus == 0.0:
            continue  # entire profile silenced
        requires = tuple(profile.get("requires") or ())
        if requires:
            profile_requires[profile_name] = list(requires)
        for kw, weight in (profile.get("keywords") or {}).items():
            eff = float(weight) * focus
            # Last-profile wins for duplicate keywords (consistent with _deep_merge)
            effective_keywords[kw] = eff
            keyword_to_profiles[kw] = keyword_to_profiles.get(kw, []) + [profile_name]
            if requires:
                keyword_requires[kw] = requires
            else:
                keyword_requires.pop(kw, None)

    return {
        "titleMultiplier": title_mult,
        "minScoreToInclude": min_score,
        "authorBoosts": author_boosts,
        "cpcBoosts": cpc_boosts,
        "assigneeBoosts": assignee_boosts,
        "effectiveKeywords": effective_keywords,
        "keywordToProfiles": keyword_to_profiles,
        "keywordRequires": keyword_requires,
        "profileRequires": profile_requires,
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


# HTTP statuses that mean "this host/client combination was rejected" rather
# than "try again later". They are retryable, but only after swapping to the
# next header profile — waiting longer with identical headers never helps.
# Statuses arXiv's edge returns when it is shedding load rather than
# answering. Measured 2026-09-18 from a GitHub Actions runner: the endpoint
# 406s *every* request - urllib, raw http.client with curl's exact headers,
# any User-Agent - for windows of several minutes, then serves 45/45 requests
# fine from the same client minutes later. So a 406 here is a "come back
# later", exactly like a 429, and the only thing that beats it is waiting.
_THROTTLE_STATUSES = frozenset({403, 406, 415, 429})


def _read_body(resp):
    """Return the response body, transparently gunzipping when needed.

    We advertise ``Accept-Encoding: gzip`` in the primary header profile, so
    we have to be able to decode it.
    """
    body = resp.read()
    encoding = ""
    try:
        raw = resp.headers.get("Content-Encoding")
        if isinstance(raw, str):
            encoding = raw.lower()
    except Exception:  # noqa: BLE001 — header access must never sink a fetch
        encoding = ""
    if "gzip" in encoding:
        try:
            return gzip.decompress(body)
        except (OSError, EOFError) as e:
            print(f"  Warning: could not gunzip response ({e}), using raw bytes")
    return body


def _arxiv_get(url, label, max_retries=_DEFAULT_MAX_RETRIES):
    """Fetch a URL from arXiv with polite retries.

    Retries on the throttle statuses (403/406/415/429), 5xx, socket timeouts,
    and transient URL errors. Honors the server's Retry-After header when
    present; otherwise uses exponential backoff with jitter, capped at
    _MAX_BACKOFF, so a single request can outlast a multi-minute rejection
    window. The header profile is rotated on each throttled retry as a free
    second chance, but rotating is *not* the strategy — waiting is, and
    running out of profiles never ends the retry loop.

    Aborts (returns None) if the wall-clock budget set in main() is exhausted —
    so a slow/hung arXiv can't run the GH Actions job clock out.
    """
    profile_idx = 0
    for attempt in range(max_retries):
        if _budget_exceeded():
            print(f"  Aborting {label}: time budget exhausted")
            return None
        try:
            req = urllib.request.Request(
                url,
                headers=dict(_HEADER_PROFILES[profile_idx]),
            )
            resp = urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
            return _read_body(resp)
        except urllib.error.HTTPError as e:
            retryable = e.code in _THROTTLE_STATUSES or 500 <= e.code < 600
            if not retryable or attempt == max_retries - 1:
                print(f"  Warning: Failed to fetch {label}: {e}")
                return None
            if e.code in _THROTTLE_STATUSES:
                profile_idx = (profile_idx + 1) % len(_HEADER_PROFILES)
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


# Wall-clock reserved for the RSS fallback, on top of whatever the API spent.
# The fallback exists for the case where the API burned the entire budget, so
# it cannot share that budget — it needs its own.
_FALLBACK_BUDGET = 90


@contextlib.contextmanager
def _reserve_budget(seconds):
    """Grant a fresh deadline for a nested block, even if the budget is spent.

    The opposite of :func:`_sub_budget`: this *extends*. Used only for the RSS
    fallback, which runs precisely when the API has exhausted the budget — it
    would otherwise abort before issuing a single request, which is exactly
    what happened on run 149.
    """
    global _BUDGET_DEADLINE
    previous = _BUDGET_DEADLINE
    _BUDGET_DEADLINE = time.monotonic() + seconds
    try:
        yield
    finally:
        _BUDGET_DEADLINE = previous


@contextlib.contextmanager
def _sub_budget(seconds):
    """Temporarily tighten the wall-clock deadline for a nested block.

    Only ever *shortens* the active budget — an outer deadline set by
    ``set_budget`` still wins. Used to give each API host a bounded slice of
    the run's network budget so the first host cannot spend it all and leave
    the failover host no room to try.
    """
    global _BUDGET_DEADLINE
    previous = _BUDGET_DEADLINE
    proposed = time.monotonic() + seconds
    _BUDGET_DEADLINE = proposed if previous is None else min(previous, proposed)
    try:
        yield
    finally:
        _BUDGET_DEADLINE = previous


def _api_bases():
    """Return the arXiv API endpoints to try, in order.

    ``SCQ_ARXIV_API_BASE`` pins a single endpoint (comma-separated for an
    explicit ordered list) — useful on a network where one of the hosts is
    unroutable, and for tests.
    """
    override = os.environ.get("SCQ_ARXIV_API_BASE", "").strip()
    if override:
        return [b.strip() for b in override.split(",") if b.strip()]
    return [ARXIV_API, *ARXIV_API_MIRRORS]


def _arxiv_get_any(params, label, max_retries=_DEFAULT_MAX_RETRIES):
    """Run one query against each API host until one answers.

    ``_arxiv_get`` already exhausts retries and header profiles per host; this
    adds the outer host failover, so a host that is blocked, blackholed or
    rate-limited for this runner's egress IP does not sink the whole digest.
    """
    query = urllib.parse.urlencode(params)
    bases = _api_bases()
    for i, base in enumerate(bases):
        if _budget_exceeded():
            print(f"  Aborting {label}: time budget exhausted")
            return None
        host_label = (
            label if len(bases) == 1 else f"{label} via {urllib.parse.urlparse(base).netloc}"
        )
        # Cap what this host may spend so a 429 storm on the first host still
        # leaves the failover host a chance to answer.
        remaining = _budget_remaining()
        hosts_left = len(bases) - i
        if remaining is not None and hosts_left > 1:
            ctx = _sub_budget(max(_MIN_HOST_BUDGET, remaining / hosts_left))
        else:
            ctx = contextlib.nullcontext()
        with ctx:
            data = _arxiv_get(f"{base}?{query}", host_label, max_retries=max_retries)
        if data is not None:
            return data
        if i + 1 < len(bases):
            print(f"  Failing over to {urllib.parse.urlparse(bases[i + 1]).netloc}...")
    return None


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
    xml_data = _arxiv_get_any(params, "combined query")

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
            cat_xml = _arxiv_get_any(params, cat)
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
        # The Atom API gave us nothing. Before failing the whole run, try the
        # RSS feeds: on 2026-09-18 the /api/query origin 406'd every request
        # for hours while rss.arxiv.org served the same announcements fine.
        # RSS only covers the latest announcement batch, so this recovers
        # today's papers rather than the full window - which still beats
        # sending nothing, and cross-run dedup keeps the next run correct.
        print("  API returned nothing — falling back to the RSS feeds...")
        # `None` = RSS could not be reached at all; `[]` = RSS answered and
        # arXiv had announced nothing. Those must not collapse together: the
        # first is a failure, the second is a fact about the day.
        rss_papers = None
        try:
            from scq.arxiv.rss import fetch_rss_papers

            with _reserve_budget(_FALLBACK_BUDGET):
                rss_papers = fetch_rss_papers(categories, days_back=days_back)
        except ArxivFetchError as e:
            print(f"  RSS fallback unreachable: {e}")
        except Exception as e:  # noqa: BLE001 — fallback must not mask the real error
            print(f"  Warning: RSS fallback failed: {e}")

        if rss_papers:
            print(f"  RSS fallback recovered {len(rss_papers)} paper(s)")
            for cat in categories:
                n = sum(1 for p in rss_papers if cat in p.get("categories", []))
                print(f"  {cat}: {n} papers (via RSS)")
            return rss_papers
        if rss_papers is not None:
            # Feeds served a valid, item-less response — arXiv announces
            # Sunday-Friday, so weekends are legitimately empty. Reporting a
            # fetch failure here would be wrong, and would suppress the
            # "no new papers" note that exists precisely for this.
            print("  RSS reachable but arXiv announced nothing — a genuinely quiet day.")
            return []
        raise ArxivFetchError(
            "arXiv returned nothing usable on any API host and the RSS "
            "fallback was empty too (likely rate-limit, a rejecting origin, "
            "timeout, 5xx, or an exhausted network budget)"
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


def _is_acronym(keyword: str) -> bool:
    """True for single-token keywords with 2+ capitals/digits (TiN, TEM, T1, cQED)."""
    return " " not in keyword and sum(c.isupper() or c.isdigit() for c in keyword) >= 2


@functools.lru_cache(maxsize=1024)
def _keyword_regex(keyword: str) -> re.Pattern[str]:
    if _is_acronym(keyword):
        return re.compile(r"(?<!\w)" + re.escape(keyword) + r"s?(?!\w)", re.IGNORECASE)
    return re.compile(r"(?<!\w)" + re.escape(keyword), re.IGNORECASE)


def _count_keyword(keyword: str, text: str) -> int:
    """Count word-anchored, case-insensitive occurrences of ``keyword`` in ``text``.

    A plain substring count let short keywords fire inside unrelated words
    ("TiN" in "distinct", "TEM" in "temperature", "STEM" in "system", "MBE"
    in "number"), inflating off-topic papers past relevant ones and out of
    the email's top slots. So:

    - Phrases/words anchor the left edge only, keeping inflections:
      "superconducting resonator" still matches "resonators".
    - Acronyms (see ``_is_acronym``) must be a whole word (plural "s" allowed)
      and must be written like an acronym — something after the first letter
      capitalised or a digit — so "stem from" and "drag force" don't count as
      STEM/DRAG while "TiN", "TLSs" and "cQED" do.
    """
    if not keyword:
        return 0
    hits = _keyword_regex(keyword).finditer(text)
    if not _is_acronym(keyword):
        return sum(1 for _ in hits)
    return sum(1 for m in hits if any(c.isupper() or c.isdigit() for c in m.group()[1:]))


def _requires_met(requires: tuple[str, ...], *texts: str) -> bool:
    """True when any anchor of a gated profile (``requires``) appears in ``texts``."""
    return any(_count_keyword(a, t) for a in requires for t in texts)


def _score_paper_simple(paper: dict) -> float:
    """Score using the flat ``_FALLBACK_KEYWORDS`` dict (pre-profile algorithm).

    Identical to the scoring logic that existed before the relevance-config
    overhaul: title hits count 2x abstract hits, no author boosts, no
    profile focus multipliers. ``paper`` is mutated in-place.
    """
    title = paper["title"]
    text = paper["title"] + " " + paper["abstract"]

    score: float = 0.0
    matched_keywords: list[str] = []

    for keyword, weight in _FALLBACK_KEYWORDS.items():
        title_hits = _count_keyword(keyword, title)
        abstract_hits = _count_keyword(keyword, text) - title_hits
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

    keyword_requires: dict[str, tuple[str, ...]] = cfg.get("keywordRequires", {})

    title = paper["title"]
    abstract = paper["abstract"]

    score: float = 0.0
    matched_keywords: list[str] = []
    matched_profiles: set[str] = set()
    # A profile with ``requires`` only counts when one of its anchors appears,
    # so generic terms ("ALD", "dephasing") score in superconducting papers
    # without dragging in battery chemistry or abstract decoherence theory.
    unlocked: dict[tuple[str, ...], bool] = {}

    for keyword, eff_weight in effective_keywords.items():
        requires = keyword_requires.get(keyword)
        if requires:
            if requires not in unlocked:
                unlocked[requires] = _requires_met(requires, title, abstract)
            if not unlocked[requires]:
                continue
        title_hits = _count_keyword(keyword, title)
        abstract_hits = _count_keyword(keyword, abstract)
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
