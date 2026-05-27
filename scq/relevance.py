"""Relevance config management commands.

Exposes four sub-subcommands accessible via ``scq relevance <sub>``:

    scq relevance show              -- print effective config summary
    scq relevance learn             -- analyse read/starred papers and suggest config updates
    scq relevance test <id|title>   -- score a single paper and explain every match
    scq relevance mode <simple|smart> -- switch ranking mode in digest config

Usage::

    scq relevance show
    scq relevance learn
    scq relevance test 2501.12345
    scq relevance test "tantalum resonator loss"
    scq relevance mode simple
    scq relevance mode smart
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ─── helpers ───


def _get_db_conn() -> sqlite3.Connection | None:
    """Return a read-only connection to the paper DB, or None if not found."""
    try:
        from scq.config.paths import paths as get_paths

        db_path = get_paths().db_path
    except Exception:  # noqa: BLE001
        db_path = Path("data/arxiv_scooper.db")

    if not db_path.is_file():
        print(f"  [relevance] database not found at {db_path}", file=sys.stderr)
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "for",
        "on",
        "with",
        "to",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "by",
        "from",
        "that",
        "this",
        "we",
        "our",
        "their",
        "its",
        "as",
        "at",
        "via",
        "using",
        "show",
        "shown",
        "study",
        "studies",
        "present",
        "report",
        "based",
        "can",
        "has",
        "have",
        "had",
        "not",
        "also",
        "such",
        "which",
        "into",
        "through",
        "between",
        "within",
        "under",
        "over",
        "high",
        "low",
        "new",
        "both",
        "these",
        "two",
        "three",
        "one",
        "than",
        "more",
        "used",
        "use",
        "when",
        "here",
        "where",
        "while",
        "during",
        "due",
        "after",
        "being",
        "very",
        "they",
        "result",
        "results",
        "large",
        "small",
        "order",
        "find",
        "found",
        "paper",
    }
)


def _ngrams(tokens: list[str], n: int) -> list[str]:
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9\-]{1,}\b", text.lower())
    return [w for w in words if w not in _STOPWORDS]


# ─── sub-commands ───


def _cmd_show(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Print a human-readable summary of the effective relevance config."""
    from scq.arxiv.search import (
        _get_ranking_mode,
        _load_relevance_config,
        invalidate_relevance_cache,
    )

    invalidate_relevance_cache()
    cfg = _load_relevance_config()

    mode = _get_ranking_mode()
    print(f"Ranking mode      : {mode}")
    print(f"titleMultiplier   : {cfg['titleMultiplier']}")
    print(f"minScoreToInclude : {cfg['minScoreToInclude']}")

    boosts = cfg.get("authorBoosts", {})
    if boosts:
        print(f"\nAuthor boosts ({len(boosts)}):")
        for name, pts in sorted(boosts.items(), key=lambda x: -x[1]):
            print(f"  {pts:+.0f}  {name}")
    else:
        print("\nAuthor boosts: (none configured)")

    cpc = cfg.get("cpcBoosts", {})
    if cpc:
        print(f"\nCPC boosts ({len(cpc)}) [patents]:")
        for code, pts in sorted(cpc.items(), key=lambda x: -x[1]):
            print(f"  {pts:+.0f}  {code}")
    assignee = cfg.get("assigneeBoosts", {})
    if assignee:
        print(f"\nAssignee boosts ({len(assignee)}) [patents]:")
        for name, pts in sorted(assignee.items(), key=lambda x: -x[1]):
            print(f"  {pts:+.0f}  {name}")

    # Group effective keywords by profile
    kw_to_profiles = cfg.get("keywordToProfiles", {})
    by_profile: dict[str, list[tuple[str, float]]] = {}
    for kw, eff in cfg["effectiveKeywords"].items():
        for pname in kw_to_profiles.get(kw, ["(unknown)"]):
            by_profile.setdefault(pname, []).append((kw, eff))

    print(f"\nKeyword profiles ({len(by_profile)} active):")
    for pname in sorted(by_profile):
        items = sorted(by_profile[pname], key=lambda x: -x[1])
        pos = sum(1 for _, w in items if w > 0)
        neg = sum(1 for _, w in items if w < 0)
        print(f"  {pname}: {len(items)} keywords ({pos} positive, {neg} negative)")

    total = len(cfg["effectiveKeywords"])
    print(f"\nTotal effective keywords: {total}")
    return 0


def _cmd_learn(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Analyse read and starred papers; write relevance_suggestions.json."""
    conn = _get_db_conn()
    if conn is None:
        return 1

    try:
        # High-interest papers: read or starred (priority >= 2)
        hi_rows = conn.execute(
            """
            SELECT p.title, p.authors, p.group_name
            FROM papers p
            LEFT JOIN read_status rs ON rs.paper_id = p.id
            WHERE rs.is_read = 1 OR rs.priority >= 2
            """,
        ).fetchall()

        # Low-interest papers: explicitly low-priority (priority = 0, not read)
        lo_rows = conn.execute(
            """
            SELECT p.title
            FROM papers p
            LEFT JOIN read_status rs ON rs.paper_id = p.id
            WHERE rs.priority = 0 AND (rs.is_read IS NULL OR rs.is_read = 0)
            """,
        ).fetchall()
    finally:
        conn.close()

    if not hi_rows:
        print("No read or starred papers found — add some papers and mark them read/starred first.")
        return 0

    # ── Author suggestions ──
    group_counter: Counter = Counter()
    for row in hi_rows:
        if row["group_name"]:
            group_counter[row["group_name"]] += 1

    max_count = max(group_counter.values()) if group_counter else 1
    author_suggestions: dict[str, int] = {}
    for name, count in group_counter.most_common(10):
        # Scale to 1-5 proportional to frequency
        weight = max(1, round(5 * count / max_count))
        author_suggestions[name] = weight

    # ── Keyword suggestions from hi-interest titles ──
    hi_tokens_all: list[str] = []
    for row in hi_rows:
        hi_tokens_all.extend(_tokenize(row["title"]))

    hi_bigrams = _ngrams(hi_tokens_all, 2)
    hi_trigrams = _ngrams(hi_tokens_all, 3)
    hi_phrase_counter: Counter = Counter(hi_bigrams + hi_trigrams)

    # Load existing keyword set so we don't re-suggest them
    from scq.arxiv.search import _load_relevance_config, invalidate_relevance_cache

    invalidate_relevance_cache()
    cfg = _load_relevance_config()
    existing_kws = {k.lower() for k in cfg["effectiveKeywords"]}

    new_keyword_suggestions: list[dict[str, Any]] = []
    for phrase, count in hi_phrase_counter.most_common(40):
        if phrase in existing_kws:
            continue
        if count < 2:
            break
        new_keyword_suggestions.append({"phrase": phrase, "count": count})
        if len(new_keyword_suggestions) >= 20:
            break

    # ── Negative keyword suggestions from lo-interest titles ──
    lo_tokens_all: list[str] = []
    for row in lo_rows:
        lo_tokens_all.extend(_tokenize(row["title"]))

    lo_bigrams = _ngrams(lo_tokens_all, 2)
    lo_trigrams = _ngrams(lo_tokens_all, 3)
    lo_phrase_counter: Counter = Counter(lo_bigrams + lo_trigrams)

    # Only suggest as negative if rare in hi-interest titles
    hi_set = set(hi_bigrams + hi_trigrams)
    neg_suggestions: list[dict[str, Any]] = []
    for phrase, count in lo_phrase_counter.most_common(40):
        if phrase in existing_kws:
            continue
        if phrase in hi_set:
            continue
        if count < 2:
            break
        neg_suggestions.append({"phrase": phrase, "count": count})
        if len(neg_suggestions) >= 10:
            break

    # ── Write suggestions file ──
    try:
        from scq.config.paths import paths as get_paths

        out_path = get_paths().repo_root / "data" / "user_config" / "relevance_suggestions.json"
    except Exception:  # noqa: BLE001
        out_path = Path("data/user_config/relevance_suggestions.json")

    suggestions = {
        "note": (
            "Review these suggestions and merge interesting ones into "
            "data/user_config/relevance.json. Counts are from your read/starred papers."
        ),
        "authorBoosts": author_suggestions,
        "potentialNewKeywords": new_keyword_suggestions,
        "potentialNegativeKeywords": neg_suggestions,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, indent=2, ensure_ascii=False)

    print(f"Analysed {len(hi_rows)} high-interest paper(s), {len(lo_rows)} low-interest paper(s).")
    print(f"Author suggestions: {len(author_suggestions)}")
    print(f"New keyword suggestions: {len(new_keyword_suggestions)}")
    print(f"Negative keyword suggestions: {len(neg_suggestions)}")
    print(f"\nWrote: {out_path}")
    print("Review the file, then copy entries into data/user_config/relevance.json.")
    return 0


def _cmd_mode(args: argparse.Namespace) -> int:
    """Set the rankingMode field in data/user_config/digest.json."""
    new_mode = args.mode

    try:
        from scq.config.paths import paths as get_paths

        digest_path = get_paths().repo_root / "data" / "user_config" / "digest.json"
    except Exception:  # noqa: BLE001
        digest_path = Path("data/user_config/digest.json")

    # Read existing file (or start from empty dict)
    if digest_path.is_file():
        try:
            with open(digest_path, encoding="utf-8") as f:
                existing: dict = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  Error reading {digest_path}: {exc}", file=sys.stderr)
            return 1
    else:
        existing = {}

    existing["rankingMode"] = new_mode

    digest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(digest_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Ranking mode set to: {new_mode}")
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    """Score a single paper (by arXiv ID or title fragment) and explain every match."""
    from scq.arxiv.search import (
        _get_ranking_mode,
        _load_relevance_config,
        invalidate_relevance_cache,
        score_paper,
    )

    query = args.query

    # Try to find the paper in the DB
    paper: dict | None = None
    conn = _get_db_conn()
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT id, title, authors, summary FROM papers WHERE id = ? LIMIT 1",
                (query,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT id, title, authors, summary FROM papers "
                    "WHERE LOWER(title) LIKE ? LIMIT 1",
                    (f"%{query.lower()}%",),
                ).fetchone()
            if row is not None:
                d = dict(row)
                d["abstract"] = d.pop("summary", "") or ""
                paper = d
        finally:
            conn.close()

    if paper is None:
        # Not a paper — maybe it's a stored patent (by number or title).
        rc = _try_score_patent(query)
        if rc is not None:
            return rc
        # Treat the query as a synthetic title with empty abstract
        print(f"  (no DB match for {query!r} — scoring as a synthetic title)")
        paper = {"id": "synthetic", "title": query, "authors": "", "abstract": ""}

    invalidate_relevance_cache()
    cfg = _load_relevance_config()
    mode = _get_ranking_mode()
    raw_score = score_paper(paper)

    print(f"\nPaper: {paper.get('title', '')}")
    print(f"Authors: {paper.get('authors', '')}")
    print(f"\nRanking mode    : {mode}")
    print(f"relevance_score : {paper['relevance_score']:.1f}  (raw: {raw_score:.1f})")
    print(f"minScoreToInclude: {cfg['minScoreToInclude']}")
    included = paper["relevance_score"] >= cfg["minScoreToInclude"]
    print(f"Would be included: {'yes' if included else 'NO (below floor)'}")

    if paper["matched_profiles"]:
        print(f"Matched profiles: {', '.join(paper['matched_profiles'])}")

    if paper["matched_keywords"]:
        print(f"\nMatched keywords ({len(paper['matched_keywords'])}):")
        for kw in paper["matched_keywords"]:
            eff = cfg["effectiveKeywords"].get(kw, 0)
            print(f"  {eff:+.1f}  {kw!r}")
    else:
        print("\nNo keywords matched.")

    boosts = cfg.get("authorBoosts", {})
    authors_lower = paper.get("authors", "").lower()
    matched_authors = [(k, v) for k, v in boosts.items() if k.lower() in authors_lower]
    if matched_authors:
        print("\nAuthor boosts:")
        for name, pts in matched_authors:
            print(f"  {pts:+.0f}  matched '{name}'")
    return 0


def _try_score_patent(query: str) -> int | None:
    """If `query` matches a stored patent, score it and print; else None."""
    conn = _get_db_conn()
    if conn is None:
        return None
    try:
        has_patents = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='patents'"
        ).fetchone()
        if not has_patents:
            return None
        from scq.patents.store import get_patent

        rec = get_patent(conn, query)
        if rec is None:
            row = conn.execute(
                "SELECT number FROM patents WHERE LOWER(title) LIKE ? LIMIT 1",
                (f"%{query.lower()}%",),
            ).fetchone()
            if row is not None:
                rec = get_patent(conn, row["number"])
        if rec is None:
            return None
    finally:
        conn.close()

    from scq.arxiv.search import _load_relevance_config, invalidate_relevance_cache
    from scq.patents.relevance import score_patent

    invalidate_relevance_cache()
    cfg = _load_relevance_config()
    raw = score_patent(rec, cfg)

    print(f"\nPatent: {rec.get('number')} — {rec.get('title', '')}")
    print(f"Assignee: {rec.get('assignee', '')}")
    print(f"\nrelevance_score : {rec['relevance_score']:.1f}  (raw: {raw:.1f})")
    print(f"minScoreToInclude: {cfg['minScoreToInclude']}")
    included = rec["relevance_score"] >= cfg["minScoreToInclude"]
    print(f"Would be included: {'yes' if included else 'NO (below floor)'}")
    if rec["matched_cpc"]:
        print("\nCPC boosts matched:")
        for code in rec["matched_cpc"]:
            print(f"  {cfg['cpcBoosts'][code]:+.0f}  {code}")
    if rec["matched_assignees"]:
        print("\nAssignee boosts matched:")
        for name in rec["matched_assignees"]:
            print(f"  {cfg['assigneeBoosts'][name]:+.0f}  {name}")
    if rec["matched_keywords"]:
        print(f"\nMatched keywords ({len(rec['matched_keywords'])}):")
        for kw in rec["matched_keywords"]:
            print(f"  {cfg['effectiveKeywords'].get(kw, 0):+.1f}  {kw!r}")
    if not (rec["matched_cpc"] or rec["matched_assignees"] or rec["matched_keywords"]):
        print("\nNo CPC / assignee / keyword signals matched.")
    return 0


# ─── argparse wiring ───


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``scq relevance`` subcommand dispatcher."""
    parser = argparse.ArgumentParser(
        prog="scq relevance",
        description="Inspect and improve the config-driven relevance scoring system.",
    )
    sub = parser.add_subparsers(dest="subcmd", metavar="<subcmd>")

    sub.add_parser("show", help="print the current effective relevance config summary")

    sub.add_parser(
        "learn",
        help=(
            "analyse read/starred papers in the DB and write "
            "data/user_config/relevance_suggestions.json"
        ),
    )

    p_test = sub.add_parser(
        "test",
        help="score a single paper (arXiv ID or title fragment) and explain matches",
    )
    p_test.add_argument("query", help="arXiv ID (e.g. 2501.12345) or title substring")

    p_mode = sub.add_parser(
        "mode",
        help="switch ranking mode: 'simple' (flat keywords) or 'smart' (profiles + author boosts)",
    )
    p_mode.add_argument(
        "mode",
        choices=["simple", "smart"],
        help="ranking mode to activate",
    )

    args = parser.parse_args(argv)
    if args.subcmd == "show":
        return _cmd_show(args)
    if args.subcmd == "learn":
        return _cmd_learn(args)
    if args.subcmd == "test":
        return _cmd_test(args)
    if args.subcmd == "mode":
        return _cmd_mode(args)

    parser.print_help()
    return 1
