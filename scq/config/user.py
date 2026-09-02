"""Python-side config loader. Mirrors src/config/loader.js.

Reads the **same** files the browser fetches:

  - defaults  : src/config/defaults/<domain>.json
  - schema    : src/config/schema/<domain>.schema.json
  - override  : data/user_config/<domain>.json   (gitignored, optional)

Merges defaults + override (deep-merge, with id-keyed array merge for arrays
whose items schema declares ``x-mergeKey``), validates against the schema, and
returns the merged dict. Validation uses the ``jsonschema`` library so behavior
matches a reference implementation byte-for-byte.

Public API::

    from scq.config import user as cfg
    digest = cfg.load_config('digest')                  # → dict
    every  = cfg.load_all()                             # → {domain: dict}
    for k, v in cfg.errors_for('digest'):
        print(k, v)

Custom JSON Schema keywords supported (in addition to standard Draft 2020-12):
    x-mergeKey  on an array's `items` — merge by `<value of that field>`
                instead of replacing the whole array.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]

from .paths import paths as _paths

# Must stay in sync with src/config/loader.js MANIFEST.
MANIFEST: tuple[str, ...] = (
    "digest",
    "citations",
    "ui",
    "ingest",
    "email",
    "watchlist",
    "privacy",
    "search-sources",
    "auto-tag-rules",
    "relevance",
)


@dataclass(frozen=True)
class LoadResult:
    data: dict[str, Any]
    source: str  # "defaults" | "merged"
    errors: tuple[str, ...]


def load_config(domain: str, *, repo_root: Path | None = None) -> LoadResult:
    """Load and validate one domain. Returns a LoadResult.

    Validation errors are collected (not raised) so callers can decide whether
    to proceed; this matches the JS-side semantics where the merged data is
    returned alongside any error list.
    """
    if domain not in MANIFEST:
        raise ValueError(f"unknown config domain {domain!r}; known: {', '.join(MANIFEST)}")
    root = repo_root or _paths().repo_root
    defaults = _read_json(root / "src" / "config" / "defaults" / f"{domain}.json", required=True)
    override_path = root / "data" / "user_config" / f"{domain}.json"
    override = _read_json(override_path, required=False)
    schema = _read_json(root / "src" / "config" / "schema" / f"{domain}.schema.json", required=True)

    if override is None:
        merged = copy.deepcopy(defaults)
        source = "defaults"
    else:
        merged = schema_aware_merge(defaults, override, schema)
        source = "merged"

    cleaned = _strip_meta_schema_key(merged)
    errors = tuple(_validate(cleaned, schema))
    return LoadResult(data=cleaned, source=source, errors=errors)


def load_all(*, repo_root: Path | None = None) -> dict[str, LoadResult]:
    """Load every domain in the manifest. Returns ``{domain: LoadResult}``."""
    return {d: load_config(d, repo_root=repo_root) for d in MANIFEST}


def errors_for(domain: str, *, repo_root: Path | None = None) -> tuple[str, ...]:
    """Convenience: just the validation errors for one domain."""
    return load_config(domain, repo_root=repo_root).errors


# ─── Schema-aware merge (port of src/config/loader.js) ───


def schema_aware_merge(a: Any, b: Any, schema: Any) -> Any:
    """Like deep-merge, but for arrays whose items declare ``x-mergeKey``,
    merge entries by that key instead of replacing the array.

    See loader.js for the full description; behavior is intentionally
    identical.
    """
    if not isinstance(schema, dict):
        return _deep_merge(a, b)

    # Object: recurse property-by-property
    if isinstance(a, dict) and isinstance(b, dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        out = dict(a)
        for k, v in b.items():
            sub = props.get(k) if isinstance(props, dict) else None
            if sub is not None:
                out[k] = schema_aware_merge(out.get(k), v, sub)
            elif isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = v
        return out

    # Array with x-mergeKey: id-merge
    if (
        isinstance(a, list)
        and isinstance(b, list)
        and isinstance(schema.get("items"), dict)
        and isinstance(schema.get("x-mergeKey"), str)
    ):
        key = schema["x-mergeKey"]
        items_schema = schema["items"]
        a_by_id: dict[Any, Any] = {}
        order: list[Any] = []
        for item in a:
            if isinstance(item, dict) and key in item:
                a_by_id[item[key]] = item
                order.append(item[key])
        for item in b:
            if not isinstance(item, dict) or key not in item:
                continue
            if item[key] in a_by_id:
                a_by_id[item[key]] = schema_aware_merge(a_by_id[item[key]], item, items_schema)
            else:
                a_by_id[item[key]] = item
                order.append(item[key])
        return [a_by_id[k] for k in order]

    # Anything else: replace
    return b if b is not None else a


def _deep_merge(a: Any, b: Any) -> Any:
    """Plain deep-merge; objects recurse, arrays/scalars in b win."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return a if b is None else b
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ─── internals ───


def _read_json(path: Path, *, required: bool) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"required config file missing: {path}")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e


def _strip_meta_schema_key(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if k != "$schema"}
    return obj


def _validate(data: Any, schema: dict[str, Any]) -> list[str]:
    """Run validation. Returns a list of human-readable error strings.

    Strips the meta `$schema` URL from the *schema* itself before validating
    (jsonschema would otherwise try to fetch it as a $id). The custom
    ``x-mergeKey`` keyword is not a standard JSON Schema keyword; jsonschema
    ignores unknown keywords by default, which is what we want.
    """
    # Use Draft 2020-12; it's what our schemas declare via $schema.
    # Pass FormatChecker so `format: "email"` is actually validated — without
    # this, jsonschema treats `format` as informational and the JS side would
    # disagree with us on whether 'not an email' is valid.
    validator_cls = jsonschema.Draft202012Validator
    cleaned_schema = dict(schema)
    cleaned_schema.pop("$schema", None)
    cleaned_schema.pop("$id", None)
    validator = validator_cls(
        cleaned_schema,
        format_checker=validator_cls.FORMAT_CHECKER,
    )
    return [
        f"$.{'.'.join(str(p) for p in err.absolute_path)}: {err.message}"
        if err.absolute_path
        else f"$: {err.message}"
        for err in validator.iter_errors(data)
    ]


__all__ = [
    "MANIFEST",
    "LoadResult",
    "load_config",
    "load_all",
    "errors_for",
    "schema_aware_merge",
]
