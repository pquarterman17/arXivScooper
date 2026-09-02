#!/usr/bin/env python3
"""Compatibility shim — moved to scq.ingest.watch (plan #12 wave 2).

Existing callers (`python tools/watch_imports.py [...]`) keep working unchanged;
new code should use `python -m scq.ingest.watch` or the `scq` CLI.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scq.ingest.watch import main  # noqa: E402

if __name__ == "__main__":
    main()
