r"""Turns the Exenia catalogue's raw index lines into the structured fields+options the
Configurator drives its dropdowns from, and reports the couplings between them.

Why this is not just string-splitting
-------------------------------------
The catalogue does not print six independent lists. It prints a small number of statements
about the luminaire, and most of them bind two properties together:

  "CRI92 2700K, 3000K, 3500K, 4000K"    a CRI grade is a distinct LED bin, and each bin is
  "CRI85 3000K"                          only produced in certain colour temperatures - 85
                                         CRI is very often 3000K only
  "Narrow Spot 11 - 8,2W - 9540cd"      one optic, the power the fixture draws with it
                                         fitted, and the resulting centre-beam intensity -
                                         not a beam list and a separate wattage list
  "TW 2700-4000K only for GW and AW"    tunable white needs two LED strings, so it is
                                         offered only on the wall-wash optics whose module
                                         has room for them

Read as flat lists, those statements invent products that do not exist: CRI85 at 4000K, a
7 optic drawing 9,2W, tunable white behind a 17 spot. So `structure()` returns the
couplings alongside the options, and build_exenia_config_rules.py turns them into
configuration rules.

One statement, more than one wattage
------------------------------------
Power is rarely a single number on these lines. "7,1/14,6W" shares one trailing W between
two orderable wattages at that length; "26+36W" is one fixture with an up-light and a
down-light; "2x13W" is one fixture with two modules; "3,3W÷11W" is a range the body is
configurable across. A plain `\d+W` scan reads only the last number in each, which dropped
the lower half of 42 shared-unit lines, the larger half of 12 composite ones, and every
"1x13W, 2x13W" line entirely (\b does not match between "1x" and "13"), leaving nine
products with no wattage at all. See `wattages_on`.

The comma there is a decimal separator, not a list: the 2023 edition of the same catalogue
prints fifteen of these values both ways - "8.2W" and "8,2W", "16.9W" and "16,9W" - so
"8,2W" is one number.

Optics without a beam angle
---------------------------
An angle only describes a symmetric (rotationally uniform) distribution. Elliptical,
grazing-washer, area-washer, deep-washer and framing (shaper) optics are asymmetric - the
catalogue names them instead of quoting a figure, e.g. "Grazing Washer 16,9W". An earlier
pass required a degree symbol and dropped all of them, which left their wattages in the
Wattage list with no optic able to reach them (DARK FIX COMPACT offered 8,2W, 16,9W and
24,4W that no selectable beam could produce). They are real optics and are kept by name.

Field shapes follow what InHouse/DuraGreen rows already use: `fields` keeps a readable
"/"-joined summary, `options` carries the selectable values.

  cri         CRI85 / CRI92 / CRI95 / CRI98      (its own field - it used to sit inside cct)
  cct         2700K / 3000K / 3500K / 4000K, plus "TW (2700K-4000K)" as ONE tunable-white
              option rather than the two endpoint temperatures it is quoted between
  beam_angle  "Very Narrow Spot 8", "Grazing Washer" - the optic, with the trailing
              "- 8,2W - 17050cd" photometrics dropped (they follow from the optic)
  wattage     every wattage quoted anywhere in the block
  ip_rating / ik_rating / lumen_package / control / accessories

Control is matched against a fixed vocabulary rather than "whatever is on the line": these
blocks also carry "Remote driver" and "Power supply Remote driver", which describe the
control gear, not a dimming protocol.

Idempotent - re-running recomputes from raw_text and rewrites the same result. Dry run by
default; pass --commit to write.
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

MAKE = "Exenia"

CRI_RE = re.compile(r"\bCRI\s?(\d{2,3}\+?)", re.I)
CCT_RE = re.compile(r"\b(\d{4})\s*K\b")
# "TW (2700K-4000K)", "TW 2700/4000K", "TW (2700K-3200K-4000K)" - the separator character
# and the per-number "K" are both inconsistent across the catalogue.
TW_RE = re.compile(r"\bTW\b\s*\(?\s*(\d{4}\s*K?(?:\s*[-–÷]\s*\d{4}\s*K?)+)\s*\)?", re.I)
NUM = r"\d{1,3}(?:[.,]\d{1,2})?"
# The catalogue writes power in a decimal comma - the 2023 edition prints the same values
# both ways ("8.2W" and "8,2W", "16.9W" and "16,9W"), so "8,2W" is one number, not two.
TERM = rf"{NUM}(?:\s*[x×]\s*{NUM})?"
# One wattage statement, which is very often NOT a single number:
#   "7,1/14,6W"   the trailing W is shared - two orderable wattages at that length
#   "26+36W"      one fixture, two LED groups (an up-light and a down-light)
#   "2x13W"       one fixture, two modules
#   "3,3W÷11W"    a range: the same body, configurable anywhere between the two
W_EXPR = re.compile(rf"(?<![\d.,])(?P<expr>{TERM}(?:\s*W?\s*[+/÷]\s*{TERM})*)\s*W\b", re.I)
# Each power group is usually quoted with its own lumen output, and on STEP 3 that output
# lands BETWEEN the two halves of one fixture: "66W - 6840Lm + 2x18W - 2 x 2000Lm" is a
# single 102W luminaire, not a 66W one and a 36W one. Removing the lumen clauses before
# reading power puts the "+" back where it belongs. (`lumen_package` is collected from the
# untouched line, so nothing is lost.)
LM_CLAUSE = re.compile(r"[-–+]?\s*(?:\d{1,3}\s*[x×]\s*)?[\d.,]+\s*Lm\b", re.I)
IP_RE = re.compile(r"\bIP(\d{2})\b")
IK_RE = re.compile(r"\bIK(\d{2})\b")
LM_RE = re.compile(r"\b(\d{2,5})\s*Lm\b", re.I)

# Real dimming/control protocols. "Remote driver" and "Power supply ..." describe the
# control GEAR and are deliberately excluded - they are not something to choose here.
CONTROLS = ["ON-OFF", "ON/OFF", "1-10V", "0-10V", "DALI-DT8", "DALI Pro", "DALI T8",
            "DALI", "Casambi", "Lumentalk", "DMX/RDM", "DMX", "Push", "Phase cut",
            "Potentiometer", "Emergency"]

ACCESSORY_HINT = re.compile(
    r"\b(Honeycomb|Snoot|Visor|Elliptical lens|Protective glass|Diffusing lens|Diffuser|"
    r"cover|Decorative ring|Clips|End cap)\b", re.I)

BEAM_WORDS = re.compile(
    r"\b(Spot|Flood|Washer|Narrow|Wide|Medium|Large|XLarge|Small|Multimetrik|sphere|"
    r"Elliptical|Grazing|Deep|Area|Diffuse|Direct|Indirect)\b", re.I)

# Asymmetric optics the catalogue names instead of giving an angle. Longest first, so
# "Low contrast Wide Flood Max" is not shortened to "Low contrast Flood Max".
NAMED_OPTICS = sorted([
    "Elliptical Narrow Spot", "Elliptical Wide Flood", "Low contrast Wide Flood Max",
    "Low contrast ell, Narrow Flood Max", "Low contrast Flood Max", "Grazing Washer",
    "Area Washer", "Deep Washer", "Shaper",
], key=len, reverse=True)

# Catalogue spellings that would read badly in a dropdown. "Low contrast ell, Narrow Flood
# Max" (DARK FIX MAX) is one optic - "ell." is the catalogue's abbreviation for elliptical -
# but the comma makes it look like two.
OPTIC_CANONICAL = {
    "Low contrast ell, Narrow Flood Max": "Low contrast Elliptical Narrow Flood Max",
}

# The abbreviations the catalogue's "only for ..." footnotes use for those optics.
OPTIC_ABBREV = {"GW": "Grazing Washer", "AW": "Area Washer", "DW": "Deep Washer",
                "ENS": "Elliptical Narrow Spot", "EWF": "Elliptical Wide Flood"}

# "<name> <angle> ..." - the angle may be followed by a dash or just a space before the
# photometrics, which are dropped.
ANGLED_OPTIC_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z \-/]*?)\s*(?P<ang>\d{1,3}(?:[.,]\d)?)\s*°")

DEG = "°"


def uniq(seq):
    return list(OrderedDict.fromkeys(x for x in seq if x))


def cct_key(v):
    """Numeric order reads naturally in a dropdown. A TW range has no single value, so it
    sorts after the fixed temperatures rather than being forced into the sequence."""
    m = re.match(r"(\d{4})K$", v)
    return (0, int(m.group(1))) if m else (1, 0)


def wattages_on(line: str) -> list:
    """Every wattage a line offers, labelled the way the catalogue names it.

    A "/" shares one trailing W across several separate wattages, so it splits into
    separate options; "+", "x" and "÷" describe a single orderable variant (two LED
    groups, two modules, one configurable range) and stay inside one label. Reading only
    the last number - which a plain `\\d+W` scan does - silently dropped the lower half of
    every one of these: SUPERLINEA at 774mm is 7,1W *or* 14,6W and only 14,6W was offered,
    TOB MAXI is 26+36W and only "36W" was, and "1x13W, 2x13W" produced nothing at all
    because \\b does not match between "1x" and "13".

    Lumen clauses are stripped first, so a "+" whose two halves are separated by one -
    STEP 3's "66W - 6840Lm + 2x18W" - still reads as the single fixture it is.
    """
    out = []
    for m in W_EXPR.finditer(LM_CLAUSE.sub(" ", line)):
        for part in m.group("expr").split("/"):
            part = re.sub(r"\s+", "", part).replace(",", ".").rstrip("W")
            if not part:
                continue
            # "÷" is a range, not two choices - one option spanning both ends.
            out.append(part.replace("÷", "-") + "W")
    return out


def watt_value(label: str) -> float:
    """Nominal power behind a label, for ordering: "88+4x6W" is 112W, "2x13W" is 26W, and
    a range sorts by its low end."""
    core = label.rstrip("W").replace("W", "").split("-")[0]
    total = 0.0
    for term in core.split("+"):
        v = 1.0
        for part in term.split("x"):
            part = part.strip()
            if part:
                v *= float(part)
        total += v
    return total


def _tw_label(raw_range: str) -> str:
    """'2700/4000K' or '2700K-3200K-4000K' -> 'TW (2700K-4000K)' / 'TW (2700K-3200K-4000K)'."""
    return "TW (" + "-".join(n + "K" for n in re.findall(r"\d{4}", raw_range)) + ")"


def _ccts_on(line: str) -> list:
    """Colour temperatures stated on one line. A tunable-white range is ONE option - the
    module is continuously adjustable between its endpoints - not the two endpoints as
    separate fixed bins, so the TW text is removed before the fixed CCTs are read."""
    out = [_tw_label(m.group(1)) for m in TW_RE.finditer(line)]
    return out + [m + "K" for m in CCT_RE.findall(TW_RE.sub(" ", line))]


def _optic_on(line: str):
    """The optic a line describes, as it should appear in the Beam dropdown, or None."""
    for name in NAMED_OPTICS:
        if re.match(r"^\s*" + re.escape(name) + r"\b", line, re.I) and re.search(r"\d", line):
            return OPTIC_CANONICAL.get(name, name)
    if DEG not in line or not BEAM_WORDS.search(line):
        return None
    m = ANGLED_OPTIC_RE.match(line)
    return f"{m.group('name').strip()} {m.group('ang')}{DEG}" if m else None


def structure(raw: str):
    """-> (fields, options, links). `links` carries the couplings the catalogue states:
        cri_cct    {"CRI85": ["3000K"], ...}          which CCTs each LED bin comes in
        beam_watt  {"Narrow Spot 11": ["8.2W"], ...}  what the fixture draws on each optic
        cct_beams  {"TW (2700K-4000K)": [...], ...}   optics a CCT is restricted to
    """
    lines = [l.strip() for l in (raw or "").split("\n") if l.strip()]
    cct, cri, beams, watts, ips, iks, lumens, controls, accessories = ([] for _ in range(9))
    cri_cct, beam_watt, cct_beams = OrderedDict(), OrderedDict(), OrderedDict()
    free_tw = []  # TW stated on its own line, i.e. not tied to one CRI grade

    for line in lines:
        found_cri = CRI_RE.findall(line)
        line_ccts = _ccts_on(line)

        if found_cri:
            cri += ["CRI" + c for c in found_cri]
            cct += line_ccts
            # Only a line naming exactly one grade tells us which CCTs belong to it;
            # "CRI 80+, CRI 90+" names two and states no temperatures at all.
            if len(found_cri) == 1 and line_ccts:
                cri_cct.setdefault("CRI" + found_cri[0], []).extend(line_ccts)
        elif line_ccts:
            cct += line_ccts
            tws = [c for c in line_ccts if c.startswith("TW")]
            free_tw += tws
            # "TW 2700-4000K only for GW and AW": tunable white needs a two-string module,
            # which only the wall-wash optics have room for.
            restricted = re.search(r"only for\s+(.+)$", line, re.I)
            if tws and restricted:
                names = [OPTIC_ABBREV[t] for t in re.findall(r"\b[A-Z]{2,3}\b", restricted.group(1))
                         if t in OPTIC_ABBREV]
                for t in tws:
                    if names:
                        cct_beams.setdefault(t, []).extend(names)

        ips += ["IP" + m for m in IP_RE.findall(line)]
        iks += ["IK" + m for m in IK_RE.findall(line)]
        lumens += [m + "Lm" for m in LM_RE.findall(line)]
        watts += wattages_on(line)

        low = line.lower()
        if "driver" not in low and "power supply" not in low:
            found = [c for c in CONTROLS
                     if re.search(r"(?<![A-Za-z0-9])" + re.escape(c) + r"(?![A-Za-z0-9])", line, re.I)]
            if found:
                # keep the most specific match only (DALI-DT8 beats DALI on the same text)
                controls += [c for c in found if not any(o != c and c in o for o in found)]

        if ACCESSORY_HINT.search(line):
            accessories += [p.strip() for p in re.split(r"[,/]", line) if p.strip()]

        optic = _optic_on(line)
        if optic:
            beams.append(optic)
            # An optic row can quote more than one wattage - SPOT 230 REVO MEDIUM's 8 optic
            # is "8,2W- 18,2W". Taking only the first offered one of the two.
            beam_watt.setdefault(optic, []).extend(wattages_on(line))

    cri = uniq(cri)
    # A standalone TW line is not tied to a CRI grade, so it stays available whichever grade
    # is chosen - without this, a CRI rule would hide the option that line just added.
    for grade in cri_cct:
        cri_cct[grade] = uniq(cri_cct[grade] + free_tw)

    options = {
        "cri": cri,
        "cct": sorted(uniq(cct), key=cct_key),
        "beam_angle": uniq(beams),
        "wattage": sorted(uniq(watts), key=watt_value),
        "ip_rating": uniq(ips),
        "control": uniq(controls),
    }
    options = {k: v for k, v in options.items() if v}

    fields = {k: " / ".join(v) for k, v in options.items()}
    for key, vals in (("ik_rating", uniq(iks)), ("lumen_package", uniq(lumens)),
                      ("accessories", uniq(accessories))):
        if vals:
            fields[key] = " / ".join(vals)

    links = {
        "cri_cct": {k: sorted(uniq(v), key=cct_key) for k, v in cri_cct.items()},
        "beam_watt": {k: uniq(v) for k, v in beam_watt.items()},
        "cct_beams": {k: uniq(v) for k, v in cct_beams.items()},
    }
    return fields, options, links


def main():
    commit = "--commit" in sys.argv
    db = SessionLocal()
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()

    changed, free_wattage_products = 0, 0
    stats = {k: 0 for k in ("cri", "cct", "beam_angle", "wattage", "ip_rating", "control")}
    for s in rows:
        fields, options, links = structure(s.raw_text or "")
        if not options and not fields:
            continue
        if fields != (s.fields or {}) or options != (s.options or {}):
            changed += 1

        # Wattages no optic can produce. Not automatically wrong: on MUSEO MINI the block
        # states "6W - 700Lm / 9W - 980Lm" against a single fixed "Wide 59" optic, i.e. the
        # power really is an independent choice of module size. It IS wrong when it means a
        # named optic was dropped, which is what this count is here to keep an eye on.
        reachable = {w for ws in links["beam_watt"].values() for w in ws}
        if options.get("beam_angle") and set(options.get("wattage") or []) - reachable:
            free_wattage_products += 1

        s.fields, s.options = fields, options
        for k in stats:
            if options.get(k):
                stats[k] += 1

    print(f"Exenia specsheets: {len(rows)}   rewritten: {changed}")
    print()
    print("products with real selectable options, per field:")
    for k, n in stats.items():
        print(f"   {k:<12} {n:>4} / {len(rows)}")
    print()
    print(f"products with a wattage no optic reaches: {free_wattage_products} "
          f"(expected where power is a module-size choice, not an optic one)")
    print()
    for s in rows[:3]:
        print(f"  {s.product_name}")
        for k, v in (s.options or {}).items():
            print(f"     {k:<11} {v}")

    if not commit:
        db.rollback()
        print("\nDRY RUN - nothing written. Re-run with --commit.")
        db.close()
        return
    db.commit()
    print(f"\nCOMMITTED. Restructured {changed} specsheet(s).")
    db.close()


if __name__ == "__main__":
    main()
