"""Make the generated specsheet say exactly what NPD holds - no more, no less.

The sheet's parameter column is drawn from `params`. The NPD form is drawn from the
typology's ParameterTemplate, reading each parameter's value out of `fields`. For a product
authored here the two agree by construction, because both are built from the same template on
save. For the 134 products read from a manufacturer's PDF and later brought under NPD they did
not, in three separate ways:

  * 851 readings were printed on the sheet with nothing in `fields` behind them - SOURCE,
    POWER FACTOR, T.H.D, the driver's own IP rating. The parser wrote them into `params` and
    never flattened them, so the sheet stated a fact the form showed as blank and no one could
    correct.
  * 70 kinds of reading were printed with no parameter in the template at all - CONTROL,
    OVERALL WARRANTY, IK RATING, LENS MATERIAL. Not merely uneditable: invisible.
  * A few parameters held a value in `fields` that the sheet never printed, so editing them
    changed nothing anybody could see.

This reconciles all three, in the only direction that keeps the sheet honest:

  1. Every parameter any product prints gets a row in its typology's template, so it can be
     seen and edited. Group and label are restored to how the page reads ("POWER SUPPLY
     PARAMETERS", not "POWERSUPPLYPARAMETERS") and the value is keyed the way the rest of the
     app already keys it, POWER under Source being the LED's draw and POWER under Power Supply
     being the driver's rating - two facts, two keys.
  2. Where `fields` has no value for a parameter the sheet prints, the printed value is copied
     into it. Where `fields` already holds one, it wins on content: it is the fuller reading of
     the two wherever they disagree (SOLARA Midi's control is "ON-OFF / DALI / 0-10V / Phase
     Cut" in the form against three of those four on the sheet). It does not win on spacing -
     where the two are the same words, the better-spaced text is kept, since a value can reach
     `params` off a cleaner parse than the one `fields` was flattened from.
  3. `params` is then rebuilt from the template and `fields`, exactly as saving the product
     through the form would. After this the sheet cannot disagree with the form, because it is
     generated from it.

`fields` is never rebuilt, only added to - it carries more than the template does (model_no,
sub_typology, and the parsed detail the Configurator matches on), and none of that should be
lost to make a printing decision.

    python scripts/maintenance/reconcile_npd_parameters.py --dry-run
    python scripts/maintenance/reconcile_npd_parameters.py
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
import argparse
import re
import collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal
from app import models, npd
from seed_parameter_templates import display_label, scoped_field_key


def tight(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def printed_readings(sheet):
    """[(group as printed, label as printed, value)] for one product's parameter table."""
    out = []
    for group, entries in (sheet.params or {}).items():
        if not isinstance(entries, dict):
            continue
        for label, value in entries.items():
            if str(value or "").strip():
                out.append((group, label, str(value).strip()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    rows = db.query(models.Specsheet).filter(models.Specsheet.origin == "npd").all()
    print("NPD products: %d" % len(rows))

    templates = collections.defaultdict(list)
    for t in db.query(models.ParameterTemplate).all():
        templates[(t.discipline, t.typology)].append(t)

    # ---- 1. a parameter for everything any product prints --------------------------------
    wanted = collections.OrderedDict()   # (discipline, typology, group, label) -> field_key
    multi_valued = set()
    for s in rows:
        scope = (s.discipline or "architectural", s.typology or "")
        for group, label, value in printed_readings(s):
            key = (scope[0], scope[1], display_label(group), display_label(label))
            wanted.setdefault(key, scoped_field_key(group, label))
            if len(npd.split_options(value)) > 1:
                multi_valued.add(key)

    added = 0
    for (discipline, typology, group, label), field_key in wanted.items():
        if not typology:
            continue                     # nothing to hang a template on
        existing = templates[(discipline, typology)]
        if any(tight(t.group_name) == tight(group) and tight(t.label) == tight(label)
               for t in existing):
            continue
        row = models.ParameterTemplate(
            discipline=discipline, typology=typology, group_name=group, label=label,
            field_key=field_key,
            sort_order=(max([t.sort_order or 0 for t in existing], default=0) + 1),
            # A parameter whose printed value lists several options is one the product offers
            # a choice of - which is what makes it a candidate for a price adder.
            is_configurable=(discipline, typology, group, label) in multi_valued,
            user_added=False)
        db.add(row)
        templates[(discipline, typology)].append(row)
        added += 1
    print("parameters added to templates so they can be edited: %d" % added)
    if added and not args.dry_run:
        db.flush()

    # ---- sub-typology, read off the line the sheet already prints ------------------------
    # How a luminaire is installed is the first word of its description - "Recessed LED
    # Downlight", "Semi Recessed...", "Inground LED Uplight". Held as its own field so it can
    # be filtered on and carry a short code later, and so the composed title can reproduce
    # the printed line instead of dropping a word from the front of it.
    subs = 0
    for s in rows:
        fields = dict(s.fields or {})
        if str(fields.get("sub_typology") or "").strip():
            continue
        match = re.match(r"^(.{2,28}?)\s*LED\b", str(s.category or ""), re.I)
        if not match:
            continue
        candidate = match.group(1).strip(" -–—")
        if not candidate or len(candidate.split()) > 3:
            continue
        fields["sub_typology"] = candidate
        s.fields = fields
        subs += 1
    print("sub-typology read off the printed line: %d" % subs)

    # ---- 2 and 3. backfill the values, then rebuild the printed table ---------------------
    backfilled = rebuilt = skipped = respaced = 0
    changed_rows = collections.Counter()
    for s in rows:
        scope = (s.discipline or "architectural", s.typology or "")
        tpl = sorted(templates.get(scope, []),
                     key=lambda t: ((t.sort_order or 0), t.id or 0))
        if not tpl:
            skipped += 1
            continue

        by_pair = {(tight(t.group_name), tight(t.label)): t for t in tpl}
        fields = dict(s.fields or {})
        for group, label, value in printed_readings(s):
            t = by_pair.get((tight(display_label(group)), tight(display_label(label))))
            if t is None:
                continue
            current = str(fields.get(t.field_key) or "")
            if not current.strip():
                fields[t.field_key] = value
                backfilled += 1
            elif tight(current) == tight(value) and value.count(" ") > current.count(" "):
                # The same reading, written more legibly. `fields` wins on content, because
                # it holds the fuller option lists - but not on spacing: a value can reach
                # `params` off a cleaner parse than the one `fields` was flattened from, and
                # letting the flattened text win printed "220VAC-240VAC" on a sheet that used
                # to read "220V AC - 240V AC". Same words, so nothing is chosen between.
                fields[t.field_key] = value
                respaced += 1
        if fields != (s.fields or {}):
            s.fields = fields

        # Which parameters this product treats as a choice, the same way the edit form
        # recovers them, so rebuilding does not quietly turn a choice into a fixed value.
        affects = {k: True for k in (s.options or {}) if k != "accessories"}
        params, _, _ = npd.assemble(tpl, s.fields, options_by_key=s.options or {},
                                    configurable_keys=affects or None)
        if params != (s.params or {}):
            before = sum(len(v) for v in (s.params or {}).values() if isinstance(v, dict))
            after = sum(len(v) for v in params.values() if isinstance(v, dict))
            changed_rows[after - before] += 1
            s.params = params
            rebuilt += 1

    print("values copied from the sheet into the form: %d" % backfilled)
    print("values re-spaced from the sheet's own text : %d" % respaced)
    print("products whose printed table was rebuilt  : %d" % rebuilt)
    if skipped:
        print("products left alone (their typology has no template): %d" % skipped)
    if changed_rows:
        print()
        print("change in printed rows per product:")
        for delta, n in sorted(changed_rows.items()):
            print("   %+d row(s): %d product(s)" % (delta, n))

    if args.dry_run:
        db.rollback()
        print()
        print("--dry-run: nothing written.")
    else:
        db.commit()
        print()
        print("done.")
    db.close()


if __name__ == "__main__":
    main()
