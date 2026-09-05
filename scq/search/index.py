#!/usr/bin/env python3
"""
Build a full-text search index from PDF files.

Extracts text from each PDF in pdfs/, tokenizes it, and outputs
a search_index.json file that the HTML database can load for
full-text PDF search.

Usage:
  python tools/build_search_index.py           # Build index from pdfs/
  python tools/build_search_index.py --stats   # Show index statistics
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

# This module lives at scq/search/index.py; PROJECT_DIR is two levels up.
# Note: this is a legacy full-text search index (replaced by SQLite FTS5;
# kept for special-case bulk reindexing).
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parents[1]
PDFS_DIR = PROJECT_DIR / "pdfs"
INDEX_FILE = PROJECT_DIR / "search_index.json"


def extract_text_from_pdf(pdf_path):
    """Extract full text from a PDF using PyMuPDF."""
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if text:
                pages.append({"page": i + 1, "text": text})
        doc.close()
        return pages
    except ImportError:
        print("  [warn] PyMuPDF not installed. Trying pdftotext...")
        try:
            import subprocess

            result = subprocess.run(
                ["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, timeout=60
            )
            if result.stdout.strip():
                return [{"page": 1, "text": result.stdout.strip()}]
        except Exception:
            # pdftotext missing or failed: no text for this PDF.
            pass
    return []


def tokenize(text):
    """Simple tokenization: lowercase, split on non-alphanumeric."""
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())


def build_index():
    """Build search index from all PDFs in pdfs/ directory."""
    if not PDFS_DIR.exists():
        print(f"No pdfs/ directory found at {PDFS_DIR}")
        return

    pdfs = sorted(PDFS_DIR.glob("*.pdf"))
    if not pdfs:
        print("No PDF files found in pdfs/")
        return

    print(f"Found {len(pdfs)} PDF(s) in pdfs/\n")

    index = {"version": 1, "builtAt": "", "papers": {}}

    total_pages = 0
    total_words = 0

    for pdf in pdfs:
        paper_id = pdf.stem  # filename without .pdf
        print(f"  Indexing: {pdf.name}...", end=" ")

        pages = extract_text_from_pdf(pdf)
        if not pages:
            print("(no text extracted)")
            continue

        # Build per-page snippets and word frequency
        page_texts = []
        word_freq = Counter()

        for p in pages:
            # Clean text: collapse whitespace, remove very short lines
            clean = re.sub(r"\s+", " ", p["text"]).strip()
            tokens = tokenize(clean)
            word_freq.update(tokens)

            # Store abbreviated page text (first 500 chars per page for snippet search)
            page_texts.append({"p": p["page"], "t": clean[:500]})

        # Top terms (excluding very common words)
        stop_words = {
            "the",
            "and",
            "for",
            "are",
            "but",
            "not",
            "you",
            "all",
            "can",
            "had",
            "her",
            "was",
            "one",
            "our",
            "out",
            "has",
            "have",
            "this",
            "that",
            "with",
            "from",
            "they",
            "been",
            "said",
            "each",
            "which",
            "their",
            "will",
            "other",
            "about",
            "many",
            "then",
            "them",
            "these",
            "some",
            "would",
            "into",
        }
        top_terms = [w for w, c in word_freq.most_common(100) if w not in stop_words and len(w) > 2]

        index["papers"][paper_id] = {
            "pages": page_texts,
            "topTerms": top_terms[:50],
            "pageCount": len(pages),
            "wordCount": sum(word_freq.values()),
        }

        total_pages += len(pages)
        total_words += sum(word_freq.values())
        print(f"{len(pages)} pages, {sum(word_freq.values())} words")

    from datetime import datetime

    index["builtAt"] = datetime.now().isoformat()

    with open(INDEX_FILE, "w") as f:
        json.dump(index, f)

    file_size = INDEX_FILE.stat().st_size
    print(f"\nIndex written to: {INDEX_FILE.name}")
    print(f"  Papers: {len(index['papers'])}")
    print(f"  Pages: {total_pages}")
    print(f"  Words: {total_words:,}")
    print(f"  File size: {file_size // 1024}KB")


def show_stats():
    """Show statistics from existing index."""
    if not INDEX_FILE.exists():
        print("No search index found. Run without --stats to build one.")
        return

    with open(INDEX_FILE) as f:
        index = json.load(f)

    print("Search Index Statistics")
    print(f"  Built: {index.get('builtAt', 'unknown')}")
    print(f"  Papers: {len(index.get('papers', {}))}")
    for pid, data in index.get("papers", {}).items():
        print(f"    {pid}: {data['pageCount']} pages, {data['wordCount']:,} words")
        print(f"      Top terms: {', '.join(data['topTerms'][:10])}")


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if "--stats" in args:
        show_stats()
    else:
        build_index()
    return 0


if __name__ == "__main__":
    sys.exit(main())
