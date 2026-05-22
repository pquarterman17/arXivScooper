# `data/` — runtime data

This directory holds **user-specific runtime data**, most of which is gitignored.

- `arxiv_scooper.db` — the SQLite database (gitignored; will be relocated to OneDrive in plan #2)
- `migrations/` — versioned SQL migrations (committed). Applied in order by `scq/db/migrations.py`.
- `user_config/` — user overrides for shipped defaults (gitignored except for `.example` templates)

`scq init` creates a fresh DB by running all migrations against an empty SQLite
file at the configured `paths.db_path`, and copies any `.example` templates in
`user_config/` to their non-example names.
