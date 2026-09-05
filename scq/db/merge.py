#!/usr/bin/env python3
"""
Merge two SCQ paper databases.

Combines papers, notes, figures, highlights, collections, and links from
a source database into a target database with intelligent conflict resolution.

Merge rules:
  - Papers: if same ID exists, keep the one with the longer summary (more complete).
            Merge tags (union). Keep the newer note. Keep the higher priority.
  - Figures: union (add any figures not already in target)
  - Highlights: union (add any highlights not already in target, deduped by text)
  - Collections: union (merge membership)
  - Links: union
  - Read status: OR (if either db marks it read, it's read). Max priority.
  - PDF text: union (add pages not already indexed)

Usage:
  python tools/merge_database.py source.db target.db
  python tools/merge_database.py source.db target.db --dry-run
  python tools/merge_database.py source.db target.db --export-js
"""

import argparse
import json
import os
import sqlite3
import sys


def merge_databases(source_path, target_path, dry_run=False):
    """Merge source database into target database."""
    if not os.path.exists(source_path):
        print(f"Error: source database not found: {source_path}")
        sys.exit(1)
    if not os.path.exists(target_path):
        print(f"Error: target database not found: {target_path}")
        sys.exit(1)

    src = sqlite3.connect(source_path)
    src.row_factory = sqlite3.Row

    if dry_run:
        # Open target read-only for dry run
        tgt = sqlite3.connect(target_path)
    else:
        # Backup target before merging
        backup_path = target_path + ".pre-merge.bak"
        import shutil

        shutil.copy2(target_path, backup_path)
        print(f"Backed up target to {backup_path}")
        tgt = sqlite3.connect(target_path)

    tgt.row_factory = sqlite3.Row

    stats = {
        "papers_added": 0,
        "papers_updated": 0,
        "papers_skipped": 0,
        "figures_added": 0,
        "highlights_added": 0,
        "collections_added": 0,
        "links_added": 0,
        "notes_updated": 0,
        "read_status_updated": 0,
        "pdf_pages_added": 0,
    }

    # Get existing target paper IDs
    target_ids = set(r["id"] for r in tgt.execute("SELECT id FROM papers").fetchall())

    # ─── Merge papers ───
    source_papers = src.execute("SELECT * FROM papers").fetchall()
    for sp in source_papers:
        sid = sp["id"]

        if sid not in target_ids:
            # New paper — insert it
            if not dry_run:
                tgt.execute(
                    """
                    INSERT INTO papers (id, title, authors, short_authors, year, journal,
                      volume, pages, doi, arxiv_id, url, group_name, date_added,
                      tags, summary, key_results, cite_bib, cite_txt, pdf_path,
                      created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                    (
                        sp["id"],
                        sp["title"],
                        sp["authors"],
                        sp["short_authors"],
                        sp["year"],
                        sp["journal"],
                        sp["volume"],
                        sp["pages"],
                        sp["doi"],
                        sp["arxiv_id"],
                        sp["url"],
                        sp["group_name"],
                        sp["date_added"],
                        sp["tags"],
                        sp["summary"],
                        sp["key_results"],
                        sp["cite_bib"],
                        sp["cite_txt"],
                        sp["pdf_path"],
                        sp["created_at"],
                        sp["updated_at"],
                    ),
                )
            stats["papers_added"] += 1
            print(f"  + ADD paper: {sp['short_authors']} — {sp['title'][:50]}")
        else:
            # Existing paper — merge fields
            tp = tgt.execute("SELECT * FROM papers WHERE id = ?", (sid,)).fetchone()
            updates = {}

            # Keep longer summary
            if len(sp["summary"] or "") > len(tp["summary"] or ""):
                updates["summary"] = sp["summary"]

            # Keep longer key_results
            src_kr = json.loads(sp["key_results"] or "[]")
            tgt_kr = json.loads(tp["key_results"] or "[]")
            if len(src_kr) > len(tgt_kr):
                updates["key_results"] = sp["key_results"]

            # Merge tags (union)
            src_tags = set(json.loads(sp["tags"] or "[]"))
            tgt_tags = set(json.loads(tp["tags"] or "[]"))
            merged_tags = sorted(tgt_tags | src_tags)
            if merged_tags != sorted(tgt_tags):
                updates["tags"] = json.dumps(merged_tags)

            # Fill in blanks
            for field in [
                "cite_bib",
                "cite_txt",
                "doi",
                "arxiv_id",
                "journal",
                "volume",
                "pages",
                "pdf_path",
            ]:
                if not tp[field] and sp[field]:
                    updates[field] = sp[field]

            if updates:
                if not dry_run:
                    sets = ", ".join(f"{k} = ?" for k in updates)
                    vals = list(updates.values()) + [sid]
                    tgt.execute(f"UPDATE papers SET {sets} WHERE id = ?", vals)
                stats["papers_updated"] += 1
                print(f"  ~ UPDATE paper: {sp['short_authors']} ({', '.join(updates.keys())})")
            else:
                stats["papers_skipped"] += 1

    # ─── Merge figures ───
    source_figs = src.execute("SELECT * FROM figures").fetchall()
    existing_figs = set(
        (r["paper_id"], r["figure_key"])
        for r in tgt.execute("SELECT paper_id, figure_key FROM figures").fetchall()
    )
    for sf in source_figs:
        key = (sf["paper_id"], sf["figure_key"])
        if key not in existing_figs:
            if not dry_run:
                tgt.execute(
                    """
                    INSERT INTO figures (paper_id, figure_key, file_path, label, caption, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        sf["paper_id"],
                        sf["figure_key"],
                        sf["file_path"],
                        sf["label"],
                        sf["caption"],
                        sf["sort_order"],
                    ),
                )
            stats["figures_added"] += 1

    # ─── Merge notes ───
    source_notes = src.execute("SELECT * FROM notes").fetchall()
    for sn in source_notes:
        tn = tgt.execute("SELECT * FROM notes WHERE paper_id = ?", (sn["paper_id"],)).fetchone()
        if tn is None:
            if sn["content"]:
                if not dry_run:
                    tgt.execute(
                        "INSERT INTO notes (paper_id, content, last_edited) VALUES (?, ?, ?)",
                        (sn["paper_id"], sn["content"], sn["last_edited"]),
                    )
                stats["notes_updated"] += 1
        else:
            # Keep the more recent note, or the longer one if no timestamps
            src_ts = sn["last_edited"] or ""
            tgt_ts = tn["last_edited"] or ""
            use_source = False
            if src_ts and tgt_ts:
                use_source = src_ts > tgt_ts
            elif not tn["content"] and sn["content"]:
                use_source = True
            elif len(sn["content"] or "") > len(tn["content"] or ""):
                use_source = True

            if use_source and not dry_run:
                tgt.execute(
                    "UPDATE notes SET content = ?, last_edited = ? WHERE paper_id = ?",
                    (sn["content"], sn["last_edited"], sn["paper_id"]),
                )
                stats["notes_updated"] += 1

    # ─── Merge read status ───
    source_rs = src.execute("SELECT * FROM read_status").fetchall()
    for sr in source_rs:
        tr = tgt.execute(
            "SELECT * FROM read_status WHERE paper_id = ?", (sr["paper_id"],)
        ).fetchone()
        if tr is None:
            if not dry_run:
                tgt.execute(
                    "INSERT INTO read_status (paper_id, is_read, priority) VALUES (?, ?, ?)",
                    (sr["paper_id"], sr["is_read"], sr["priority"]),
                )
            stats["read_status_updated"] += 1
        else:
            new_read = max(sr["is_read"] or 0, tr["is_read"] or 0)
            new_priority = max(sr["priority"] or 0, tr["priority"] or 0)
            if new_read != (tr["is_read"] or 0) or new_priority != (tr["priority"] or 0):
                if not dry_run:
                    tgt.execute(
                        "UPDATE read_status SET is_read = ?, priority = ? WHERE paper_id = ?",
                        (new_read, new_priority, sr["paper_id"]),
                    )
                stats["read_status_updated"] += 1

    # ─── Merge highlights ───
    source_hl = src.execute("SELECT * FROM highlights").fetchall()
    existing_hl = set(
        (r["paper_id"], r["text"])
        for r in tgt.execute("SELECT paper_id, text FROM highlights").fetchall()
    )
    for sh in source_hl:
        key = (sh["paper_id"], sh["text"])
        if key not in existing_hl:
            if not dry_run:
                tgt.execute(
                    "INSERT INTO highlights (paper_id, text, page, color) VALUES (?, ?, ?, ?)",
                    (sh["paper_id"], sh["text"], sh["page"], sh["color"]),
                )
            stats["highlights_added"] += 1

    # ─── Merge collections ───
    source_colls = src.execute("SELECT * FROM collections").fetchall()
    existing_colls = set(
        (r["name"], r["paper_id"])
        for r in tgt.execute("SELECT name, paper_id FROM collections").fetchall()
    )
    for sc in source_colls:
        key = (sc["name"], sc["paper_id"])
        if key not in existing_colls:
            if not dry_run:
                tgt.execute(
                    "INSERT OR IGNORE INTO collections (name, paper_id) VALUES (?, ?)",
                    (sc["name"], sc["paper_id"]),
                )
            stats["collections_added"] += 1

    # ─── Merge paper links ───
    source_links = src.execute("SELECT * FROM paper_links").fetchall()
    existing_links = set(
        (r["paper_a"], r["paper_b"])
        for r in tgt.execute("SELECT paper_a, paper_b FROM paper_links").fetchall()
    )
    for sl in source_links:
        key = (sl["paper_a"], sl["paper_b"])
        if key not in existing_links:
            if not dry_run:
                tgt.execute(
                    "INSERT OR IGNORE INTO paper_links (paper_a, paper_b) VALUES (?, ?)",
                    (sl["paper_a"], sl["paper_b"]),
                )
            stats["links_added"] += 1

    # ─── Merge PDF text index ───
    try:
        source_pdf = src.execute("SELECT paper_id, page_num, content FROM pdf_text").fetchall()
        existing_pdf = set(
            (r["paper_id"], r["page_num"])
            for r in tgt.execute("SELECT paper_id, page_num FROM pdf_text").fetchall()
        )
        for sp in source_pdf:
            key = (sp["paper_id"], sp["page_num"])
            if key not in existing_pdf:
                if not dry_run:
                    tgt.execute(
                        "INSERT INTO pdf_text (paper_id, page_num, content) VALUES (?, ?, ?)",
                        (sp["paper_id"], sp["page_num"], sp["content"]),
                    )
                stats["pdf_pages_added"] += 1
    except Exception:
        pass  # pdf_text table might not exist in source

    # Rebuild FTS
    if not dry_run:
        try:
            tgt.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
        except Exception:
            # FTS table may not exist in an older target schema.
            pass

    if not dry_run:
        tgt.commit()

    src.close()
    tgt.close()

    # Print summary
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Merge complete:")
    print(
        f"  Papers: {stats['papers_added']} added, {stats['papers_updated']} updated, {stats['papers_skipped']} unchanged"
    )
    print(f"  Figures: {stats['figures_added']} added")
    print(f"  Notes: {stats['notes_updated']} updated")
    print(f"  Read status: {stats['read_status_updated']} updated")
    print(f"  Highlights: {stats['highlights_added']} added")
    print(f"  Collections: {stats['collections_added']} memberships added")
    print(f"  Links: {stats['links_added']} added")
    if stats["pdf_pages_added"]:
        print(f"  PDF text: {stats['pdf_pages_added']} pages added")

    return stats


def export_collection(db_path, collection_name, output_path):
    """Export a collection as a standalone .db file with only its papers."""
    if not os.path.exists(db_path):
        print(f"Error: database not found: {db_path}")
        sys.exit(1)

    src = sqlite3.connect(db_path)
    src.row_factory = sqlite3.Row

    # Get paper IDs in the collection
    paper_ids = [
        r["paper_id"]
        for r in src.execute(
            "SELECT paper_id FROM collections WHERE name = ?", (collection_name,)
        ).fetchall()
    ]
    if not paper_ids:
        print(f"Error: collection '{collection_name}' is empty or doesn't exist")
        avail = [r["name"] for r in src.execute("SELECT DISTINCT name FROM collections").fetchall()]
        if avail:
            print(f"Available collections: {', '.join(avail)}")
        src.close()
        sys.exit(1)

    # Create output database with schema
    if os.path.exists(output_path):
        os.remove(output_path)

    # Use the schema from init_database
    from init_database import SCHEMA

    out = sqlite3.connect(output_path)
    out.executescript(SCHEMA)

    placeholders = ",".join("?" for _ in paper_ids)

    # Copy papers
    papers = src.execute(f"SELECT * FROM papers WHERE id IN ({placeholders})", paper_ids).fetchall()
    for p in papers:
        out.execute(
            """
            INSERT INTO papers (id, title, authors, short_authors, year, journal,
              volume, pages, doi, arxiv_id, url, group_name, date_added,
              tags, summary, key_results, cite_bib, cite_txt, pdf_path,
              created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            tuple(p),
        )

    # Copy figures
    for p in src.execute(f"SELECT * FROM figures WHERE paper_id IN ({placeholders})", paper_ids):
        out.execute(
            "INSERT INTO figures (paper_id, figure_key, file_path, label, caption, sort_order) VALUES (?,?,?,?,?,?)",
            (
                p["paper_id"],
                p["figure_key"],
                p["file_path"],
                p["label"],
                p["caption"],
                p["sort_order"],
            ),
        )

    # Copy notes
    for p in src.execute(f"SELECT * FROM notes WHERE paper_id IN ({placeholders})", paper_ids):
        out.execute(
            "INSERT INTO notes (paper_id, content, last_edited) VALUES (?,?,?)",
            (p["paper_id"], p["content"], p["last_edited"]),
        )

    # Copy read status
    for p in src.execute(
        f"SELECT * FROM read_status WHERE paper_id IN ({placeholders})", paper_ids
    ):
        out.execute(
            "INSERT INTO read_status (paper_id, is_read, priority) VALUES (?,?,?)",
            (p["paper_id"], p["is_read"], p["priority"]),
        )

    # Copy highlights
    for p in src.execute(f"SELECT * FROM highlights WHERE paper_id IN ({placeholders})", paper_ids):
        out.execute(
            "INSERT INTO highlights (paper_id, text, page, color) VALUES (?,?,?,?)",
            (p["paper_id"], p["text"], p["page"], p["color"]),
        )

    # Copy collection membership (only for this collection)
    for pid in paper_ids:
        out.execute(
            "INSERT INTO collections (name, paper_id) VALUES (?, ?)", (collection_name, pid)
        )

    # Copy links (only between papers both in this collection)
    pid_set = set(paper_ids)
    for lnk in src.execute(
        f"SELECT * FROM paper_links WHERE paper_a IN ({placeholders}) AND paper_b IN ({placeholders})",
        paper_ids + paper_ids,
    ):
        if lnk["paper_a"] in pid_set and lnk["paper_b"] in pid_set:
            out.execute(
                "INSERT OR IGNORE INTO paper_links (paper_a, paper_b) VALUES (?,?)",
                (lnk["paper_a"], lnk["paper_b"]),
            )

    # Copy PDF text
    try:
        for p in src.execute(
            f"SELECT * FROM pdf_text WHERE paper_id IN ({placeholders})", paper_ids
        ):
            out.execute(
                "INSERT INTO pdf_text (paper_id, page_num, content) VALUES (?,?,?)",
                (p["paper_id"], p["page_num"], p["content"]),
            )
    except Exception:
        # pdf_text table may not exist in the source DB.
        pass

    # Rebuild FTS
    try:
        out.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    except Exception:
        # FTS table may not exist in the exported schema.
        pass

    out.commit()
    out.close()
    src.close()

    print(f"Exported collection '{collection_name}' ({len(paper_ids)} papers) → {output_path}")
    print(f"Size: {os.path.getsize(output_path):,} bytes")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Merge SCQ paper databases")
    sub = parser.add_subparsers(dest="command")

    merge_p = sub.add_parser("merge", help="Merge source.db into target.db")
    merge_p.add_argument("source", help="Source database to merge from")
    merge_p.add_argument("target", help="Target database to merge into")
    merge_p.add_argument("--dry-run", action="store_true", help="Preview without making changes")

    export_p = sub.add_parser("export-collection", help="Export a collection as standalone .db")
    export_p.add_argument("db", help="Source database")
    export_p.add_argument("collection", help="Collection name to export")
    export_p.add_argument("-o", "--output", help="Output .db path (default: {collection}.db)")

    args = parser.parse_args(argv)

    if args.command == "merge":
        merge_databases(args.source, args.target, dry_run=args.dry_run)
    elif args.command == "export-collection":
        output = args.output or (args.collection.replace(" ", "_") + ".db")
        export_collection(args.db, args.collection, output)
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
