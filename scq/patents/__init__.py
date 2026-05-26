"""Patent ingestion + summarization for arXivScooper.

Patents are a first-class entity alongside arXiv papers. This package
mirrors the structure of ``scq.arxiv``:

  - ``normalize``  — the canonical ``Patent`` contract + patent-number parsing
  - ``providers``  — per-source clients (PatentsView first) that converge on ``Patent``
  - ``store``      — DB insert / fetch against the ``patents`` table
  - ``summarize``  — claim → prompt builder + ``store_summary`` (skill now, automation later)
  - ``cli``        — ``scq patents fetch|process|show``

See ``plans/patent-scraping.md`` for the full design and phasing.
"""

from __future__ import annotations

from .normalize import Patent, parse_patent_number

__all__ = ["Patent", "parse_patent_number"]
