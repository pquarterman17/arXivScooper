---
name: literature-review
description: "Generate a structured literature review or field summary from papers in the SCQ database, optionally supplemented by external search. Use this skill when the user asks 'what do we know about X', 'summarize the state of the field', 'literature review on', 'what papers do we have on', 'compare these papers', 'identify gaps', 'what is missing from our collection', 'survey of', or wants to understand how a set of papers in the database relate to each other or to a broader research question."
---

# Literature Review

This skill synthesizes papers already in the database (and optionally external sources) into a structured overview of a research topic.

## Step 1: Gather Papers from the Database

```python
import sqlite3, json, os, glob

# Find project root + DB dynamically
matches = glob.glob("/sessions/*/mnt/*/data/arxiv_scooper.db")
DB = matches[0] if matches else "data/arxiv_scooper.db"
PROJECT_ROOT = os.path.dirname(os.path.dirname(DB))

conn = sqlite3.connect(DB)
conn.execute("PRAGMA foreign_keys = ON")
conn.row_factory = sqlite3.Row

# Search by FTS
topic = "coherence tantalum"
rows = conn.execute("""
    SELECT p.id, p.title, p.authors, p.short_authors, p.year, p.tags,
           p.summary, p.key_results, p.group_name, p.pdf_path
    FROM papers p
    JOIN papers_fts fts ON p.rowid = fts.rowid
    WHERE papers_fts MATCH ?
    ORDER BY p.year DESC
""", (topic,)).fetchall()

# Also check notes for relevant context
notes = conn.execute("""
    SELECT n.paper_id, n.content, p.title FROM notes n
    JOIN papers p ON n.paper_id = p.id
    WHERE n.content LIKE ?
""", (f"%{topic}%",)).fetchall()
```

Cast a wide net — include borderline papers and filter later.

## Step 2: Read Papers That Need It

For enriched papers (with summaries and key results), work from the DB fields. For un-enriched papers, read the PDFs:

```python
pdf_full_path = os.path.join(PROJECT_ROOT, row["pdf_path"])
```

If many papers lack enrichment, offer to enrich them as part of the review — this gives lasting value beyond just the review document.

## Step 3: Supplement with External Search (Optional)

If the user wants a comprehensive review, use available search tools:

- **Scholar Gateway MCP** (if connected): semantic search for related work
- **PubMed MCP** (if connected): biomedical/materials science crossover
- **Consensus MCP** (if connected): broader scientific literature
- **WebSearch**: for recent preprints or conference proceedings

When finding relevant external papers, mention them and offer to add them via the add-paper skill.

## Step 4: Structure the Review

Organize thematically, not paper-by-paper.

### For a topic survey (e.g., "T1 coherence in transmons"):

**1. Overview & Motivation** (1-2 paragraphs)
Why this matters in SCQ. Practical significance.

**2. Key Approaches** (grouped by technique/method)
For each cluster: core idea, which groups work on it, results achieved, limitations.

**3. State of the Art**
Best current numbers, who holds them, what enabled the breakthrough.

**4. Open Questions & Gaps**
What's unresolved, where does the literature disagree, what experiments haven't been tried.

**5. Papers in Our Database**
Table summarizing relevant papers with their key contributions.

**6. Suggested Additions**
Papers found externally that should be added to the collection.

### For a comparison (e.g., "compare these 4 papers on surface treatments"):

Use a table format:
| | Paper A | Paper B | Paper C | Paper D |
|---|---|---|---|---|
| Approach | ... | ... | ... | ... |
| Key metric | ... | ... | ... | ... |
| Group | ... | ... | ... | ... |

Followed by analysis of what the comparison reveals.

## Step 5: Output Format

Ask the user what they want:
- **Conversation summary**: Discuss in chat (default for quick questions)
- **Markdown document**: Save as `.md` to the workspace folder
- **Section draft**: LaTeX-ready text with `\cite{}` commands using BibTeX keys from `references.bib`

## Tips for Good Reviews

- Reference specific numbers — vague summaries aren't useful for researchers
- Note when results disagree or when a later paper supersedes an earlier one
- Include the user's notes on papers (from the `notes` table) — they contain context not in the paper
- Mention research groups by name — in SCQ, knowing which group did what matters
- If the database is sparse on a topic, say so and suggest papers to add
