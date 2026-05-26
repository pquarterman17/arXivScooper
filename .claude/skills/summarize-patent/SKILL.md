---
name: summarize-patent
description: "Read a patent's claims and fill in its plain-English summary fields in the SCQ database. Use this skill whenever the user says 'summarize this patent', 'what does this patent claim', 'explain the patent', 'translate the legalese', 'what's actually protected', or asks you to review/analyze a patent that's already in the database (added via 'scq patents process'). Translates claim legalese into: what it does, what's actually protected, and what it builds on."
---

# Summarize Patent

After a patent is added via `scq patents fetch` + `scq patents process`,
it has bibliographic data and raw claim text but no plain-English
summary. This skill reads the claims — especially the *independent*
claims, which define the legal scope — and fills in three fields:

- **plain_summary** — 2-3 sentences: what the invention actually does, no legalese.
- **protected_scope** — a plain reading of the independent claims: the real legal boundary, what someone would have to do to infringe.
- **prior_art_note** — what it builds on / distinguishes from, per the patent's own statements (or "Not stated in the patent").

These three fields were the deliberate Phase 1 scope (see
`plans/patent-scraping.md`). Do **not** editorialize about SCQ research
relevance — keep the summary factual and legal.

## Path setup

Find the project root in the sandbox via the canonical database:
```bash
PROJECT_ROOT=$(find /sessions -name "arxiv_scooper.db" -path "*/mnt/*/data/*" 2>/dev/null | head -1 | xargs dirname | xargs dirname)
```
Key paths relative to `$PROJECT_ROOT`:
- Database: `data/arxiv_scooper.db`
- Patents table: `patents` (see `data/migrations/002_patents.sql`)

## Finding the patent

The user may refer to a patent by number, assignee, or title.

```python
import sqlite3, json, glob, os

matches = glob.glob("/sessions/*/mnt/*/data/arxiv_scooper.db")
DB = matches[0] if matches else "data/arxiv_scooper.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT number, title, assignee FROM patents "
    "WHERE number LIKE ? OR title LIKE ? OR assignee LIKE ?",
    (f"%{term}%", f"%{term}%", f"%{term}%"),
).fetchall()
```

## Building the prompt and reading the claims

Reuse the shared prompt builder so the skill and the future automated
pipeline ask for exactly the same thing:

```python
from scq.patents.store import get_patent, store_summary
from scq.patents.summarize import build_summary_prompt, parse_summary_response

rec = get_patent(conn, number)            # JSON columns already decoded
prompt = build_summary_prompt(rec)        # surfaces independent claims in full
```

Read `prompt` yourself (you are the LLM in the loop). Focus on the
**independent claims** — dependent claims only narrow them. The abstract
is marketing; the claims are the law.

## Writing the three fields

Compose the summary, then store it. `store_summary` only writes the
fields you pass, so you can fill them incrementally:

```python
store_summary(
    conn, number,
    plain_summary="A method for fabricating a transmon qubit with a "
                  "tantalum capacitor pad to reduce two-level-system loss...",
    protected_scope="Independent claim 1 covers any superconducting qubit "
                    "whose capacitor electrode is alpha-phase tantalum on a "
                    "sapphire substrate with surface roughness below X. To "
                    "infringe you would need all of: (a)..., (b)..., (c)...",
    prior_art_note="Builds on niobium-based transmons; distinguishes itself "
                   "by the tantalum surface-oxide chemistry. (As stated in "
                   "the Background section.)",
)
conn.close()
```

## Guidance

- **Independent claims first.** If a patent has 3 independent claims, the
  protected_scope must address all three — they're separate legal walls.
- **Translate, don't quote.** "A method comprising the steps of..." → "You
  do X, then Y." Name the actual technique.
- **Be honest about prior art.** Only state what the patent itself says.
  If the Background is silent, write "Not stated in the patent." Never
  invent prior art.
- **Verify the field landed:** `scq patents show <number>` should now print
  your `plain_summary` instead of "(not yet summarized)".
