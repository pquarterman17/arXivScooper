"""Daily-digest orchestrator (plan #13).

The thin glue layer that ties :mod:`scq.arxiv.search`,
:mod:`scq.arxiv.render`, and :mod:`scq.arxiv.email` together. Argument
parsing and runtime choices (mock data, weekend smart-lookback, network
budget) live here; everything else is in the focused modules.

Module entry point: ``python -m scq.arxiv.digest [--days 3] [...]``
or via the CLI: ``scq digest [...]``. The legacy
``python tools/arxiv_digest.py`` invocation is preserved by a thin shim.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from scq.arxiv import search as _search
from scq.arxiv.email import send_email_digest
from scq.arxiv.render import generate_html_digest
from scq.arxiv.search import (
    ARXIV_CATEGORIES,
    fetch_arxiv_papers,
    rank_papers,
)

# ─── Config loaders (plan #6 Python pass) ───
#
# digest.py historically took every behavior knob via CLI flags. The
# user_config-backed loader is now the canonical source of truth shared
# with the JS frontend; CLI flags stay supported and override the config
# when supplied. When the loader fails (missing schema, malformed file,
# fresh checkout without the config system), we fall back to the same
# constants the legacy code path used.

_DIGEST_DEFAULTS = {
    "maxPapers": None,  # None = no cap (matches legacy behavior)
    "lookbackDays": 3,  # legacy --days default
    "minRelevanceScore": 0,  # 0 = no filtering (legacy "send everything ranked")
    "includeSources": [],  # empty = all enabled (matches schema description)
}


def _load_digest_config():
    """Return the merged digest config or defaults if the loader fails.

    Treats ``null`` in user_config the same as a missing key: a
    hand-edited ``digest.json`` with ``"minRelevanceScore": null`` would
    otherwise flow ``None`` into the filter and crash on ``>=``. The
    schema rejects null at validation time, but ``load_config`` collects
    errors rather than raising, so the bad value still reaches us.
    """
    try:
        from scq.config.user import load_config

        result = load_config("digest")
        data = result.data or {}

        def _coerce(key):
            value = data.get(key)
            return value if value is not None else _DIGEST_DEFAULTS[key]

        return {
            "maxPapers": _coerce("maxPapers"),
            "lookbackDays": _coerce("lookbackDays"),
            "minRelevanceScore": _coerce("minRelevanceScore"),
            "includeSources": _coerce("includeSources"),
        }
    except Exception as e:  # noqa: BLE001 — keep the workflow robust
        print(f"  [config] digest config unreadable, using defaults: {e}")
        return dict(_DIGEST_DEFAULTS)


def _load_search_categories():
    """Return arxivCategories from search-sources config, fallback to constants."""
    try:
        from scq.config.user import load_config

        result = load_config("search-sources")
        cats = (result.data or {}).get("arxivCategories")
        if isinstance(cats, list) and cats:
            return list(cats)
    except Exception as e:  # noqa: BLE001
        print(f"  [config] search-sources config unreadable, using defaults: {e}")
    return list(ARXIV_CATEGORIES)


def _apply_digest_filters(papers, *, min_score, max_count):
    """Drop low-relevance papers, then cap to ``max_count`` (None = no cap).

    Papers must already carry a ``relevance_score`` from
    :func:`scq.arxiv.search.rank_papers`. Sort order is preserved for
    ties — the caller has already ranked.
    """
    if not papers:
        return papers
    # Defensive: if a caller (or a hand-edited config that slipped past
    # the loader's coerce-null pass) supplies ``None`` for the floor,
    # treat as "no filter" rather than crashing on the >= comparison.
    if min_score is None:
        min_score = 0
    filtered = [p for p in papers if p.get("relevance_score", 0) >= min_score]
    if max_count is not None and max_count > 0:
        filtered = filtered[:max_count]
    return filtered


# Where finished digest HTMLs land. `paths().digests_dir` is the canonical
# resolver and respects user_config/paths.toml + SCQ_DIGESTS_DIR. Falls back
# to repo-relative `digests/` for source-checkout invocations before
# ``pip install``.
try:
    from scq.config.paths import paths as _scq_paths

    DIGEST_DIR = str(_scq_paths().digests_dir)
except Exception:  # noqa: BLE001
    DIGEST_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "digests",
    )


# ─── Mock data (used by --test mode) ───


def generate_mock_papers():
    """Generate mock papers for testing when arXiv API is unavailable."""
    now = datetime.now(timezone.utc)
    return [
        {
            "id": "2603.99001",
            "title": "Reduced Dielectric Loss in Tantalum-Based Superconducting Resonators via Surface Treatment",
            "authors": "A. Smith, B. Jones, C. Lee, D. Patel",
            "short_authors": "Smith et al.",
            "abstract": (
                "We demonstrate a 3x reduction in dielectric loss tangent in tantalum "
                "superconducting microwave resonators via a novel surface treatment "
                "process. Quality factors approaching 5x10^6 are achieved at single "
                "photon power. The treatment removes amorphous oxide layers and "
                "passivates the substrate, eliminating two-level system loss "
                "mechanisms that previously dominated transmon qubit coherence."
            ),
            "published": now.isoformat(),
            "categories": ["cond-mat.supr-con", "quant-ph"],
            "pdf_url": "https://arxiv.org/pdf/2603.99001",
            "abs_url": "https://arxiv.org/abs/2603.99001",
        },
        {
            "id": "2603.99002",
            "title": "Generic Quantum Algorithms (off-topic test)",
            "authors": "X. Doe",
            "short_authors": "Doe",
            "abstract": "Generic abstract that should score low on relevance.",
            "published": now.isoformat(),
            "categories": ["quant-ph"],
            "pdf_url": "https://arxiv.org/pdf/2603.99002",
            "abs_url": "https://arxiv.org/abs/2603.99002",
        },
    ]


# ─── Weekend smart lookback ───


def compute_effective_days_back(days_back):
    """Return ``(effective_days, note)`` adjusting for weekends.

    arXiv announces papers Sunday–Friday US Eastern. A 3-day lookback
    that runs early Saturday catches Wed/Thu/Fri; one that runs early
    Monday only catches Sun (which is sparse). On Mondays, extend to
    cover the previous business day. ``note`` is a human-readable
    explanation when the value was bumped, else ``""``.
    """
    today = datetime.now()
    weekday = today.weekday()  # 0=Mon ... 6=Sun
    note = ""
    if weekday == 0:  # Monday
        days_back = max(days_back, 4)
        note = f"Monday - extending lookback to {days_back} days to cover Fri+Sat+Sun"
    elif weekday == 6:  # Sunday
        days_back = max(days_back, 3)
        note = f"Sunday - extending lookback to {days_back} days"
    return days_back, note


# ─── GitHub Actions job summary ───


def _write_github_step_summary(
    *,
    digest_date: str,
    n_fetched: int,
    n_relevant: int,
    n_digest: int,
    email_status: str,
    artifact_run_id: str,
) -> None:
    """Append a Markdown summary to $GITHUB_STEP_SUMMARY if running in CI.

    The file is append-only per the Actions spec; a missing env var means
    we are running locally and the function is a no-op.

    Args:
        digest_date: ISO date string (YYYY-MM-DD) of the digest run.
        n_fetched: Total papers fetched from arXiv before ranking/filters.
        n_relevant: Papers that matched SCQ keywords (score >= 5).
        n_digest: Papers included in the final digest after all filters.
        email_status: One of ``"sent"``, ``"failed"``, or ``"skipped"``.
        artifact_run_id: The GITHUB_RUN_ID string (empty when not in CI).
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return

    if email_status == "sent":
        email_line = "Sent"
    elif email_status == "failed":
        email_line = "**Failed** (check SMTP secrets)"
    else:
        email_line = "Skipped (`--no-email` or secrets absent)"

    artifact_note = (
        f"[Download artifact](https://github.com/pquarterman17/arXivScooper/"
        f"actions/runs/{artifact_run_id}) (30-day retention)"
        if artifact_run_id
        else "Artifact available on the Actions run page (30-day retention)"
    )

    summary = (
        f"## arXiv Digest — {digest_date}\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Papers fetched | {n_fetched} |\n"
        f"| Relevant (score ≥ 5) | {n_relevant} |\n"
        f"| In digest (after filters) | {n_digest} |\n"
        f"| Email | {email_line} |\n\n"
        f"{artifact_note}\n"
    )

    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(summary)
    except OSError as exc:
        print(f"  [summary] could not write to GITHUB_STEP_SUMMARY: {exc}")


# ─── Main ───


def _positive_int(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {n}")
    return n


def main(argv=None):
    parser = argparse.ArgumentParser(description="SCQ arXiv Daily Digest")
    # Config-backed flags use sentinel `None` so we can distinguish "not
    # specified" (→ fall back to digest config) from "explicitly set to N".
    parser.add_argument(
        "--days", type=int, default=None, help="Days to look back. Defaults to digest.lookbackDays."
    )
    parser.add_argument("--no-email", action="store_true", help="Skip email, generate HTML only")
    parser.add_argument("--test", action="store_true", help="Use mock data (no network)")
    parser.add_argument(
        "--max-results", type=int, default=500, help="Max papers per category at fetch time"
    )
    parser.add_argument(
        "--max-papers",
        type=_positive_int,
        default=None,
        help="Cap on papers in the digest after ranking. Defaults to digest.maxPapers.",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Drop papers below this relevance score. Defaults to digest.minRelevanceScore.",
    )
    parser.add_argument(
        "--smart-weekend",
        action="store_true",
        help="Auto-extend lookback on weekends so Friday's papers are not missed",
    )
    parser.add_argument(
        "--budget-seconds",
        type=int,
        default=600,
        help="Hard wall-clock budget for arXiv fetching (default: 600s). "
        "Leaves runway under the GH Actions 15-min job timeout.",
    )
    parser.add_argument(
        "--require-email",
        action="store_true",
        help="Exit 2 if email fails or is skipped (for CI use). "
        "Without this flag, email failure prints a warning but exits 0.",
    )
    args = parser.parse_args(argv)

    digest_cfg = _load_digest_config()
    categories = _load_search_categories()

    # CLI overrides config; config overrides hardcoded defaults.
    days_back = args.days if args.days is not None else digest_cfg["lookbackDays"]
    max_papers = args.max_papers if args.max_papers is not None else digest_cfg["maxPapers"]
    min_score = args.min_score if args.min_score is not None else digest_cfg["minRelevanceScore"]

    # Set the network deadline. Anything in _arxiv_get that would push past
    # this aborts cleanly with a logged warning. 0/negative disables.
    if args.budget_seconds > 0:
        _search.set_budget(args.budget_seconds)
        print(f"  Network budget: {args.budget_seconds}s")

    # Apply weekend adjustment when requested
    if args.smart_weekend:
        days_back, note = compute_effective_days_back(days_back)
        if note:
            print(f"  [!] {note}")

    digest_date = datetime.now().strftime("%Y-%m-%d")
    print(f"SCQ arXiv Digest - {digest_date}")
    print(f"  Categories: {', '.join(categories)}")
    print(f"  Looking back: {days_back} day(s)")
    if max_papers:
        print(f"  Cap: {max_papers} papers (post-rank)")
    if min_score:
        print(f"  Min relevance: {min_score}")

    # Fetch papers
    if args.test:
        print("\n  Using mock data for testing...")
        papers = generate_mock_papers()
    else:
        print("\nFetching from arXiv API...")
        papers = fetch_arxiv_papers(
            categories,
            days_back=days_back,
            max_results=args.max_results,
        )

    if not papers:
        print("\nNo new papers found - sending empty digest so the run is visible.")
        papers = []
    else:
        print(f"\nRanking {len(papers)} papers...")
        papers = rank_papers(papers)

    # Apply digest filters (plan #6) — config-driven floor + cap.
    pre_filter = len(papers)
    papers = _apply_digest_filters(papers, min_score=min_score, max_count=max_papers)
    if pre_filter != len(papers):
        print(f"  Filters dropped {pre_filter - len(papers)} of {pre_filter} ranked papers")

    relevant = sum(1 for p in papers if p["relevance_score"] >= 5)
    print(f"  {relevant} papers match SCQ keywords")

    # Generate HTML digest
    print("\nGenerating digest...")
    os.makedirs(DIGEST_DIR, exist_ok=True)
    digest_path = os.path.join(DIGEST_DIR, f"digest_{digest_date}.html")
    generate_html_digest(papers, digest_date, digest_path)

    # Send email
    email_status = "skipped"
    if not args.no_email:
        ok = send_email_digest(papers, digest_date)
        if ok:
            email_status = "sent"
        else:
            email_status = "failed"
            if args.require_email:
                print(
                    "ERROR: --require-email set but email failed to send",
                    file=sys.stderr,
                )
                _write_github_step_summary(
                    digest_date=digest_date,
                    n_fetched=pre_filter,
                    n_relevant=relevant,
                    n_digest=len(papers),
                    email_status=email_status,
                    artifact_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                )
                sys.exit(2)
    else:
        print("  Email skipped (--no-email)")

    _write_github_step_summary(
        digest_date=digest_date,
        n_fetched=pre_filter,
        n_relevant=relevant,
        n_digest=len(papers),
        email_status=email_status,
        artifact_run_id=os.environ.get("GITHUB_RUN_ID", ""),
    )

    print(f"\nDone! {len(papers)} papers processed.")
    return digest_path


if __name__ == "__main__":
    main()
