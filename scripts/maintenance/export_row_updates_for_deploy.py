"""Exports corrections to rows a deployment ALREADY has, matched by the manufacturer's URL.

export_specsheets_for_deploy.py moves a make a server does not have yet, as new rows. This is
the other half: the server already holds these products, its BOQ items and prices point at
them by id, and what has changed is the content of the rows - option codes, accessories,
photographs, classification. Those have to be updated in place, because inserting them again
would duplicate the catalogue and deleting them would orphan whatever references them.

The join is (source_url, variant), not id. Ids differ between machines - the two libraries
were built by separate import runs - but a manufacturer's product page plus the version of it
is the same fact on both, and is unique: 431 Exenia rows, 431 distinct keys.

What is deliberately NOT exported, so that a correction can never overwrite something the
server is the authority on:

  list_price / list_price_by_wattage   people set prices on the server, not here
  id, and anything referencing it      BOQ items, price entries, users
  image_path as a path                 only the bare filename travels; the importer
                                       rebuilds it against the server's own data directory

    python scripts/maintenance/export_row_updates_for_deploy.py Exenia -o row_updates.json

Read-only. The companion is apply_row_updates_on_deploy.py, run on the server.
"""
# --- repo-root shim ------------------------------------------------------------------
# Running a script file puts ITS OWN directory on sys.path, not the repo root, so `app`
# would not import and a `Path(__file__).parent` would no longer be the project root.
# Both are fixed here by walking up to the directory that contains app/ - the same rule
# app/database.py uses. Run this script from anywhere; paths below stay repo-relative.
import pathlib as _pathlib, sys as _sys
_REPO_ROOT = next((p for p in _pathlib.Path(__file__).resolve().parents
                   if (p / "app").is_dir()), None)
if _REPO_ROOT is None:
    raise SystemExit("Cannot find the repo root: no parent directory contains app/.")
_sys.path.insert(0, str(_REPO_ROOT))
# --- end shim ------------------------------------------------------------------------
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models

# Everything that describes the product itself. Prices and ids are excluded on purpose - see
# the module docstring.
COLUMNS = ["filename", "category", "typology", "product_name", "series", "family", "variant",
           "environment", "page_number", "make", "raw_text", "params", "fields", "options",
           "source_url"]


def main():
    argv = sys.argv[1:]
    out = "row_updates.json"
    if "-o" in argv:
        i = argv.index("-o")
        out, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    makes = [a for a in argv if not a.startswith("-")] or ["Exenia"]

    db = SessionLocal()
    rows, retired, skipped = [], [], 0
    for s in db.query(models.Specsheet).filter(models.Specsheet.make.in_(makes)).all():
        if not s.source_url:
            # No stable key, so it cannot be matched on the server and is not exported.
            skipped += 1
            continue
        rec = {c: getattr(s, c) for c in COLUMNS}
        rec["image_name"] = os.path.basename(s.image_path) if s.image_path else ""
        rec["diagram_names"] = [os.path.basename(d.get("path", ""))
                                for d in (s.diagrams or []) if d.get("path")]
        rows.append(rec)

    # Rows this machine deliberately removed, so the server can retire the same ones. Named,
    # never id'd, and the server re-checks that nothing references them before removing any.
    for make in makes:
        retired.append({"make": make})

    payload = {"makes": makes, "rows": rows,
               "note": "match on (source_url, variant); see export_row_updates_for_deploy.py"}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    size = os.path.getsize(out) / 1024 / 1024

    print(f"exported {len(rows)} row(s) across {len(makes)} make(s) -> {out} ({size:.1f} MB)")
    for make, n in Counter(r["make"] for r in rows).most_common():
        print(f"   {make:<14} {n}")
    if skipped:
        print(f"   {skipped} row(s) had no source_url and cannot be matched - not exported")
    print(f"   rows carrying an image: {sum(1 for r in rows if r['image_name'])}")
    db.close()


if __name__ == "__main__":
    main()
