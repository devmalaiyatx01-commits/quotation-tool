"""Fills the InHouse accessory library from the products' own datasheets: one record per
accessory, and every product linked to the accessories its datasheet lists.

Why, and why now
----------------
The library (app/accessory_parts.py) started empty, to be filled by people picking accessories in
each product's NPD form. 135 products were waiting, and almost every accessory they list is
already written on their datasheets - so on the owner's instruction (6 Oct 2026) this links them
from there in one pass, and people correct what the datasheets cannot tell.

What the datasheets can and cannot tell
---------------------------------------
  * An accessory the datasheet names with nothing more ("HONEYCOMB") becomes ONE record, shared
    by every product that lists that name. Whether the NEBEL Mini's honeycomb and the NEBEL
    Maxi's are the same physical part, nothing on the sheets says, so they are left as one for
    now ("let them be as is") - splitting one later into variants is an edit in the library.
  * A profile is told apart by its PROFILE DIMENSION line: each size becomes a variant
    ("Profile Dimension 17mm X 15mm"), and a product takes every size its sheet lists. When a
    sheet lists two profiles, the line says nothing about which size is whose. The drawings do:
    on Filo S 10 and Vena S 10 the 16 x 16 mm drawing is the quarter-round TRIANGULAR PROFILE and
    the rest are U-shape channels. That one arrangement is the only one decided here; any other
    leaves the profiles without sizes and is reported.
  * Pictures are not touched (the owner adds them).

Codes - why each record takes the abbreviation it already had
--------------------------------------------------------------
A library accessory's own code replaces the name's abbreviation in a product's code
(product_codes.Context.with_overrides), and one with no code is reported missing. So each new
record takes the abbreviation its name already has in NPD -> Product codes, and a product's code
says exactly what it said before. Two exceptions, both reported:
  * Variants of one name need codes of their own, so a profile size gets the abbreviation and the
    size, the pattern the first one was given by hand (U-shape 35x35 = USP35X35).
  * Two different names with one abbreviation (END CAP and END CAPS are both EC) cannot both have
    it: library codes are unique. The name on more products takes it, and the other is left as
    its datasheet name - exactly as it is today - for a person to say whether it is the same
    accessory (pick it) or a different one (give it a code).
Records that already exist (the U-shape profile 35x35, Honeycomb) keep what a person gave them.

What it never touches
---------------------
Prices (no InHouse product has a per-product accessory price - it checks, and stops if one
appears), BOQ lines, pictures, approval status. A product's printed ACCESSORIES line is only
rewritten where the list itself changed - a product taking two sizes of a profile names both
("'U'-SHAPE PROFILE (17mm X 15mm)") - as saving the product would (write_accessory_list).

Undo
----
A run writes data/backfill_records/inhouse_accessory_library_<time>.json: every record and link
it created and each product's lists before. `--undo <file>` puts them back, and refuses for any
record a person has since priced, pictured or used on a BOQ line.

    python scripts/backfills/backfill_inhouse_accessory_library.py --dry-run
    python scripts/backfills/backfill_inhouse_accessory_library.py
    python scripts/backfills/backfill_inhouse_accessory_library.py --undo data/backfill_records/<file>.json

Safe to re-run: a product already linked to an accessory keeps the link, and a record that
exists is reused, never duplicated. All in one transaction; --dry-run does everything, reports
and rolls back.
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
import datetime
import json
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import accessories, accessory_parts, models, product_codes
from app.database import SessionLocal

SPEC = "Profile Dimension"          # the name the first profile record (#67) was given by hand
RECORDS = _REPO_ROOT / "data" / "backfill_records"


def sizes_of(sheet):
    raw = str((sheet.fields or {}).get("profile_dimension") or "")
    return [" ".join(s.split()) for s in raw.split("/") if s.strip()]


def profile_sizes(sheet, names):
    """({profile name: [size, ...]}, problem) for the profiles one product lists."""
    sizes = sizes_of(sheet)
    profiles = [n for n in names if "profile" in accessories.fold(n)]
    if not sizes or not profiles:
        return {}, ""
    if len(profiles) == 1:
        return {profiles[0]: sizes}, ""
    # The one arrangement the drawings settle: a triangular profile and one other, and a single
    # 16 x 16 size, which is the triangular one's.
    tri = [n for n in profiles if accessories.fold(n) == "triangularprofile"]
    rest = [n for n in profiles if n not in tri]
    corner = [s for s in sizes if accessory_parts.norm_value(s) == "16x16"]
    if len(tri) == 1 and len(rest) == 1 and len(corner) == 1 and len(sizes) > 1:
        return {tri[0]: corner, rest[0]: [s for s in sizes if s not in corner]}, ""
    return {}, "lists %s with sizes %s, and nothing says which size is whose" % (
        " and ".join(profiles), " / ".join(sizes))


def size_key(params):
    """A record's specification values alone, as norm_value writes them."""
    return frozenset(accessory_parts.norm_value(" ".join(p.get("options") or [])) for p in params or [])


def accessory_choices(db, sheet):
    """[(option, code)] - what each accessory adds to this product's code when it is picked."""
    result = product_codes.code_for_sheet(db, sheet)
    if result is None:
        return None
    seg = next((s for s in result["segments"] if s["key"] == "accessories"), None)
    return [(c["value"], c["code"] or "(%s)" % (c.get("reason") or "no code"))
            for c in (seg or {}).get("choices") or []]


def fingerprint(db):
    """What must not change: every price's figure, what it applies to and its status, and BOQ
    lines, as (count, id sum, a checksum of their content). Which product or library accessory a
    price row hangs on is left out - moving a product's own price onto the record it now links
    to is the one thing this may do to a price."""
    prices = db.query(models.PriceEntry).all()
    lines = db.query(models.BOQItem).all()
    return {
        "price_entries": (len(prices), sum(p.id for p in prices),
                          hash(tuple(sorted((p.id, str(p.price), json.dumps(p.config, sort_keys=True),
                                             p.status, p.source_file, p.source_label)
                                            for p in prices)))),
        "boq_items": (len(lines), sum(l.id for l in lines),
                      hash(tuple(sorted((l.id, json.dumps(l.config, sort_keys=True, default=str))
                                        for l in lines)))),
    }


LABEL_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*[x×*]\s*(\d+(?:\.\d+)?)", re.I)


def move_prices(db, sheet, notes):
    """A product's own price for an accessory, onto the library record it now links to - as the
    product form does when a person picks (accessory_parts.take_over_price): only when the record
    has no price yet or the same one, so a price is neither lost nor contradicted. Returns
    [(row, part, old specsheet id)].

    When the product takes several sizes of the accessory, the row's own label says which size it
    was matched to ("... PROFILE DIMENSION 17x15/7x17mm"). A row naming two sizes goes to whichever
    of them has no price yet, so the rows of several products price every size they name. A row
    whose size cannot be told, whose figure disagrees, or that applies only to some configurations
    stays on the product and is reported."""
    links = accessory_parts.links_of(db, [sheet.id]).get(sheet.id, [])
    moved = []
    rows = [r for r in db.query(models.PriceEntry).filter(
                models.PriceEntry.specsheet_id == sheet.id,
                models.PriceEntry.accessory_part_id.is_(None)).order_by(models.PriceEntry.id).all()
            if (r.config or {}).get("accessory")]
    for row in rows:
        name = row.config["accessory"]
        where = "#%d %s, %s (Rs %s, price row %d)" % (sheet.id, sheet.product_name, name, _fmt(row.price), row.id)
        if set(row.config) != {"accessory"}:
            notes.append("%s applies only when %s - left on the product." % (
                where, ", ".join("%s is %s" % (k, v) for k, v in row.config.items() if k != "accessory")))
            continue
        family = [l.part for l in links if accessories.fold(l.part.name) == accessories.fold(name)]
        if not family:
            notes.append("%s - the product links no library record of that name; left on the product." % where)
            continue
        if len(family) > 1:
            sizes = {accessory_parts.norm_value("%sx%s" % m) for m in LABEL_SIZE.findall(row.source_label or "")}
            family = [p for p in family if size_key(p.params) & sizes]
            if not family:
                notes.append("%s - its label names no size this product takes; left on the product." % where)
                continue
        prices = {p.id: accessory_parts.price_of(accessory_parts.price_rows(db, [p.id]).get(p.id, [])) for p in family}
        target = next((p for p in family if prices[p.id] is None), None) or \
            next((p for p in family if prices[p.id] == round(float(row.price), 2)), None)
        if target is None:
            notes.append("%s - %s already cost%s a different figure; left on the product." % (
                where, " and ".join(accessory_parts._describe(p) for p in family), "s" if len(family) == 1 else ""))
            continue
        old = row.specsheet_id
        row.specsheet_id = None
        row.accessory_part_id = target.id
        db.flush()
        moved.append((row, target, old))
    return moved


def _fmt(price):
    return ("%.2f" % float(price)).rstrip("0").rstrip(".")


def run(db):
    before_print = fingerprint(db)
    figures_before = {p.id: (str(p.price), p.status) for p in db.query(models.PriceEntry).all()}
    sheets = [s for s in db.query(models.Specsheet).order_by(models.Specsheet.id).all()
              if accessory_parts.is_part_brand(s.make)]
    abbrev = {}
    for row in db.query(models.FieldCode).filter(models.FieldCode.field == "accessories").all():
        if (row.code or "").strip():
            abbrev.setdefault(accessories.fold(row.option_value), set()).add(product_codes.clean_code(row.code))
    codes_before = {s.id: accessory_choices(db, s) for s in sheets}

    # 1. What each product lists, as (name, params) in its datasheet's order.
    wanted, problems = {}, []
    for s in sheets:
        # A product someone has already linked lists its library accessories by their labels
        # ("'U'-SHAPE PROFILE (35 x 44)"). Those are links, kept as they are - read as datasheet
        # names they would become records of their own.
        linked_labels = accessory_parts.labels_of(db, s.id)
        # A list entry can hold a whole printed line - live's "Recessed LED Grazer – Fixed" lists
        # ["NIL / HONEYCOMB / RECESSING BLOCK / LOOPING CABLE"] - so it is split as a printed line is.
        listed = [part for n in accessories._names_of(s)
                  for part in (accessories.split_options(n) if "/" in n else [n])
                  if accessories.is_name(part) and not accessories.is_none(part)]
        names = [n for n in dict.fromkeys(listed) if accessories.fold(n) not in linked_labels]
        sized, problem = profile_sizes(s, names)
        if problem:
            problems.append("#%d %s %s - its profiles are linked without sizes." % (s.id, s.product_name, problem))
        picks = []
        for n in names:
            if n in sized:
                picks += [(n, [{"name": SPEC, "options": [size]}]) for size in sized[n]]
            else:
                picks.append((n, []))
        if picks:
            wanted[s.id] = picks

    # 2. One record per accessory: the existing one, or one to create.
    existing = defaultdict(list)                      # (discipline, fold) -> [part]
    for p in db.query(models.AccessoryPart).all():
        if accessory_parts.is_part_brand(p.make):
            existing[(p.discipline, accessories.fold(p.name))].append(p)
    disc = {s.id: s.discipline or models.DEFAULT_DISCIPLINE for s in sheets}
    spellings = defaultdict(Counter)                  # (discipline, fold) -> {spelling: products}
    variants = defaultdict(set)                       # (discipline, fold) -> {identity}
    for sid, picks in wanted.items():
        for name, params in picks:
            fam = (disc[sid], accessories.fold(name))
            spellings[fam][name] += 1
            variants[fam].add(accessory_parts.identity(name, params))
    for fam, parts in existing.items():
        for p in parts:
            variants[fam].add(accessory_parts.identity(p.name, p.params))

    taken = {(p.discipline, (p.code or "").upper()) for ps in existing.values() for p in ps if p.code}
    # Who gets an abbreviation two names share: the name on more products, as the docstring says.
    # A tie goes to the better spelling, by the rule the catalogue picks spellings with.
    order = sorted(spellings, key=lambda f: (sum(spellings[f].values()),
                                             accessories._rank(accessories.canonical_name(spellings[f]))), reverse=True)
    plan, left_as_is, created = {}, {}, []
    for fam in order:
        family_name = (Counter(p.name for p in existing.get(fam, [])).most_common(1)[0][0]
                       if existing.get(fam) else accessories.canonical_name(spellings[fam]))
        codes = abbrev.get(fam[1], set())
        for sid, picks in wanted.items():
            if disc[sid] != fam[0]:
                continue
            for name, params in picks:
                if accessories.fold(name) != fam[1]:
                    continue
                me = accessory_parts.identity(family_name, params)
                if me in plan:
                    continue
                # A record a person made counts as this size whatever its specification is called -
                # #67 says "Profile Dimension 35mm X 35mm", #110 "Dimension 35 x 44".
                part = next((p for p in existing.get(fam, []) if size_key(p.params) == size_key(params)), None)
                if part is not None:
                    plan[me] = part
                    continue
                if len(codes) != 1:
                    code = ""
                    problems.append("%s: %s abbreviation%s in Product codes (%s) - created without a code." % (
                        family_name, "no" if not codes else "several", "" if len(codes) == 1 else "s",
                        ", ".join(sorted(codes)) or "none"))
                else:
                    code = next(iter(codes))
                    if len(variants[fam]) > 1:
                        size = " ".join(o for p_ in params for o in p_.get("options") or [])
                        code = product_codes.clean_code(code + accessory_parts.norm_value(size)) if size else ""
                        if not size:
                            problems.append("%s has sized variants and one without a size - created without a code." % family_name)
                if code and (fam[0], code.upper()) in taken:
                    holder = next((p for p in list(plan.values()) + [x for ps in existing.values() for x in ps]
                                   if p is not None and p.discipline == fam[0] and (p.code or "").upper() == code.upper()), None)
                    left_as_is[me] = (name, code, holder.name if holder is not None else "?")
                    plan[me] = None
                    continue
                params_clean = accessory_parts.params_from_specs(
                    [{"name": p_["name"], "value": " / ".join(p_["options"])} for p_ in params])
                part = models.AccessoryPart(discipline=fam[0], make="InHouse", name=family_name,
                                            code=code, params=params_clean, image="")
                db.add(part)
                db.flush()
                # The library's own rules, as the form applies them: no twin, no code twice.
                accessory_parts._check(db, part.make, part.discipline, part.name, part.params,
                                       part.code or None, exclude_id=part.id)
                if code:
                    taken.add((fam[0], code.upper()))
                plan[me] = part
                created.append(part)
                existing[fam].append(part)

    # 3. Link each product, keeping any link a person made, in its datasheet's order.
    snapshot = {"sheets": {}, "parts": [], "links": [], "prices": []}
    price_notes, moves = [], []
    linked = 0
    by_sheet = {s.id: s for s in sheets}
    # identity() folds the name, so a datasheet's spelling finds its family's record.
    candidates = {}
    for sid, picks in wanted.items():
        candidates[sid] = list(dict.fromkeys(plan[accessory_parts.identity(n, p)].id for n, p in picks
                                             if plan.get(accessory_parts.identity(n, p)) is not None))
    parts_by_id = {p.id: p for p in plan.values() if p is not None}

    # A record's price is one figure for every product that takes it. So a record that will carry
    # a price links only the products whose own price row is behind it (owner's decision, 6 Oct:
    # live had a honeycomb price on 11 NYX products only, and linking the other 31 would have
    # started quoting Rs N for theirs). The rest keep the accessory as their datasheet name,
    # unpriced, as before, until a person decides it is the same part.
    own_rows = defaultdict(list)                      # sid -> [(folded name, sizes its label names)]
    for r in db.query(models.PriceEntry).filter(models.PriceEntry.specsheet_id.in_(list(wanted)),
                                                models.PriceEntry.accessory_part_id.is_(None)).all():
        if set(r.config or {}) == {"accessory"}:
            own_rows[r.specsheet_id].append((accessories.fold(r.config["accessory"]), {
                accessory_parts.norm_value("%sx%s" % m) for m in LABEL_SIZE.findall(r.source_label or "")}))

    def backs(sid, part):
        """Whether this product's own price row is behind this record's price - as move_prices
        will decide it: any row of the name when the product takes one record of it, else a row
        whose label names this record's size."""
        key = accessories.fold(part.name)
        rows = [sizes for f, sizes in own_rows.get(sid, []) if f == key]
        family = [i for i in candidates[sid] if accessories.fold(parts_by_id[i].name) == key]
        return bool(rows) and (len(family) == 1 or any(size_key(part.params) & s for s in rows))

    priced = {pid for pid, rows in accessory_parts.price_rows(db, list(parts_by_id)).items() if rows}
    priced |= {pid for sid, ids in candidates.items() for pid in ids if backs(sid, parts_by_id[pid])}
    withheld = defaultdict(set)                       # sid -> {part id}
    for sid, ids in candidates.items():
        for pid in ids:
            if pid in priced and not backs(sid, parts_by_id[pid]):
                withheld[sid].add(pid)
    for sid, picks in wanted.items():
        sheet = by_sheet[sid]
        ids = [i for i in candidates[sid] if i not in withheld[sid]]
        have = [l.part_id for l in accessory_parts.links_of(db, [sid]).get(sid, [])]
        ids = have + [i for i in ids if i not in have]
        if ids == have:
            continue
        snapshot["sheets"][str(sid)] = {"options": sheet.options, "fields": sheet.fields, "params": sheet.params}
        previous = accessories._names_of(sheet)
        def left_out(n, params):
            part = plan.get(accessory_parts.identity(n, params))
            return part is None or part.id in withheld[sid]
        legacy = list(dict.fromkeys(n for n, params in picks if left_out(n, params)))
        if any(not left_out(n, p) for n, p in picks if n in legacy):
            problems.append("#%d %s takes some sizes of %s from the library and keeps the name for "
                            "others - check its list." % (sid, sheet.product_name, ", ".join(legacy)))
        changes = accessory_parts.set_product_accessories(db, sheet, ids)
        accessory_parts.write_accessory_list(db, sid, legacy=legacy, previous=previous)
        # The list puts library accessories first and datasheet names after, so a product that
        # keeps some names (held back above) would have its printed line reordered with nothing
        # in it changed. Same items, same printed line - as a product saved untouched keeps its sheet.
        was = snapshot["sheets"][str(sid)]
        old_line = str((was["fields"] or {}).get("accessories") or "")
        new_line = str((sheet.fields or {}).get("accessories") or "")

        def items(line):
            return sorted(accessories.fold(n) for n in accessories.split_options(line) if not accessories.is_none(n))
        if old_line and new_line != old_line and items(old_line) == items(new_line):
            sheet.fields, sheet.params = was["fields"], was["params"]
            db.flush()
        for link in accessory_parts.links_of(db, [sid]).get(sid, []):
            if link.part_id in {c["id"] for c in changes["added"]}:
                snapshot["links"].append(link.id)
        linked += 1
    # Each product's own accessory prices, onto the records it now takes - after every product is
    # linked, so a record priced from one product is seen as priced by the next.
    for sid in wanted:
        moves += move_prices(db, by_sheet[sid], price_notes)
    snapshot["prices"] = [[row.id, old] for row, _part, old in moves]
    snapshot["parts"] = [p.id for p in created]
    db.flush()

    # 4. Report.
    print("\nRecords created: %d" % len(created))
    for p in sorted(created, key=lambda p: (accessories.fold(p.name), str(p.params))):
        n = db.query(models.AccessoryLink).filter(models.AccessoryLink.part_id == p.id).count()
        print("  #%-4d %-34s %-24s code %-10s on %d product(s)" % (
            p.id, p.name, ", ".join("%s %s" % (s["name"], s["value"]) for s in accessory_parts.specs_of(p)) or "-",
            p.code or "(none)", n))
    reused = {p.id: p for p in plan.values() if p is not None and p not in created}
    for p in reused.values():
        n = db.query(models.AccessoryLink).filter(models.AccessoryLink.part_id == p.id).count()
        print("  reused #%d %s (code %s) - now on %d product(s)" % (p.id, p.name, p.code or "none", n))
    print("Products linked: %d (new links: %d)" % (linked, len(snapshot["links"])))
    print("\nProducts' own accessory prices moved onto the records: %d" % len(moves))
    for row, part, old in sorted(moves, key=lambda m: (m[1].id, m[0].id)):
        print("  price row %-5d Rs %-6s from #%d %s -> #%d %s" % (
            row.id, _fmt(row.price), old, by_sheet[old].product_name, part.id, accessory_parts._describe(part)))
    print("\nLibrary records and their price now:")
    for part in sorted({p.id: p for _r, p, _o in moves}.values(), key=lambda p: p.id):
        rows = accessory_parts.price_rows(db, [part.id]).get(part.id, [])
        print("  #%-4d %-44s Rs %s (%d row%s)" % (part.id, accessory_parts._describe(part)[:44],
              _fmt(accessory_parts.price_of(rows)) if accessory_parts.price_of(rows) is not None else "SEVERAL FIGURES",
              len(rows), "" if len(rows) == 1 else "s"))
    if price_notes:
        print("\nPrices left on their product, for a person to settle:")
        for n in price_notes:
            print("  " + n)
    held = defaultdict(list)
    for sid, pids in withheld.items():
        for pid in pids:
            held[pid].append(by_sheet[sid].product_name)
    if held:
        print("\nNot linked, so as not to give them a price they never had (kept as their datasheet name):")
        for pid, names in sorted(held.items(), key=lambda x: -len(x[1])):
            print("  %s - %d product(s): %s" % (accessory_parts._describe(parts_by_id[pid]), len(names),
                                                 ", ".join(sorted(names))))
    if left_as_is:
        print("\nLeft as their datasheet names (their abbreviation is another record's code):")
        for name, code, holder in left_as_is.values():
            users = [s.product_name for s in sheets if name in accessories._names_of(s)]
            print("  %s - %s is %s's code; on %s" % (name, code, holder, ", ".join(users)))
    if problems:
        print("\nFor a person to settle:")
        for p in dict.fromkeys(problems):
            print("  " + p)

    changed = []
    for s in sheets:
        db.refresh(s)
        after = accessory_choices(db, s)
        if after != codes_before[s.id]:
            changed.append((s, codes_before[s.id], after))
    reworded = [(s, snapshot["sheets"][str(s.id)]["fields"].get("accessories"), (s.fields or {}).get("accessories"))
                for s in sheets if str(s.id) in snapshot["sheets"]]
    reworded = [r for r in reworded if r[1] != r[2]]
    print("\nPrinted ACCESSORIES lines rewritten: %d" % len(reworded))
    for s, b, a in reworded:
        print("  #%d %s\n      before %s\n      after  %s" % (s.id, s.product_name, b, a))
    print("\nWhat picking an accessory adds to a product's code - products where it changed: %d" % len(changed))
    for s, b, a in changed:
        print("  #%d %s\n      before %s\n      after  %s" % (s.id, s.product_name, b, a))
    after_print = fingerprint(db)
    figures_after = {p.id: (str(p.price), p.status) for p in db.query(models.PriceEntry).all()}
    if figures_after != figures_before:
        raise SystemExit("Stopped: a price figure or status changed, which this backfill must never do. Rolled back.")
    for k in before_print:
        print("%s: %s" % (k, "unchanged" if before_print[k] == after_print[k] else "CHANGED %s -> %s" % (before_print[k], after_print[k])))
        if before_print[k] != after_print[k]:
            raise SystemExit("Stopped: %s changed, which this backfill must never do. Rolled back." % k)
    return snapshot


def undo(db, path):
    snap = json.loads(pathlib_path(path).read_text(encoding="utf-8"))
    for pid in snap["parts"]:
        part = db.get(models.AccessoryPart, pid)
        if part is None:
            continue
        ours = {rid for rid, _old in snap.get("prices", [])}
        theirs = [r for r in accessory_parts.price_rows(db, [pid]).get(pid, []) if r.id not in ours]
        if part.image or theirs or accessory_parts.lines_naming(db, pid):
            raise SystemExit("Stopped: #%d %s has since been given a picture, price or BOQ line - undo it by hand." % (pid, part.name))
    for rid, old in snap.get("prices", []):
        row = db.get(models.PriceEntry, rid)
        if row is not None and old is not None:
            row.specsheet_id, row.accessory_part_id = old, None
    db.flush()
    for lid in snap["links"]:
        link = db.get(models.AccessoryLink, lid)
        if link is not None:
            db.delete(link)
    db.flush()
    for sid, was in snap["sheets"].items():
        sheet = db.get(models.Specsheet, int(sid))
        if sheet is not None:
            sheet.options, sheet.fields, sheet.params = was["options"], was["fields"], was["params"]
    for pid in snap["parts"]:
        part = db.get(models.AccessoryPart, pid)
        if part is not None:
            if db.query(models.AccessoryLink).filter(models.AccessoryLink.part_id == pid).count():
                raise SystemExit("Stopped: #%d %s was picked by a product since - undo it by hand." % (pid, part.name))
            db.delete(part)
    db.flush()
    print("Undone: %d link(s), %d record(s), %d product(s) restored, %d price row(s) back on their product." % (
        len(snap["links"]), len(snap["parts"]), len(snap["sheets"]), len(snap.get("prices", []))))


def pathlib_path(path):
    p = _pathlib.Path(path)
    return p if p.is_absolute() else _REPO_ROOT / p


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="do everything, report, and roll back")
    ap.add_argument("--undo", metavar="RECORD", help="put back what one run did, from its record file")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        if args.undo:
            undo(db, args.undo)
        else:
            snapshot = run(db)
        if args.dry_run:
            db.rollback()
            print("\nDry run: nothing saved.")
            return
        if not args.undo:
            RECORDS.mkdir(parents=True, exist_ok=True)
            out = RECORDS / ("inhouse_accessory_library_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        db.commit()
        accessories.forget()
        if not args.undo:
            print("\nSaved. Undo record: %s" % out.relative_to(_REPO_ROOT))
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
