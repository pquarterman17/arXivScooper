"""Persist :class:`Patent` records to the ``patents`` table.

Thin DB layer between the providers and SQLite. Insertion is an upsert on
the canonical patent number so re-fetching a patent refreshes its
bibliographic data without clobbering the human-written summary fields
(those are updated only by :func:`store_summary`).
"""

from __future__ import annotations

import json
import sqlite3

from .normalize import Patent, today_iso

# Columns owned by an upsert from a provider. Deliberately excludes the
# three summary fields — those belong to store_summary() so a re-fetch
# never wipes a hand-written summary.
_PROVIDER_COLUMNS = (
    "number",
    "country",
    "doc_number",
    "kind_code",
    "is_application",
    "title",
    "abstract",
    "assignee",
    "inventors",
    "short_inventors",
    "filing_date",
    "grant_date",
    "pub_date",
    "claims",
    "independent_claims",
    "cpc_codes",
    "cites",
    "cited_by",
    "url",
    "source",
    "date_added",
)


def _patent_row(p: Patent) -> dict:
    return {
        "number": p.number,
        "country": p.country,
        "doc_number": p.doc_number,
        "kind_code": p.kind_code,
        "is_application": 1 if p.is_application else 0,
        "title": p.title,
        "abstract": p.abstract,
        "assignee": p.assignee,
        "inventors": ", ".join(p.inventors),
        "short_inventors": p.short_inventors,
        "filing_date": p.filing_date,
        "grant_date": p.grant_date,
        "pub_date": p.pub_date,
        "claims": json.dumps(p.claims, ensure_ascii=False),
        "independent_claims": json.dumps(p.independent_claims, ensure_ascii=False),
        "cpc_codes": json.dumps(p.cpc_codes, ensure_ascii=False),
        "cites": json.dumps(p.cites, ensure_ascii=False),
        "cited_by": json.dumps(p.cited_by, ensure_ascii=False),
        "url": p.url,
        "source": p.source,
        "date_added": today_iso(),
    }


def upsert_patent(conn: sqlite3.Connection, patent: Patent) -> str:
    """Insert or refresh a patent's provider-owned fields. Returns its number.

    On conflict (same number), every provider column is refreshed but the
    summary fields and ``date_added`` are preserved.
    """
    row = _patent_row(patent)
    cols = list(_PROVIDER_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in cols)
    # date_added is set on insert only; keep the original on update.
    update_cols = [c for c in cols if c not in ("number", "date_added")]
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
    conn.execute(
        f"""
        INSERT INTO patents ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(number) DO UPDATE SET
            {update_clause},
            updated_at = datetime('now')
        """,
        row,
    )
    conn.commit()
    return patent.number


def store_summary(
    conn: sqlite3.Connection,
    number: str,
    *,
    plain_summary: str | None = None,
    protected_scope: str | None = None,
    prior_art_note: str | None = None,
) -> bool:
    """Write the three plain-English summary fields for a patent.

    Only non-None arguments are written, so the summarize-patent skill can
    fill fields incrementally. Returns True if a row was updated.
    """
    sets = []
    params: list = []
    for col, val in (
        ("plain_summary", plain_summary),
        ("protected_scope", protected_scope),
        ("prior_art_note", prior_art_note),
    ):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        return False
    sets.append("updated_at = datetime('now')")
    params.append(number)
    cur = conn.execute(f"UPDATE patents SET {', '.join(sets)} WHERE number = ?", params)
    conn.commit()
    return cur.rowcount > 0


def list_patents(
    conn: sqlite3.Connection, *, query: str | None = None, limit: int = 200
) -> list[dict]:
    """List stored patents, newest first, optionally filtered by FTS query.

    A non-empty ``query`` runs against ``patents_fts`` (title/abstract/claims/
    summary); otherwise all patents are returned. The heavy text columns
    (claims, abstract) are omitted from the list payload — callers fetch the
    full record via :func:`get_patent` when a patent is opened.
    """
    limit = max(1, min(int(limit), 1000))
    conn.row_factory = sqlite3.Row
    cols = (
        "number, title, assignee, short_inventors, filing_date, grant_date, "
        "cpc_codes, source, plain_summary, date_added"
    )
    if query and query.strip():
        sql = (
            f"SELECT {cols} FROM patents WHERE number IN "
            "(SELECT number FROM patents_fts WHERE patents_fts MATCH ?) "
            "ORDER BY date_added DESC, grant_date DESC LIMIT ?"
        )
        params: tuple = (query.strip(), limit)
    else:
        sql = f"SELECT {cols} FROM patents ORDER BY date_added DESC, grant_date DESC LIMIT ?"
        params = (limit,)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        rec = dict(row)
        if isinstance(rec.get("cpc_codes"), str):
            try:
                rec["cpc_codes"] = json.loads(rec["cpc_codes"])
            except (json.JSONDecodeError, TypeError):
                rec["cpc_codes"] = []
        rec["has_summary"] = bool(rec.get("plain_summary"))
        out.append(rec)
    return out


def existing_numbers(conn: sqlite3.Connection) -> set[str]:
    """Return the set of patent numbers already stored (for dedup)."""
    return {row[0] for row in conn.execute("SELECT number FROM patents").fetchall()}


def get_patent(conn: sqlite3.Connection, number: str) -> dict | None:
    """Fetch one patent as a dict (JSON columns decoded), or None."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM patents WHERE number = ?", (number,)).fetchone()
    if row is None:
        return None
    rec = dict(row)
    for col in ("claims", "independent_claims", "cpc_codes", "cites", "cited_by", "tags"):
        if col in rec and isinstance(rec[col], str):
            try:
                rec[col] = json.loads(rec[col])
            except (json.JSONDecodeError, TypeError):
                rec[col] = []
    return rec
