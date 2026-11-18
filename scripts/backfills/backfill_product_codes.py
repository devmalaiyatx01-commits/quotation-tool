"""Records each product's own ordering code, from the source that actually publishes it.

A BOQ line is what a purchase order is typed from, so the code has to be the manufacturer's
real one. Nothing here derives or infers a code: a plausible-looking wrong code orders the
wrong fixture, and no code at all is the safer failure. Where a make publishes none for a
product, the row simply gets none.

Each make states it differently, so each is read from its own source:

  InHouse     The code is in the datasheet itself, and usually in the product's own name -
              "RAYON Spot -LL103002", "Filo S 05 -LL 106001". Local, no fetch needed.

  Duragreen   NONE. They publish no ordering code, so their rows get none. Checked on the
              rendered pages (Playwright, as the importer sees them): the WooCommerce SKU
              field is EMPTY on all 16 variants of acme-fixed, there is no "product code"
              anywhere in the text, and no sku in the ld+json. A variant is identified only
              by its description - "36 Mini-Trim Fixed 5W, CRI 92".
              Two things on those pages look like codes and are not. `data-product_id`
              (14567, 14568...) is WordPress's internal post id, meaningless to a supplier.
              And the spec-PDF filenames are sometimes code-shaped ("MFL36.pdf") but usually
              a description ("Alabaster-Luna-57-Downlight-1.pdf"), so reading them would
              invent codes for most of the catalogue. Order Duragreen by description.

  Exenia      There is no single code. Their configurator composes one from the SKU of every
              option chosen - housing body 134, version 80, control R, finish W, colour 22,
              optic V - so the full code only exists once a fixture is fully specified. What
              IS fixed for a library row is the stem: the housing body and the version, since
              this app already splits versions into their own rows. That stem is recorded,
              and the per-option SKUs are kept alongside it so the rest can be completed.

Dry run by default; pass --commit to write. Pass a make to limit it.
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
import re
import sys
import time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models

# "LL103002", "LL 106001" - InHouse print it with or without the space.
INHOUSE_CODE = re.compile(r"\bLL\s?(\d{4,7})\b")


# The step that names the luminaire itself, whose SKU opens the code. Exenia calls it three
# things across the catalogue - "Housing body" on 195 pages, "Apparel Body" on 14 and
# "System body" on CACTUS - and a page missing from this list yields no code at all, which
# is how the four CACTUS rows came back empty on the first pass. Lower-cased to compare.
BODY_STEPS = ("housing body", "apparel body", "system body")


def inhouse_code(s) -> str:
    """The code InHouse prints on the datasheet. The product name is checked first: where a
    sheet covers several products the name is the only thing that says which one this row is."""
    for text in (s.product_name or "", s.raw_text or ""):
        m = INHOUSE_CODE.search(text)
        if m:
            return "LL" + m.group(1)
    return ""


def exenia_code(s) -> tuple:
    """-> (stem, {step: {option: sku}}). The stem is housing body + version, which is fixed
    for this row; everything after it depends on what the specifier picks."""
    skus = (s.params or {}).get("Option codes (Exenia)") or {}
    if not skus:
        return "", {}
    try:
        parsed = {k: json.loads(v) if isinstance(v, str) else v for k, v in skus.items()}
    except ValueError:
        return "", {}
    body = next((list(v.values())[0] for k, v in parsed.items()
                 if k.lower() in BODY_STEPS and v), "")
    version = ""
    for k, v in parsed.items():
        if k.lower() == "version" and isinstance(v, dict):
            version = v.get(s.variant or "", "")
    return f"{body}{version}", parsed


def main():
    commit = "--commit" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    db = SessionLocal()

    planned = []
    for s in db.query(models.Specsheet).all():
        if only and s.make not in only:
            continue
        code = ""
        if s.make == "InHouse":
            code = inhouse_code(s)
        elif s.make == "Exenia":
            code, _ = exenia_code(s)
        if code and (s.fields or {}).get("product_code") != code:
            planned.append((s, code))

    by_make = Counter(s.make for s, _ in planned)
    total = Counter(s.make for s in db.query(models.Specsheet).all()
                    if not only or s.make in only)
    print("rows that would gain a product code:")
    for make in sorted(total):
        print(f"   {make:<12} {by_make.get(make, 0):>4} of {total[make]}")
    print()
    for s, code in planned[:10]:
        print(f"   {s.make:<11} {s.product_name[:34]:<36} {code}")

    if not commit:
        db.close()
        print("\nDRY RUN - nothing written. Re-run with --commit.")
        return

    for s, code in planned:
        fields = dict(s.fields or {})
        fields["product_code"] = code
        s.fields = fields
    db.commit()
    print(f"\nCOMMITTED. {len(planned)} row(s) now carry their product code.")
    db.close()


if __name__ == "__main__":
    main()
