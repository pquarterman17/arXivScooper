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
    from .providers import google, patentsview

    # Provider dispatch. All providers expose fetch_patent(number, *, http,
    # **kwargs) → Patent; patentsview additionally consumes api_key. Google
    # is the default: keyless, works immediately.
    providers = {"google": google.fetch_patent, "patentsview": patentsview.fetch_patent}
    fetch = providers[args.source]
    kwargs: dict = {"fetch_claims": not args.no_claims}
    if args.source == "patentsview":
        kwargs["api_key"] = _resolve_api_key(args.api_key)

    try:
        patent = fetch(args.number, **kwargs)
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


def _digits(number: str) -> str:
    """The digit run of a patent number, for format-agnostic dedup."""
    try:
        return parse_patent_number(number)["doc_number"]
    except ValueError:
        return number


def _cmd_monitor(args: argparse.Namespace) -> int:
    """Find recent filings by tracked assignees, dedup against the DB.

    Dormant until a PatentsView key is stored (search needs it). With
    --add, new patents are fetched + stored via the chosen source.
    """
    from datetime import date, timedelta

    from scq.db.connection import connect

    from .providers import patentsview
    from .store import existing_numbers

    assignees = args.assignee or []
    if not assignees:
        print("error: pass at least one --assignee NAME", file=sys.stderr)
        return 2
    since = (date.today() - timedelta(days=args.days)).isoformat()
    api_key = _resolve_api_key(args.api_key)

    conn = connect()
    try:
        have = existing_numbers(conn)
    finally:
        conn.close()
    # Dedup on the digit run, not the full number: PatentsView returns bare
    # digits ("10374134") while stored numbers are canonical ("US10374134B2"),
    # so a string compare would never match.
    have_digits = {_digits(n) for n in have}

    total_new = 0
    for name in assignees:
        try:
            hits = patentsview.search_by_assignee(name, api_key=api_key, since=since)
        except ValueError as e:  # no key → dormant
            print(f"error: {e}", file=sys.stderr)
            return 2
        except Exception as e:  # noqa: BLE001
            print(f"  [{name}] search failed: {e}", file=sys.stderr)
            continue
        new = [h for h in hits if _digits(h["number"]) not in have_digits]
        print(f"{name}: {len(hits)} filings since {since}, {len(new)} new")
        for h in new:
            print(f"  + {h['number']}  {h['title'][:70]}")
        total_new += len(new)
        if args.add and new:
            for h in new:
                if _add_via_source(h["number"]) == 0:
                    have_digits.add(_digits(h["number"]))

    print(f"\n{total_new} new patent(s) across {len(assignees)} assignee(s).")
    return 0


def _add_via_source(number: str) -> int:
    """Fetch+store one patent via Google (keyless), for monitor --add."""
    from scq.db.connection import connect

    from .providers import google
    from .store import upsert_patent

    try:
        patent = google.fetch_patent(number)
    except Exception as e:  # noqa: BLE001
        print(f"    (could not add {number}: {e})", file=sys.stderr)
        return 1
    conn = connect()
    try:
        upsert_patent(conn, patent)
    finally:
        conn.close()
    return 0


def _anthropic_llm(model: str):
    """Return an (prompt -> reply) callable backed by the Anthropic SDK.

    Returns None if the SDK isn't installed or no key is configured — the
    caller then falls back to printing the prompt for the interactive
    summarize-patent skill. Key resolves from the 'anthropic_api_key'
    secret or the ANTHROPIC_API_KEY env var.
    """
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        return None
    from scq.config import secrets as secrets_mod

    key = secrets_mod.get("anthropic_api_key")
    if not key:
        return None
    client = anthropic.Anthropic(api_key=key)

    def call(prompt: str) -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")

    return call


def _cmd_summarize(args: argparse.Namespace) -> int:
    from scq.db.connection import connect

    from .store import get_patent, store_summary
    from .summarize import build_summary_prompt, summarize_patent

    info = parse_patent_number(args.number)
    conn = connect()
    try:
        rec = get_patent(conn, info["canonical"])
        if rec is None:
            print(
                f"error: {info['canonical']} not in the DB — fetch+process it first.",
                file=sys.stderr,
            )
            return 1

        if args.print_prompt:
            print(build_summary_prompt(rec))
            return 0

        llm = _anthropic_llm(args.model)
        if llm is None:
            print(
                "No Anthropic LLM available (install the SDK and set the "
                "'anthropic_api_key' secret, or pass --print-prompt). For "
                "interactive use, run the summarize-patent skill instead.\n",
                file=sys.stderr,
            )
            print(build_summary_prompt(rec))
            return 2

        try:
            fields = summarize_patent(rec, llm)
        except Exception as e:  # noqa: BLE001
            print(f"error: summarization failed: {e}", file=sys.stderr)
            return 1
        store_summary(conn, info["canonical"], **fields)
    finally:
        conn.close()

    print(f"summarized {info['canonical']} ({', '.join(fields) or 'no fields'})")
    for k, v in fields.items():
        print(f"  {k}: {v[:100]}{'…' if len(v) > 100 else ''}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scq patents", description="fetch and inspect patents")
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")

    p_fetch = sub.add_parser("fetch", help="fetch a patent into the inbox")
    p_fetch.add_argument("number", help="patent number, e.g. US10374134B2 or 10374134")
    p_fetch.add_argument(
        "--source",
        choices=("google", "patentsview"),
        default="google",
        help="data source (default: google — keyless HTML scrape)",
    )
    p_fetch.add_argument(
        "--api-key", help="PatentsView API key (overrides secret/env; --source patentsview only)"
    )
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

    p_sum = sub.add_parser(
        "summarize", help="LLM-summarize a stored patent's claims into the summary fields"
    )
    p_sum.add_argument("number", help="patent number (must already be stored)")
    p_sum.add_argument(
        "--print-prompt",
        action="store_true",
        help="print the summary prompt instead of calling an LLM",
    )
    p_sum.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Anthropic model id (default: claude-sonnet-4-6)",
    )
    p_sum.set_defaults(func=_cmd_summarize)

    p_mon = sub.add_parser(
        "monitor", help="find recent filings by tracked assignees (needs PatentsView key)"
    )
    p_mon.add_argument("--assignee", action="append", help="assignee org to track (repeatable)")
    p_mon.add_argument("--days", type=int, default=90, help="look-back window in days (default 90)")
    p_mon.add_argument("--add", action="store_true", help="fetch+store new patents (via Google)")
    p_mon.add_argument("--api-key", help="PatentsView API key (overrides secret/env)")
    p_mon.set_defaults(func=_cmd_monitor)

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
