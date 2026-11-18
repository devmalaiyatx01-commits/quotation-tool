"""Every code table DuraGreen prints, checked against the codes printed on the spec sheets.

    python scripts/maintenance/verify_duragreen_nomenclature.py [--csv PATH]

For each table edition (Duragreen Catalogue/duragreen_code_matrices.json) and the products it is meant
for - ACME's editions for the ACME range, MOOD's for MOOD, EVOLINE's for EVOLINE, BELLA 41 / GLAMO 68 /
ARTFOCUS 36 for the products whose printed codes start with that table's own type and engine - every
piece of every printed code is read against the row it is printed for:

    agrees        the table writes the row's option exactly as the printed code does
    differs       the table writes it otherwise (both are DuraGreen's words: one of them is out of date)
    not listed    the table has no entry for that option
    type known / type not in table   the code's type letters, against the table's Type column

Read-only: the database and the documents are only read.
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
import argparse
import csv
import json
from collections import Counter, defaultdict

from app import duragreen, models
from app.database import SessionLocal

MATRICES = _REPO_ROOT / "Duragreen Catalogue" / "duragreen_code_matrices.json"
PREFIXED = {"BELLA 41": "RT41", "GLAMO 68": "RT68", "ARTFOCUS 36": "RT36"}


def table_entries(edition):
    """{field: [(meaning, code, source)]} of one printed table."""
    out = defaultdict(list)
    for seg in edition["segments"]:
        for e in seg["entries"]:
            out[seg["field"]].append((e["meaning"], e["code"], edition["source"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(_REPO_ROOT / "Duragreen Catalogue" / "nomenclature_verified.csv"))
    args = ap.parse_args()
    editions = json.loads(MATRICES.read_text(encoding="utf-8"))["editions"]
    db = SessionLocal()
    try:
        sheets = {s.id: s for s in db.query(models.Specsheet).filter(models.Specsheet.make == duragreen.MAKE)}
        codes_by = defaultdict(list)
        for c in db.query(models.VendorOrderCode).filter(models.VendorOrderCode.make == duragreen.MAKE):
            codes_by[c.specsheet_id].append(c)
    finally:
        db.close()

    range_of = {}
    for sid, codes in codes_by.items():
        template = next((c for c in codes if duragreen.is_example(c)), None)
        range_of[sid] = duragreen.range_of(template)

    rows, totals = [], defaultdict(Counter)
    for ed in editions:
        rng = ed["range"]
        if rng.startswith("THE CHOSEN"):
            mine = [sid for sid, s in sheets.items() if (s.category or "") == "The Chosen Ones"]
        elif rng in PREFIXED:
            mine = [sid for sid, codes in codes_by.items()
                    if any(c.code.startswith(PREFIXED[rng]) and not duragreen.is_example(c) for c in codes)]
        else:
            mine = [sid for sid, r in range_of.items() if r == rng]
        label = "%s | %s %s p.%s" % (rng, ed["date"], ed["source"][:40], ed["printed_page"] or ed["pdf_page"])
        entries = table_entries(ed)
        types = {e["code"] for e in ed["segments"][0]["entries"]}
        for sid in mine:
            for c in codes_by[sid]:
                if duragreen.is_example(c) or not c.parts:
                    continue
                cfg = c.config or {}
                for name, token in c.parts:
                    if name == "type":
                        k = "type known" if token in types else "type not in table"
                        totals[label][k] += 1
                        if k != "type known":
                            rows.append([label, sid, sheets[sid].product_name, c.code, "type", token, "", k, ""])
                        continue
                    field = duragreen.PART_FIELD.get(name)
                    value = cfg.get(field) if field else None
                    if not field or not value or str(value).startswith("?"):
                        continue
                    found, loose = duragreen._match(field, value, entries.get(field, []))
                    toks = sorted({t for t, _s in found}) if found and not loose else []
                    if not toks:
                        k, said = "not listed", ""
                    elif token in toks:
                        k, said = "agrees", token
                    else:
                        k, said = "differs", "/".join(toks)
                    totals[label][k] += 1
                    totals[label][k + " (" + field + ")"] += 1
                    if k != "agrees":
                        rows.append([label, sid, sheets[sid].product_name, c.code, field, token, value, k, said])
        totals[label]["products"] = len(mine)

    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table", "specsheet_id", "product", "printed code", "piece", "printed", "for option", "result", "table writes"])
        w.writerows(rows)
    for label, t in totals.items():
        main_keys = ["products", "agrees", "differs", "not listed", "type known", "type not in table"]
        print("%-70s %s" % (label, "  ".join("%s=%d" % (k, t[k]) for k in main_keys)))
        detail = sorted((k, v) for k, v in t.items() if "(" in k and not k.startswith("agrees"))
        if detail:
            print("      " + ", ".join("%s=%d" % kv for kv in detail))
    print("\ndetail:", args.csv)


if __name__ == "__main__":
    main()
