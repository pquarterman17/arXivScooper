"""Command-line interface for the SCQ toolkit.

Currently exposes:

    scq init                               # create + migrate the DB
    scq config <subcommand>                # inspect/manage config + secrets

More subcommands (``serve``, ``fetch``, ``ingest``, ``digest``) will land
with plan item #12 when the ``tools/`` scripts move into ``scq/``.

Usage::

    scq init                               # create DB at paths.db_path
    scq init --force                       # overwrite an existing populated DB
    scq config show                        # all domains, JSON
    scq config show digest                 # one domain
    scq config get digest maxPapers        # one nested key
    scq config validate                    # exit 1 if any domain has errors
    scq config paths                       # resolved filesystem locations
    scq config has-secret email_app_password
    scq config set-secret email_app_password   # prompt for value
    scq config delete-secret email_app_password
"""

from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .config import secrets as secrets_mod
from .config import user as user_cfg
from .config.paths import paths as get_paths
from .db import migrations as db_migrations


def main(argv: list[str] | None = None) -> int:
    # Passthrough subcommands forward their unparsed argv to a sibling
    # module's main(). Argparse's REMAINDER is unreliable with option-style
    # args (e.g. `scq init-db --stats` is parsed as a parent flag), so we
    # short-circuit before argparse touches them.
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] in _PASSTHROUGH_COMMANDS:
        # B5/B9 fix: intercept --help BEFORE the passthrough fires. Most
        # underlying modules don't use argparse, so they'd treat `--help`
        # as a positional arg (process: ARXIV-ID; mendeley: .bib path) or
        # ignore it entirely (watch: enters the daemon loop). Short-circuit
        # to a docstring summary instead.
        if len(raw) >= 2 and raw[1] in ("--help", "-h"):
            return _passthrough_help(raw[0])
        return _PASSTHROUGH_COMMANDS[raw[0]](raw[1:])

    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


# Subcommands that swallow the rest of argv verbatim and forward it to the
# corresponding module's main(). Built lazily so we don't import heavy
# dependencies until they're needed.
def _passthrough_process(rest: list[str]) -> int:
    from .ingest import process as _process_mod

    saved = sys.argv
    try:
        sys.argv = ["scq process"] + rest
        _process_mod.main()
    except SystemExit as e:
        return int(e.code or 0)
    finally:
        sys.argv = saved
    return 0


def _passthrough_merge(rest: list[str]) -> int:
    from .db import merge as _merge_mod

    return _merge_mod.main(rest)


def _passthrough_init_db(rest: list[str]) -> int:
    from .db import init as _initdb_mod

    return _initdb_mod.main(rest)


def _passthrough_digest(rest: list[str]) -> int:
    from .arxiv import digest as _digest_mod

    _digest_mod.main(rest)
    return 0


def _passthrough_module(module_path: str, *, supports_argv: bool = True):
    """Build a passthrough handler for a module whose `main()` takes argv.

    `supports_argv=False` is for modules whose main() reads sys.argv directly
    (legacy ones we haven't updated). Those need argv injected via sys.argv.
    """

    def handler(rest: list[str]) -> int:
        import importlib

        mod = importlib.import_module(module_path)
        if supports_argv:
            try:
                rc = mod.main(rest)
                return int(rc) if rc is not None else 0
            except SystemExit as e:
                return int(e.code or 0)
        # Legacy main()-with-no-args path: splice argv
        saved = sys.argv
        try:
            sys.argv = [module_path] + rest
            mod.main()
        except SystemExit as e:
            return int(e.code or 0)
        finally:
            sys.argv = saved
        return 0

    return handler


_PASSTHROUGH_COMMANDS = {
    "process": _passthrough_process,
    "merge": _passthrough_merge,
    "init-db": _passthrough_init_db,
    "digest": _passthrough_digest,
    # Wave 2 (#12): the remaining ingest/overleaf/search tools
    "mendeley": _passthrough_module("scq.ingest.mendeley", supports_argv=False),
    "inbox": _passthrough_module("scq.ingest.inbox", supports_argv=False),
    "watch": _passthrough_module("scq.ingest.watch", supports_argv=False),
    "overleaf": _passthrough_module("scq.overleaf.sync", supports_argv=False),
    "build-index": _passthrough_module("scq.search.index", supports_argv=True),
    # #12 final move (2026-05-03): serve.py → scq/server.py.
    "serve": _passthrough_module("scq.server", supports_argv=True),
    # #13 (2026-05-03): rewrite the digest workflow's cron line.
    "schedule": _passthrough_module("scq.schedule", supports_argv=True),
    # #23 (2026-05-03): convert legacy scraper_config.js → user_config/*.json.
    "migrate-from-legacy": _passthrough_module("scq.migrate", supports_argv=True),
    # monitor: check last digest workflow run health.
    "monitor": _passthrough_module("scq.monitor", supports_argv=True),
    # relevance: config-driven scoring management.
    "relevance": _passthrough_module("scq.relevance", supports_argv=True),
    # patents: fetch/process/show patents (PatentsView).
    "patents": _passthrough_module("scq.patents.cli", supports_argv=True),
}


# Maps each passthrough subcommand to its underlying module path. Used by
# _passthrough_help to fetch the right docstring without invoking main().
# Keep in sync with _PASSTHROUGH_COMMANDS above.
_PASSTHROUGH_MODULES = {
    "process": "scq.ingest.process",
    "merge": "scq.db.merge",
    "init-db": "scq.db.init",
    "digest": "scq.arxiv.digest",
    "mendeley": "scq.ingest.mendeley",
    "inbox": "scq.ingest.inbox",
    "watch": "scq.ingest.watch",
    "overleaf": "scq.overleaf.sync",
    "build-index": "scq.search.index",
    "serve": "scq.server",
    "schedule": "scq.schedule",
    "migrate-from-legacy": "scq.migrate",
    "monitor": "scq.monitor",
    "relevance": "scq.relevance",
    "patents": "scq.patents.cli",
}


def _passthrough_help(name: str) -> int:
    """Print a passthrough subcommand's documentation without invoking it.

    Most underlying modules don't use argparse, so passing ``--help`` to
    them either produces wrong behaviour (process treats it as ARXIV-ID,
    mendeley as .bib path) or hangs (watch enters the daemon loop).
    Print the module's docstring instead — that's where the usage notes
    actually live.
    """
    import importlib

    mod_path = _PASSTHROUGH_MODULES.get(name)
    if not mod_path:
        print(f"scq {name}: no help available", file=sys.stderr)
        return 1
    try:
        mod = importlib.import_module(mod_path)
    except ImportError as e:
        print(f"scq {name}: failed to load {mod_path} ({e})", file=sys.stderr)
        return 1
    doc = (mod.__doc__ or "").strip()
    print(f"scq {name} -- {mod_path}\n")
    if doc:
        print(doc)
    else:
        print(f"(no docstring on {mod_path})")
    print(
        f"\nThis subcommand forwards its arguments to {mod_path}.main(). "
        "Some modules accept --help themselves; others don't. The summary "
        "above is the canonical usage."
    )
    return 0


# ─── parser construction ───


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scq",
        description="Scientific Literature Scoop CLI",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_doctor = sub.add_parser(
        "doctor",
        help="check digest pipeline health: secrets, config, paths, SMTP, GitHub secrets",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_init = sub.add_parser(
        "init",
        help="create + migrate the paper database at paths.db_path",
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing DB that already contains paper data",
    )
    p_init.add_argument(
        "--db-path",
        help="override paths.db_path for this invocation (e.g. for testing)",
    )
    p_init.set_defaults(func=_cmd_init)

    # Passthrough subcommands (see _PASSTHROUGH_COMMANDS in main()). These
    # appear in `scq --help` for discoverability, but main() short-circuits
    # before argparse touches them — argparse.REMAINDER is unreliable with
    # option-style args like `--stats`. Run `scq <cmd> --help` to see the
    # underlying module's options.
    sub.add_parser(
        "process",
        help="ingest a paper end-to-end: extract figures, generate citations, insert into DB (arxiv_id [--note ...])",
        add_help=False,  # underlying module owns help
    )
    sub.add_parser(
        "merge",
        help="merge SCQ paper databases or export a collection (scq.db.merge subcommands)",
        add_help=False,
    )
    sub.add_parser(
        "init-db",
        help="legacy schema initializer / migrator / stats viewer (--migrate / --stats / --db)",
        add_help=False,
    )
    sub.add_parser(
        "digest",
        help="generate + email the daily arXiv digest (--days N / --no-email / --test / --smart-weekend)",
        add_help=False,
    )
    # Wave 2 of #12 — the remaining ingest/overleaf/search tools
    sub.add_parser(
        "mendeley", help="import a Mendeley/Zotero .bib file into the SCQ database", add_help=False
    )
    sub.add_parser(
        "inbox", help="batch-process PDFs dropped into the inbox/ folder", add_help=False
    )
    sub.add_parser(
        "watch", help="watch the inbox folder for new .bib/.ris/.json files", add_help=False
    )
    sub.add_parser(
        "overleaf",
        help="sync references.bib to your Overleaf project (--setup / --status)",
        add_help=False,
    )
    sub.add_parser(
        "build-index", help="(legacy) build a full-text JSON search index from PDFs", add_help=False
    )
    sub.add_parser(
        "monitor",
        help="check last digest workflow run and report health (--notify / --fix)",
        add_help=False,
    )
    sub.add_parser(
        "relevance",
        help="inspect/improve relevance scoring (show / learn / test <id|title>)",
        add_help=False,
    )
    sub.add_parser(
        "patents",
        help="fetch/process/show patents from PatentsView (fetch / process / show <number>)",
        add_help=False,
    )

    config = sub.add_parser("config", help="inspect and manage configuration")
    config_sub = config.add_subparsers(dest="config_command", metavar="<config-command>")

    # config show
    p_show = config_sub.add_parser("show", help="print resolved config")
    p_show.add_argument("domain", nargs="?", help="one of MANIFEST; default = all")
    p_show.add_argument("--no-pretty", action="store_true", help="emit compact JSON")
    p_show.set_defaults(func=_cmd_show)

    # config get
    p_get = config_sub.add_parser("get", help="get one nested key")
    p_get.add_argument("domain")
    p_get.add_argument("key", help="dot-separated path, e.g. autoFetch.cooldownHours")
    p_get.set_defaults(func=_cmd_get)

    # config validate
    p_validate = config_sub.add_parser("validate", help="validate config, exit 1 on errors")
    p_validate.add_argument("domain", nargs="?", help="default = all")
    p_validate.set_defaults(func=_cmd_validate)

    # config paths
    p_paths = config_sub.add_parser("paths", help="show resolved filesystem paths")
    p_paths.set_defaults(func=_cmd_paths)

    # secrets
    p_has = config_sub.add_parser(
        "has-secret",
        help="exit 0 if a secret resolves, 1 otherwise (does not print the value)",
    )
    p_has.add_argument("name")
    p_has.set_defaults(func=_cmd_has_secret)

    p_set = config_sub.add_parser(
        "set-secret",
        help="prompt for and store a secret in the OS keyring",
    )
    p_set.add_argument("name")
    p_set.set_defaults(func=_cmd_set_secret)

    p_del = config_sub.add_parser("delete-secret", help="remove a secret from the OS keyring")
    p_del.add_argument("name")
    p_del.set_defaults(func=_cmd_delete_secret)

    # #22: portable bundle of data/user_config/* (no secrets, no DB).
    p_exp = config_sub.add_parser("export", help="bundle user_config/* into a zip for transfer")
    p_exp.add_argument("path", help="destination .zip path")
    p_exp.add_argument(
        "--include-paths",
        action="store_true",
        help="also bundle paths.toml (off by default; paths are machine-specific)",
    )
    p_exp.set_defaults(func=_cmd_config_export)

    p_imp = config_sub.add_parser("import", help="extract a config bundle into user_config/")
    p_imp.add_argument("path", help="source .zip path")
    p_imp.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing user_config files (default: skip)",
    )
    p_imp.set_defaults(func=_cmd_config_import)

    return parser


# ─── command handlers ───


def _cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    return run_doctor()


def _cmd_init(args: argparse.Namespace) -> int:
    if args.db_path:
        db_path = Path(args.db_path).expanduser().resolve()
    else:
        db_path = get_paths(force_reload=True).db_path

    db_path.parent.mkdir(parents=True, exist_ok=True)

    existed = db_path.exists()
    if existed and not args.force:
        # Idempotent on a clean / migration-only DB; refuse only if real data is present.
        # NB: ``with sqlite3.connect()`` only commits — does NOT close. Must close
        # explicitly or Windows holds the file lock and breaks subsequent unlink().
        probe = sqlite3.connect(db_path)
        try:
            row = probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='papers'"
            ).fetchone()
            count = probe.execute("SELECT COUNT(*) FROM papers").fetchone()[0] if row else 0
        except sqlite3.DatabaseError as e:
            probe.close()
            print(f"error: {db_path} is not a valid SQLite database ({e})", file=sys.stderr)
            return 1
        finally:
            probe.close()
        if count > 0:
            print(
                f"error: {db_path} already contains {count} paper(s). "
                "Use --force to overwrite, or move it aside first.",
                file=sys.stderr,
            )
            return 1

    if existed and args.force:
        db_path.unlink()
        print(f"removed existing database at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        applied = db_migrations.apply_pending(conn)
    finally:
        conn.close()

    if applied:
        verb = "Created" if not existed or args.force else "Migrated"
        print(
            f"{verb} database at {db_path} (applied {len(applied)} migration(s); "
            f"now at version {applied[-1].version})."
        )
    else:
        print(f"Database at {db_path} is already up to date.")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if args.domain:
        result = user_cfg.load_config(args.domain)
        _emit_json(result.data, pretty=not args.no_pretty)
        if result.errors:
            print(f"\nNote: {len(result.errors)} validation error(s):", file=sys.stderr)
            for e in result.errors:
                print(f"  {e}", file=sys.stderr)
        return 0
    every = user_cfg.load_all()
    payload = {d: r.data for d, r in every.items()}
    _emit_json(payload, pretty=not args.no_pretty)
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    result = user_cfg.load_config(args.domain)
    cur: Any = result.data
    for part in args.key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            print(f"key '{args.key}' not found in {args.domain}", file=sys.stderr)
            return 1
    _emit_json(cur, pretty=True)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    domains = [args.domain] if args.domain else list(user_cfg.MANIFEST)
    bad = 0
    for d in domains:
        result = user_cfg.load_config(d)
        if result.errors:
            bad += 1
            print(f"{d}: {len(result.errors)} error(s)")
            for e in result.errors:
                print(f"  {e}")
        else:
            print(f"{d}: ok")
    return 1 if bad else 0


def _cmd_paths(args: argparse.Namespace) -> int:
    p = get_paths()
    out = {
        "repo_root": str(p.repo_root),
        "db_path": str(p.db_path),
        "papers_dir": str(p.papers_dir),
        "figures_dir": str(p.figures_dir),
        "inbox_dir": str(p.inbox_dir),
        "exports_dir": str(p.exports_dir),
        "digests_dir": str(p.digests_dir),
        "references_bib_path": str(p.references_bib_path),
        "references_txt_path": str(p.references_txt_path),
    }
    _emit_json(out, pretty=True)
    return 0


def _cmd_has_secret(args: argparse.Namespace) -> int:
    return 0 if secrets_mod.has(args.name) else 1


def _cmd_set_secret(args: argparse.Namespace) -> int:
    if not secrets_mod.keyring_available():
        print(
            "keyring is not installed. Install with:\n    pip install scq[keyring]",
            file=sys.stderr,
        )
        return 2
    value = getpass.getpass(f"Enter value for {args.name} (input hidden): ")
    if not value:
        print("aborted: empty value", file=sys.stderr)
        return 1
    try:
        secrets_mod.set(args.name, value)
    except Exception as e:  # noqa: BLE001
        print(f"failed to set secret: {e}", file=sys.stderr)
        return 1
    print(f"secret '{args.name}' stored in OS keyring")
    return 0


def _cmd_delete_secret(args: argparse.Namespace) -> int:
    removed = secrets_mod.delete(args.name)
    if removed:
        print(f"removed '{args.name}' from OS keyring")
        return 0
    print(f"no secret '{args.name}' found in keyring (env-var-only secrets cannot be deleted here)")
    return 1


def _cmd_config_export(args: argparse.Namespace) -> int:
    from .config.portable import export_config

    target = Path(args.path).expanduser().resolve()
    try:
        manifest = export_config(target, include_paths=args.include_paths)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    n = len(manifest["contents"])
    print(
        f"wrote {target} ({n} file{'s' if n != 1 else ''}, paths={'yes' if args.include_paths else 'no'})"
    )
    return 0


def _cmd_config_import(args: argparse.Namespace) -> int:
    from .config.portable import import_config

    source = Path(args.path).expanduser().resolve()
    try:
        result = import_config(source, overwrite=args.overwrite)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if result["written"]:
        print(f"installed {len(result['written'])} file(s): {', '.join(result['written'])}")
    if result["skipped"]:
        print(
            f"skipped {len(result['skipped'])} existing file(s) (use --overwrite to replace): "
            f"{', '.join(result['skipped'])}"
        )
    return 0


# ─── helpers ───


def _emit_json(payload: Any, *, pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
