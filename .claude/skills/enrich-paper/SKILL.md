---
name: enrich-paper
description: "Read a paper's PDF and fill in its summary, key results, and research group in the SCQ database. Use this skill whenever the user says 'summarize this paper', 'read the paper', 'enrich', 'fill in the details', 'what are the key results', 'who wrote this', or asks you to review/analyze a paper that's already in the database. Also trigger when the user asks 'what is this paper about' for a paper in the DB — reading and enriching go hand in hand."
---

# Enrich Paper

After a paper is added via the add-paper pipeline, it has a truncated abstract as its summary, no key results, and no research group. This skill reads the actual PDF and fills in those fields with real scientific insight.

## Path Setup

Find the project root in the sandbox by locating the canonical SQLite database:
```bash
PROJECT_ROOT=$(find /sessions -name "arxiv_scooper.db" -path "*/mnt/*/data/*" 2>/dev/null | head -1 | xargs dirname | xargs dirname)
```

The key file paths relative to `$PROJECT_ROOT`:
- Database: `data/arxiv_scooper.db`
- PDFs: `papers/<arxiv_id>_<Author>_<ShortTitle>.pdf`
- Figures: `figures/<arxiv_id>/`

## Finding the Paper

The user might refer to a paper by arXiv ID, author name, or partial title.

```python
import sqlite3, json, os, glob

# Find project root + DB dynamically
matches = glob.glob("/sessions/*/mnt/*/data/arxiv_scooper.db")
DB = matches[0] if matches else "data/arxiv_scooper.db"
PROJECT_ROOT = os.path.dirname(os.path.dirname(DB))

conn = sqlite3.connect(DB)
conn.execute("PRAGMA foreign_keys = ON")
conn.row_factory = sqlite3.Row

# Search by ID, title, or author
search_term = "tantalum"  # whatever the user referenced
rows = conn.execute(
    "SELECT id, title, authors, summary, key_results, group_name, pdf_path FROM papers WHERE id LIKE ? OR title LIKE ? OR authors LIKE ?",
    (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%")
).fetchall()
```

## Reading the PDF

The PDF path is in the `pdf_path` column. Full sandbox path:
```python
pdf_full_path = os.path.join(PROJECT_ROOT, row["pdf_path"])
```

Use the Read tool to read the PDF. Focus on:
- The abstract and introduction for context
- The results/discussion sections for key findings
- Author affiliations (usually first page) for the research group

## What to Write

### Summary (2-3 sentences)
Focus on *what was done* and *why it matters*. Write for a researcher in the same broad field (superconducting quantum computing) who might not be in the exact sub-area.

**Good:** "Demonstrates a tantalum-based transmon qubit with T1 times exceeding 500 μs, a 5x improvement over niobium devices on the same substrate. The improvement is attributed to reduced oxide losses at the metal-air interface."

**Bad:** "This paper studies qubit coherence times using tantalum." (too vague, no results)

### Key Results (3-5 items, JSON array of strings)
Specific, quantitative findings when possible. Skimmable — someone scanning the database should get the main contributions from these alone.

```json
[
  "T1 = 503 ± 23 μs (median), up from ~100 μs for Nb baseline",
  "Identified TaOx surface oxide as primary remaining loss channel via XPS",
  "Gate fidelity 99.97% for single-qubit gates (randomized benchmarking)",
  "Coherence stable over 6-month period with no degradation"
]
```

### Group Name
Format: "PI Last Name (Institution)" — e.g., "de Leon (Princeton)", "Oliver (MIT/Lincoln Lab)". Use the corresponding author's group. Leave empty if uncertain.

## Updating the Database

```python
summary = "Your 2-3 sentence summary here"
key_results = json.dumps(["Result 1", "Result 2", "Result 3"])
group_name = "PI (Institution)"
paper_id = "2401.12345"

conn.execute(
    "UPDATE papers SET summary=?, key_results=?, group_name=? WHERE id=?",
    (summary, key_results, group_name, paper_id)
)
conn.commit()
conn.close()
# No re-export step — the .db file is canonical and the browser reads it directly.
```

## Presenting Results

After enriching, show the user what you wrote so they can confirm or tweak:
- **Summary:** (your summary)
- **Key results:** (bulleted list)
- **Group:** (group name)

Apply their edits with another `UPDATE` + `commit()` — no re-export step.

## Batch Enrichment

If asked to "enrich all papers that don't have summaries":

```sql
SELECT id, title, pdf_path FROM papers
WHERE summary IS NULL OR summary = '' OR key_results IS NULL OR key_results = '[]'
```

Work through them one at a time, showing each for confirmation. The user prefers guided entry — don't batch-update without review.
