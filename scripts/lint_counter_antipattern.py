#!/usr/bin/env python3
"""Lint for the PG-107 counter antipattern.

A ``fields.Integer(compute=...)`` declared on a model WITHOUT ``store=True``
will be recomputed on every read. When such a field is named ``*_count`` and
appears in a ``<list>`` (or legacy ``<tree>``) view, Odoo recomputes it once
per row on every list-view paint — frequently turning O(rows) reads into
O(rows * children) DB queries.

This script flags those cases. Run with no arguments from the repo root:

    python scripts/lint_counter_antipattern.py

Exits 0 if clean, 1 if any antipatterns found.

Limitations
-----------
* Heuristic regex scan, not an AST walk. Multi-line field declarations are
  handled via a 6-line lookahead; very-exotic formatting may slip past.
* Only flags fields whose *name ends in _count*; ignores other compute fields.
* Cross-addon view references (rare) are caught because we glob all addons.
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ADDONS_DIR = REPO_ROOT / "addons"

# Matches the OPENING of a fields.Integer(...) declaration. The body may span
# multiple lines; we slurp up to 6 lines after the open-paren to test for
# `compute=` and `store=True`.
FIELD_RE = re.compile(
    r"^\s*(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*fields\.Integer\s*\(",
)


def scan_models() -> list[dict]:
    """Return list of antipattern-candidate fields (name + file + line)."""
    candidates: list[dict] = []
    for py_file in ADDONS_DIR.glob("*/models/*.py"):
        text = py_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            m = FIELD_RE.match(line)
            if not m:
                continue
            name = m.group("name")
            if not name.endswith("_count"):
                continue
            # Slurp the field body (open paren may not close on the same line).
            body = "\n".join(lines[idx : idx + 6])
            if "compute=" not in body:
                continue
            if re.search(r"\bstore\s*=\s*True\b", body):
                continue
            candidates.append(
                {
                    "name": name,
                    "file": py_file.relative_to(REPO_ROOT),
                    "line": idx + 1,
                }
            )
    return candidates


def field_in_list_view(name: str) -> list[pathlib.Path]:
    """Return XML view files that reference <field name="NAME"/> inside a
    <list>/<tree> block."""
    hits: list[pathlib.Path] = []
    field_ref = f'name="{name}"'
    block_re = re.compile(r"<(list|tree)\b.*?</\1>", re.DOTALL)
    for xml_file in ADDONS_DIR.glob("*/views/*.xml"):
        try:
            text = xml_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if field_ref not in text:
            continue
        for block in block_re.findall(text):
            if field_ref in block:
                hits.append(xml_file.relative_to(REPO_ROOT))
                break
    return hits


def main() -> int:
    candidates = scan_models()
    flagged: list[dict] = []
    for c in candidates:
        view_hits = field_in_list_view(c["name"])
        if view_hits:
            c["views"] = view_hits
            flagged.append(c)

    if not flagged:
        print("OK: no counter-antipattern fields found "
              f"(scanned {len(candidates)} candidate *_count fields).")
        return 0

    print(f"FAIL: {len(flagged)} counter-antipattern field(s) found.\n")
    for c in flagged:
        print(f"  {c['file']}:{c['line']}  {c['name']}  "
              f"(compute, no store=True, rendered in list view)")
        for v in c["views"]:
            print(f"    -> {v}")
    print()
    print("Fix: add store=True (and any missing @api.depends) or move the "
          "field off the list view.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
