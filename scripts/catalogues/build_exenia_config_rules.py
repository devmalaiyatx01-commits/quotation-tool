"""Turns the couplings stated in the Exenia catalogue into per-product configuration rules,
so the Configurator stops offering combinations the manufacturer never published.

The three couplings (parsed by restructure_exenia_fields.structure, which explains the
catalogue's line shapes) and the lighting reason each one exists:

  CRI -> CCT      A CRI grade is a different LED die, not a setting. Higher colour
                  rendering is bought with efficacy, so each grade is binned and sold in
                  its own set of colour temperatures - "CRI85 3000K" next to "CRI95 2700K,
                  3000K, 4000K" means 85 CRI is a 3000K-only part on that fixture. Offering
                  the union invents CRI85 at 4000K.

  Beam -> Wattage The optic is not independent of the power. Exenia states each optic as a
                  complete row - "Narrow Spot 11 - 8,2W - 9540cd" - one lens, the power the
                  fixture draws with it fitted, and the resulting centre-beam intensity. On
                  MUSEO REVO SMALL a 7 optic is 3W and nothing else; 8,2W at 7 does not
                  exist.

  CCT -> Beam     Tunable white needs two LED strings on one board, which only fits the
                  larger wall-wash modules: "TW 2700-4000K only for GW and AW" (grazing
                  washer and area washer). Tunable white behind a 17 spot is not orderable.

Why the rules are per product
-----------------------------
The same optic draws different power on every body size, because the bodies carry
different LED modules: "Narrow Flood 24" maps to 4,5W / 9,2W / 17,3W / 25,1W / 35,8W across
the single "Base Projectors" typology. A typology-wide rule would therefore be correct for
one product and wrong for four, which is why ConfigRule has an optional specsheet_id (see
its docstring, and migrate_config_rule_specsheet_scope.py).

What is deliberately NOT written
--------------------------------
- A rule that would constrain nothing (every CRI grade offering the product's whole CCT
  list, or a product with a single wattage). Rules that restate the default only make the
  rule table harder to read.
- A CCT->Beam rule naming optics this product does not list. Four products carry the TW
  footnote but no grazing/area washer row in the captured text (SPOT 48 REVO COMPACT and
  MEDIUM, MUSEO REVO COMPACT and MEDIUM) - almost certainly a capture gap, since the
  footnote would not be printed otherwise. Writing the rule anyway would empty their Beam
  dropdown the moment TW is picked, so TW is left unconstrained on them instead.
- Anything for an optic whose row states no wattage: the field is left unconstrained rather
  than guessed at.

This script owns the per-product rules for Exenia's CATALOGUE rows - it deletes and
rebuilds them on each run, so it must stay their single writer. Rows imported from Exenia's
website are skipped: their options come from Exenia's own configurator, where these
couplings are already explicit, and their rules belong to
restructure_coupled_field_values.py. Dry run by default; pass --commit.
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
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models
from restructure_exenia_fields import structure

MAKE = "Exenia"


def rules_for(options: dict, links: dict):
    """-> [(trigger_field, trigger_value, target_field, allowed_options), ...]"""
    out = []

    ccts = options.get("cct") or []
    cris = options.get("cri") or []
    beams = options.get("beam_angle") or []
    watts = options.get("wattage") or []

    # CRI -> CCT. Only worth a rule when the grades actually differ from each other or from
    # the product's full CCT list; a single grade constrains nothing.
    if len(cris) > 1:
        per_grade = links["cri_cct"]
        if any(set(v) != set(ccts) for v in per_grade.values()):
            for grade, allowed in per_grade.items():
                if allowed:
                    out.append(("cri", grade, "cct", allowed))

    # CCT -> Beam, restricted to optics this product actually lists.
    for cct, need in links["cct_beams"].items():
        present = [b for b in need if b in beams]
        if present and set(present) != set(beams):
            out.append(("cct", cct, "beam_angle", present))

    # Beam -> Wattage. A single-wattage product has nothing to narrow.
    if len(watts) > 1:
        for beam, allowed in links["beam_watt"].items():
            if beam in beams and allowed:
                out.append(("beam_angle", beam, "wattage", allowed))

    return out


def main():
    commit = "--commit" in sys.argv
    db = SessionLocal()
    # Catalogue rows only. The rows import_exenia_website.py created carry Exenia's own
    # configurator, where the couplings are already explicit, and their rules are owned by
    # restructure_coupled_field_values.py - deleting by make would erase those.
    sheets = [s for s in db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()
              if not (s.filename or "").endswith(".html")]

    planned = []
    by_kind = defaultdict(int)
    products_touched = set()
    for s in sheets:
        _, options, links = structure(s.raw_text or "")
        for trig_f, trig_v, target, allowed in rules_for(options, links):
            planned.append((s, trig_f, trig_v, target, allowed))
            by_kind[f"{trig_f} -> {target}"] += 1
            products_touched.add(s.id)

    print(f"Exenia specsheets            : {len(sheets)}")
    print(f"products with at least 1 rule : {len(products_touched)}")
    print(f"rules to write                : {len(planned)}")
    for k, n in sorted(by_kind.items()):
        print(f"    {k:<24} {n}")
    print()
    for s, tf, tv, target, allowed in planned:
        if s.product_name == "DARK FIX COMPACT":
            print(f"   DARK FIX COMPACT   {tf}={tv:<24} -> {target} {allowed}")

    if not commit:
        db.close()
        print("\nDRY RUN - nothing written. Re-run with --commit.")
        return

    ids = [s.id for s in sheets]
    removed = (db.query(models.ConfigRule)
               .filter(models.ConfigRule.specsheet_id.in_(ids))
               .delete(synchronize_session=False))
    for s, tf, tv, target, allowed in planned:
        db.add(models.ConfigRule(
            typology=s.typology, specsheet_id=s.id,
            triggers=[{"field": tf, "values": [tv]}],
            target_fields=[target], action="Show Only", affected_options=allowed,
        ))
    db.commit()
    total = db.query(models.ConfigRule).filter(models.ConfigRule.specsheet_id.isnot(None)).count()
    print(f"\nCOMMITTED. Replaced {removed} old rule(s); {total} per-product rule(s) now stored.")
    db.close()


if __name__ == "__main__":
    main()
