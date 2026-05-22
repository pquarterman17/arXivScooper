"""Digest workflow health monitor — ``scq monitor``.

Checks the most recent GitHub Actions run of ``arxiv_digest.yml`` and
reports its status.  Designed to be called from a cron job or a Claude Code
routine that needs to alert on digest failures.

Usage::

    scq monitor                  # check status, print one line, exit 0/1/2
    scq monitor --notify         # also emit a structured NOTIFY block on failure
    scq monitor --fix            # on failure, attempt diagnosis + suggest remedy

Exit codes:

    0 — today's run succeeded
    1 — last run failed (or gh CLI error)
    2 — stale: no run today (schedule may be broken) or run in progress

"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

# GitHub repo that hosts the workflow.
_REPO = "pquarterman17/arXivScooper"
_WORKFLOW = "arxiv_digest.yml"


# ─── core logic ──────────────────────────────────────────────────────────────


def _run_gh(*args: str) -> tuple[int, str, str]:
    """Run a ``gh`` sub-command, return (returncode, stdout, stderr)."""
    cmd = ["gh", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", "gh CLI not found"


def _ensure_gh() -> None:
    """Raise SystemExit(1) with a clear message when ``gh`` is unavailable."""
    if shutil.which("gh") is None:
        print(
            "error: the GitHub CLI ('gh') is not installed or not on PATH.\n"
            "Install from https://cli.github.com/ then run `gh auth login`.",
            file=sys.stderr,
        )
        sys.exit(1)


def _ensure_gh_auth() -> None:
    """Raise SystemExit(1) when ``gh`` is installed but not authenticated."""
    rc, _out, err = _run_gh("auth", "status")
    if rc != 0:
        print(
            f"error: gh CLI is not authenticated ({err.splitlines()[0] if err else 'unknown'}).\n"
            "Run `gh auth login` to authenticate.",
            file=sys.stderr,
        )
        sys.exit(1)


def fetch_last_run() -> dict[str, Any]:
    """Return the most-recent workflow run record as a dict.

    Keys: ``status``, ``conclusion``, ``createdAt``, ``url``, ``databaseId``.
    Raises ``RuntimeError`` on any gh invocation failure.
    """
    rc, out, err = _run_gh(
        "run",
        "list",
        "--repo",
        _REPO,
        "--workflow",
        _WORKFLOW,
        "--limit",
        "1",
        "--json",
        "status,conclusion,createdAt,url,databaseId",
    )
    if rc != 0:
        raise RuntimeError(f"gh run list failed (exit {rc}): {err}")
    try:
        runs = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse gh output: {exc}\nraw: {out!r}") from exc
    if not runs:
        raise RuntimeError(
            f"no workflow runs found for '{_WORKFLOW}' in {_REPO}.\n"
            "Has the workflow ever run?  Check Actions on GitHub."
        )
    return runs[0]


def _parse_created_at(raw: str) -> datetime:
    """Parse the ISO-8601 timestamp GitHub returns, normalised to UTC."""
    # GitHub returns e.g. "2026-05-04T10:52:03Z"
    raw = raw.replace("Z", "+00:00")
    return datetime.fromisoformat(raw).astimezone(timezone.utc)


def _today_utc() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


class MonitorResult:
    """The interpreted result of a ``check_last_run()`` call."""

    # status_code mirrors the exit code returned by check_last_run():
    #   0 = success, 1 = failure, 2 = stale / in_progress
    def __init__(
        self,
        *,
        status_code: int,
        symbol: str,
        message: str,
        run: dict[str, Any],
        run_date: datetime,
    ) -> None:
        self.status_code = status_code
        self.symbol = symbol
        self.message = message
        self.run = run
        self.run_date = run_date

    def one_line(self) -> str:
        return f"{self.symbol} {self.message}"

    def notify_block(self) -> str:
        lines = [
            "NOTIFY",
            f"  STATUS:  {self.symbol} {self.message}",
            f"  RUN_ID:  {self.run.get('databaseId', 'n/a')}",
            f"  URL:     {self.run.get('url', 'n/a')}",
            f"  DATE:    {self.run_date.strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)


def check_last_run() -> MonitorResult:
    """Fetch the most-recent digest run and return a :class:`MonitorResult`.

    This function does NOT print anything — the caller decides what to emit.
    Exits with code 1 on ``gh`` infrastructure errors (not digest failures).
    """
    _ensure_gh()
    _ensure_gh_auth()

    try:
        run = fetch_last_run()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    status = run.get("status", "")
    conclusion = run.get("conclusion", "")
    run_id = run.get("databaseId", "?")
    url = run.get("url", "")
    created_raw = run.get("createdAt", "")

    try:
        run_date = _parse_created_at(created_raw)
    except (ValueError, TypeError):
        run_date = datetime.now(timezone.utc)

    run_date_str = run_date.strftime("%Y-%m-%d")
    today = _today_utc()
    is_today = run_date >= today

    if status == "in_progress":
        return MonitorResult(
            status_code=2,
            symbol="~",
            message=f"Digest in progress — {run_date_str} (run #{run_id})",
            run=run,
            run_date=run_date,
        )

    if conclusion == "success":
        if is_today:
            return MonitorResult(
                status_code=0,
                symbol="OK",
                message=f"Digest OK — {run_date_str} (run #{run_id})",
                run=run,
                run_date=run_date,
            )
        else:
            return MonitorResult(
                status_code=2,
                symbol="WARN",
                message=f"Digest stale — last success was {run_date_str} (expected daily)",
                run=run,
                run_date=run_date,
            )

    # failure (or cancelled / skipped / timed_out)
    return MonitorResult(
        status_code=1,
        symbol="FAIL",
        message=f"Digest FAILED — {run_date_str} ({url})",
        run=run,
        run_date=run_date,
    )


# ─── --fix logic ─────────────────────────────────────────────────────────────


def _run_fix(result: MonitorResult) -> None:
    """Attempt diagnosis and print a suggested remedy."""
    print("\n-- diagnosis --")
    try:
        from .doctor import run_doctor  # type: ignore[import]

        issues = run_doctor()
    except ImportError:
        issues = None

    if issues is None:
        # doctor module doesn't exist yet; fall back to heuristics
        _fix_heuristic(result)
        return

    if not issues:
        print(
            "scq doctor: all checks passed.\n"
            "Suggestion: re-trigger the workflow manually:\n"
            "  gh workflow run arxiv_digest.yml --repo " + _REPO
        )
        return

    for issue in issues:
        kind = issue.get("kind", "unknown")
        if kind == "secrets_missing":
            missing = issue.get("missing", [])
            print(f"Missing GitHub secrets: {', '.join(missing)}")
            print("Fix: add them at https://github.com/" + _REPO + "/settings/secrets/actions")
        elif kind == "smtp_unreachable":
            print("SMTP server is unreachable — this may be transient.")
            print(
                "Suggestion: wait a few minutes then retry:\n"
                "  gh workflow run arxiv_digest.yml --repo " + _REPO
            )
        else:
            print(f"issue: {issue}")


def _fix_heuristic(result: MonitorResult) -> None:
    """Fallback fix suggestions when scq.doctor is unavailable."""
    run_url = result.run.get("url", "")
    print(
        "scq doctor is not installed; performing basic checks.\n"
        "\nCommon causes of digest failure:\n"
        "  1. Missing or expired GitHub secrets (SCQ_EMAIL_FROM, "
        "SCQ_EMAIL_APP_PASSWORD, SCQ_EMAIL_TO)\n"
        "     Fix: https://github.com/" + _REPO + "/settings/secrets/actions\n"
        "  2. Transient SMTP or arXiv API error\n"
        "     Fix: re-run the workflow:\n"
        "       gh workflow run arxiv_digest.yml --repo " + _REPO + "\n"
        "  3. Bug introduced in recent commit\n"
        "     Fix: check the run log:\n"
        f"       {run_url or 'https://github.com/' + _REPO + '/actions'}"
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scq monitor",
        description=(
            "Check the last GitHub Actions run of the arXiv digest workflow and report its health."
        ),
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="on failure or stale status, emit a structured NOTIFY block to stdout",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="on failure, attempt diagnosis via scq doctor and suggest a remedy",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``scq monitor``.

    Returns the exit code (0 = OK, 1 = failure, 2 = stale/in-progress).
    """
    args = _build_parser().parse_args(argv)

    result = check_last_run()
    print(result.one_line())

    if args.notify and result.status_code != 0:
        print()
        print(result.notify_block())

    if args.fix and result.status_code == 1:
        _run_fix(result)

    return result.status_code
