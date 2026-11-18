"""Splits option values that bundle two properties into the fields those properties belong
in, and records the coupling as a rule instead of as a string.

The problem
-----------
Manufacturers sell coupled properties as one line item, because the coupling is real:

    Exenia      "CRI92 3000K"    a 92 CRI part and a 98 CRI part are different LED dies,
                                 binned into different colour temperatures - you order the
                                 pair, not a CRI and then a temperature
    Lumenpulse  "2200K to 3000K, CRI 80, Multi-Channel Control"

Storing the line verbatim under `cct` is the wrong shape for it. A field name is a promise
about its contents: the Configurator labels that value "CCT", the BOQ's CCT column records
it, and the exported quotation prints a colour-rendering grade where a colour temperature
belongs. audit_field_values.py is what found it.

The fix
-------
The coupling is kept - it just moves from inside a string to where this app already models
couplings, a ConfigRule that narrows one field when another is chosen (app/config_rules.py,
and ConfigRule.specsheet_id for a rule true of one product only). So:

    cct  ["CRI92 2700K", ..., "CRI98 4000K"]   ->  cri ["CRI92", "CRI98"]
                                                   cct ["2700K", "3000K", "3500K", "4000K"]
                                                   + rule: cri=CRI92 -> show only its own CCTs

Nothing becomes orderable that was not orderable before: the rule reproduces exactly the
pairs the manufacturer published, and a grade offered in every temperature produces no rule
because it constrains nothing.

Lumenpulse's dynamic-white lines keep their colour-mode text - the tuning range and the
channel architecture ARE one choice, and "Dim to Warm, 1 Channel Control" is a colour
behaviour, not a dimming protocol to be crossed with the others. Only the CRI, which is a
genuinely separate property and constant across all four, is lifted out. Their
"1500lmAmber" is a spacing fault in the source page, not a coupling; it is repaired in
place.

Rule ownership: this script owns the per-product rules for Exenia rows that came from the
website (filename ends .html). build_exenia_config_rules.py owns the catalogue rows' rules
and skips these, so neither erases the other.

Idempotent - re-running on already-split data changes nothing. Dry run by default; pass
--commit to write.
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
import re
import sys
from collections import OrderedDict

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models

# "CRI92 3000K", "CRI 85 2700K", "CRI98 TW (2700K-4000K)" - a grade, then the colour it is
# offered in, whatever shape that colour takes.
CRI_PREFIX = re.compile(r"^CRI\s?(?P<cri>\d{2,3})\s+(?P<rest>.+)$", re.I)
# "..., CRI 80, ..." inside a longer colour-mode description.
CRI_CLAUSE = re.compile(r",\s*CRI\s?(\d{2,3})\s*(?=,|$)", re.I)
# "1500lmAmber" - the lumen figure and the colour ran together in the source markup.
GLUED_LUMENS = re.compile(r"(?<=\d)(lm)(?=[A-Z])", re.I)


def uniq(seq):
    return list(OrderedDict.fromkeys(x for x in seq if x))


def normalise_cct(v: str) -> str:
    """Exenia writes kelvin both ways ("3000K" and "3000k"). One product's dropdown listing
    both reads as two different temperatures."""
    return re.sub(r"(\d{4})\s*k\b", lambda m: m.group(1) + "K", v.strip())


def cct_sort_key(v: str):
    """Plain temperatures in numeric order; a tunable range or a named colour mode has no
    single number to sort by, so it follows them."""
    m = re.match(r"^(\d{4})K$", v)
    return (0, int(m.group(1))) if m else (1, v.lower())


def split_exenia_colour(cct_options):
    """-> (cri_options, cct_options, {cri: [cct, ...]}) or None if no value states a grade.

    Split per value, not all-or-nothing: one product's step mixes "CRI92 3000K" with a bare
    "2700K" and with "CRI98 TW (2700K-4000K)". A value with no grade on it is a colour
    available whatever grade is chosen, so it stays in the list and is allowed under every
    grade - the same reasoning the catalogue's standalone TW line gets.
    """
    if not cct_options or not any(CRI_PREFIX.match(v) for v in cct_options):
        return None
    pairs, cris, ccts, ungraded = OrderedDict(), [], [], []
    for v in cct_options:
        m = CRI_PREFIX.match(v)
        if not m:
            colour = normalise_cct(v)
            ccts.append(colour)
            ungraded.append(colour)
            continue
        cri = "CRI" + m.group("cri")
        colour = normalise_cct(m.group("rest"))
        cris.append(cri)
        ccts.append(colour)
        pairs.setdefault(cri, []).append(colour)
    return (uniq(cris), sorted(uniq(ccts), key=cct_sort_key),
            {k: sorted(uniq(v + ungraded), key=cct_sort_key) for k, v in pairs.items()})


PLAIN_KELVIN = re.compile(r"^\d{4}\s*K$", re.I)


def sort_colour_temperatures(db, commit):
    """A colour-temperature dropdown that runs 3000K, 2700K, 4000K reads as a mistake. Only
    lists that are entirely plain temperatures are sorted - anything holding a tunable range
    or a named colour mode keeps the order the manufacturer published it in, since there is
    no single number to sort those by."""
    fixed = 0
    for s in db.query(models.Specsheet).all():
        ccts = (s.options or {}).get("cct") or []
        if len(ccts) < 2 or not all(PLAIN_KELVIN.match(v) for v in ccts):
            continue
        ordered = sorted(ccts, key=lambda v: int(re.sub(r"\D", "", v)))
        if ordered == ccts:
            continue
        fixed += 1
        if commit:
            opts, fields = dict(s.options), dict(s.fields or {})
            opts["cct"] = ordered
            fields["cct"] = " / ".join(ordered)
            s.options, s.fields = opts, fields
    print(f"Colour temperatures listed out of order: {fixed} product(s)")
    return fixed


# The configurator steps that couple two properties, as Exenia names them.
COUPLED_STEPS = ("CRI-CCT", "CRI and CCT")
OPTIC_STEPS = ("Optic", "Optics", "Down optic", "Up optic")

# "Narrow Spot 11 - 8,2W", "Elliptical Narrow Spot 18,2W" - the optic and the power the
# fixture draws with it fitted. The trailing wattage is what makes it a pair.
OPTIC_WATTAGE = re.compile(r"^(?P<optic>.+?)\s*[-–]?\s*"
                           r"(?P<w>\d{1,3}(?:[.,]\d)?)\s*W$", re.I)


def coupled_pairs(s):
    """The pairs the manufacturer published, read from `params` - which keeps Exenia's
    configurator verbatim. That matters: once the pair is split out of `cct` the pairing is
    gone from `options`, so a second run would have nothing to rebuild the rules from and
    would leave the products unconstrained. Falls back to `cct` for a row that has not been
    split yet and has no params."""
    cfg = (s.params or {}).get("Configurator (Exenia)") or {}
    for name in COUPLED_STEPS:
        if cfg.get(name):
            return [v.strip() for v in cfg[name].split("/") if v.strip()]
    return list((s.options or {}).get("cct") or [])


def split_exenia_optic(optic_options):
    """-> (beam_options, wattage_options, {beam: [wattage]}) or None if nothing to split.

    An optic and the power drawn through it are one line on Exenia's configurator because
    they are one order: the lens and the LED module are chosen together. They are still two
    properties, and a Beam field holding "Narrow Spot 11 - 8,2W" is a beam angle field
    holding a wattage."""
    if not any(OPTIC_WATTAGE.match(v) for v in optic_options or []):
        return None
    pairs, beams, watts = OrderedDict(), [], []
    for v in optic_options:
        m = OPTIC_WATTAGE.match(v)
        if not m:
            # An optic quoted without a power - real, and left unconstrained rather than
            # guessed at.
            beams.append(v.strip())
            continue
        beam = m.group("optic").strip().rstrip("-").strip()
        watt = m.group("w").replace(",", ".") + "W"
        beams.append(beam)
        watts.append(watt)
        pairs.setdefault(beam, []).append(watt)
    return (uniq(beams), sorted(uniq(watts), key=lambda w: float(w[:-1])),
            {k: uniq(v) for k, v in pairs.items()})


def optic_values(s):
    """The optic step as the manufacturer published it, from `params` - the same durable
    source coupled_pairs() uses, so the split survives and the rules rebuild every run."""
    cfg = (s.params or {}).get("Configurator (Exenia)") or {}
    for name in OPTIC_STEPS:
        for key, text in cfg.items():
            if key.lower() == name.lower() and text:
                return [v.strip() for v in text.split("/") if v.strip()]
    return list((s.options or {}).get("beam_angle") or [])


def fix_exenia(db, commit):
    # Only the rows imported from Exenia's website. The catalogue rows already hold a split
    # cri and cct - restructure_exenia_fields.py separated them when it parsed the catalogue -
    # and their couplings are build_exenia_config_rules.py's to own. They carry the website's
    # configurator in `params` too (import_exenia_website.py records it for every matched
    # row), so without this they would be rewritten from a source that is not theirs and
    # their 497 rules would end up competing with rules written here.
    rows = [s for s in db.query(models.Specsheet).filter(models.Specsheet.make == "Exenia").all()
            if (s.filename or "").endswith(".html")]
    changed, ruled, skipped_uniform = 0, 0, 0
    planned_rules, optic_rules = [], []
    for s in rows:
        split = split_exenia_colour(coupled_pairs(s))
        if not split:
            continue
        cris, ccts, pairs = split
        opts = dict(s.options or {})
        fields = dict(s.fields or {})
        if opts.get("cri") != cris or opts.get("cct") != ccts:
            changed += 1
        opts["cri"], opts["cct"] = cris, ccts
        fields["cri"], fields["cct"] = " / ".join(cris), " / ".join(ccts)

        # The optic step couples a beam with the power drawn through it, the same way.
        osplit = split_exenia_optic(optic_values(s))
        if osplit:
            beams, watts, beam_watt = osplit
            if opts.get("beam_angle") != beams or opts.get("wattage") != watts:
                changed += 1
            opts["beam_angle"], opts["wattage"] = beams, watts
            fields["beam_angle"] = " / ".join(beams)
            fields["wattage"] = " / ".join(watts)
            if len(watts) > 1:
                for beam, allowed in beam_watt.items():
                    optic_rules.append((s, beam, allowed))
        # A grade offered in every temperature constrains nothing, so it needs no rule.
        if len(cris) > 1 and any(set(v) != set(ccts) for v in pairs.values()):
            for cri, allowed in pairs.items():
                planned_rules.append((s, cri, allowed))
                ruled += 1
        else:
            skipped_uniform += 1
        if commit:
            s.options, s.fields = opts, fields

    print(f"Exenia: {changed} field list(s) still bundling two properties")
    print(f"        {ruled} CRI->CCT rule(s); {skipped_uniform} product(s) need none "
          f"(every grade in every temperature)")
    print(f"        {len(optic_rules)} beam->wattage rule(s)")
    for s, cri, allowed in planned_rules[:6]:
        print(f"          {s.product_name[:30]:<32} {cri} -> {allowed}")

    if commit:
        ids = [s.id for s in rows]
        removed = (db.query(models.ConfigRule)
                   .filter(models.ConfigRule.specsheet_id.in_(ids))
                   .delete(synchronize_session=False)) if ids else 0
        for s, cri, allowed in planned_rules:
            db.add(models.ConfigRule(
                typology=s.typology, specsheet_id=s.id,
                triggers=[{"field": "cri", "values": [cri]}],
                target_fields=["cct"], action="Show Only", affected_options=allowed))
        for s, beam, allowed in optic_rules:
            db.add(models.ConfigRule(
                typology=s.typology, specsheet_id=s.id,
                triggers=[{"field": "beam_angle", "values": [beam]}],
                target_fields=["wattage"], action="Show Only", affected_options=allowed))
        print(f"        replaced {removed} old website-row rule(s)")
    return changed


def fix_lumenpulse(db, commit):
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == "Lumenpulse").all()
    colour_fixed = lumens_fixed = 0
    for s in rows:
        opts = dict(s.options or {})
        fields = dict(s.fields or {})
        touched = False

        ccts = opts.get("cct") or []
        grades = uniq("CRI " + m for v in ccts for m in CRI_CLAUSE.findall(v))
        if grades:
            opts["cct"] = uniq(CRI_CLAUSE.sub("", v).strip() for v in ccts)
            fields["cct"] = " / ".join(opts["cct"])
            existing = opts.get("cri") or []
            opts["cri"] = uniq(existing + [g.strip() for g in grades])
            fields["cri"] = " / ".join(opts["cri"])
            colour_fixed += 1
            touched = True

        lm = opts.get("lumen_output") or []
        repaired = [GLUED_LUMENS.sub(r"\1 ", v) for v in lm]
        if repaired != lm:
            opts["lumen_output"] = repaired
            fields["lumen_output"] = " / ".join(repaired)
            lumens_fixed += 1
            touched = True

        if touched and commit:
            s.options, s.fields = opts, fields

    print(f"Lumenpulse: {colour_fixed} product(s) whose colour option carried a CRI grade")
    print(f"            {lumens_fixed} product(s) whose lumen figure ran into the colour name")
    return colour_fixed + lumens_fixed


def main():
    commit = "--commit" in sys.argv
    db = SessionLocal()
    total = (fix_exenia(db, commit) + fix_lumenpulse(db, commit)
             + sort_colour_temperatures(db, commit))
    if not commit:
        db.rollback()
        db.close()
        print(f"\nDRY RUN - {total} product(s) would change. Re-run with --commit.")
        return
    db.commit()
    db.close()
    print(f"\nCOMMITTED. {total} product(s) restructured.")


if __name__ == "__main__":
    main()
