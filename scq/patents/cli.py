"""``scq patents`` — fetch, process, and inspect patents.

Mirrors the arXiv add-paper split between a network leg (runs on the
host / locally) and a DB leg (runs in the sandbox):

    scq patents fetch <number>      # network: PatentsView -> inbox/<num>_patent.json
    scq patents process <number>    # DB: inbox JSON -> patents table
    scq patents fetch <number> --process   # both, when the network is reachable
    scq patents show <number>       # print a stored patent

The API key comes from the ``patentsview_api_key`` secret
(``scq config set-secret patentsview_api_key``) or the
``SCQ_PATENTSVIEW_API_KEY`` env var; ``--api-key`` overrides both.

See plans/patent-scraping.md and the summarize-patent skill for the
plain-English summarization step that follows ``process``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .normalize import Patent, parse_patent_number


def _inbox_json_path(canonical: str) -> Path:
    from scq.config.paths import paths as get_paths

    inbox = get_paths().inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox / f"{canonical}_patent.json"


def _resolve_api_key(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    from scq.config import secrets as secrets_mod

    return secrets_mod.get("patentsview_api_key")


def _patent_to_json(p: Patent) -> dict:
    """Serialize a Patent to a plain dict for the inbox file."""
    d = dataclasses.asdict(p)
    d["independent_claims"] = p.independent_claims  # property, not a field
    return d


def _patent_from_json(d: dict) -> Patent:
    """Rebuild a Patent from an inbox dict (ignores derived properties)."""
    fields = {f.name for f in dataclasses.fields(Patent)}
    return Patent(**{k: v for k, v in d.items() if k in fields})


def _cmd_fetch(args: argparse.Namespace) -> int:
    from .providers import patentsview

    api_key = _resolve_api_key(args.api_key)
    try:
        patent = patentsview.fetch_patent(
            args.number, api_key=api_key, fetch_claims=not args.no_claims
        )
    except ValueError as e:  # missing api key / bad number
        print(f"error: {e}", file=sys.stderr)
        return 2
    except LookupError as e:  # no such patent
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — network/parse failures
        print(f"error: failed to fetch {args.number}: {e}", file=sys.stderr)
        return 1

    out = _inbox_json_path(patent.number)
    out.write_text(json.dumps(_patent_to_json(patent), indent=2, ensure_ascii=False), "utf-8")
    print(f"fetched {patent.number}: {patent.title!r}")
    print(f"  assignee: {patent.assignee or '(unknown)'}")
    print(f"  claims: {len(patent.claims)} ({len(patent.independent_claims)} independent)")
    print(f"  saved -> {out}")

    if args.process:
        return _process_number(patent.number)
    print(f"  next: scq patents process {patent.number}")
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    info = parse_patent_number(args.number)
    return _process_number(info["canonical"])


def _process_number(canonical: str) -> int:
    from scq.db.connection import connect

    from .store import upsert_patent

    path = _inbox_json_path(canonical)
    if not path.exists():
        print(
            f"error: no inbox file at {path}. Run 'scq patents fetch {canonical}' first.",
            file=sys.stderr,
        )
        return 1
    patent = _patent_from_json(json.loads(path.read_text("utf-8")))
    conn = connect()
    try:
        upsert_patent(conn, patent)
    finally:
        conn.close()
    print(f"stored {canonical} in patents table")
    print("  next: use the summarize-patent skill to add plain-English summaries")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    from scq.db.connection import connect

    from .store import get_patent

    info = parse_patent_number(args.number)
    conn = connect()
    try:
        rec = get_patent(conn, info["canonical"])
    finally:
        conn.close()
    if rec is None:
        print(f"no patent {info['canonical']} in the database", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        return 0
    print(f"{rec['number']} — {rec['title']}")
    print(f"  assignee:   {rec['assignee'] or '(unknown)'}")
    print(f"  inventors:  {rec['short_inventors'] or rec['inventors']}")
    print(f"  filed/granted: {rec['filing_date']} / {rec['grant_date']}")
    print(f"  CPC: {', '.join(rec['cpc_codes']) or '(none)'}")
    print(f"  claims: {len(rec['claims'])} ({len(rec['independent_claims'])} independent)")
    print(f"  summary: {rec['plain_summary'] or '(not yet summarized)'}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scq patents", description="fetch and inspect patents")
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")

    p_fetch = sub.add_parser("fetch", help="fetch a patent from PatentsView into the inbox")
    p_fetch.add_argument("number", help="patent number, e.g. US10374134B2 or 10374134")
    p_fetch.add_argument("--api-key", help="PatentsView API key (overrides secret/env)")
    p_fetch.add_argument("--no-claims", action="store_true", help="skip the claims fetch")
    p_fetch.add_argument(
        "--process", action="store_true", help="also insert into the DB (needs network locally)"
    )
    p_fetch.set_defaults(func=_cmd_fetch)

    p_proc = sub.add_parser("process", help="insert a fetched patent from the inbox into the DB")
    p_proc.add_argument("number", help="patent number (must already be fetched)")
    p_proc.set_defaults(func=_cmd_process)

    p_show = sub.add_parser("show", help="print a stored patent")
    p_show.add_argument("number", help="patent number")
    p_show.add_argument("--json", action="store_true", help="emit the full record as JSON")
    p_show.set_defaults(func=_cmd_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
