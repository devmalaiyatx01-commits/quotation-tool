"""One-time backfill: tags the 87 specsheets created by import_inhouse_specsheets.py
(2026-08-06) with Interior/Exterior, using the exact source-folder info captured in that
run's own console output - pasted verbatim below (LOG). Nothing about a general upload
can classify Interior/Exterior from folder structure (see app/environment_classifier.py's
own docstring on why this only works from PDF content in general), but for THIS specific
batch the folder path really was known and worth not throwing away.

Not run again after this one time: a fresh upload of new files has no such log to read
from, so this script has no ongoing purpose once it's been run once. Safe to re-run
anyway (only ever sets environment on rows that are still empty, never overwrites).

Matching logic: LOG's "+ [...]" lines are in the exact order the 87 rows were inserted
in (single-threaded, one flush() per row, all in one commit at the end) - so the Nth "+"
line corresponds exactly to the Nth-oldest of "the last 87 specsheets" by id. Verified,
not assumed: each pairing asserts product_name and page_number match before writing
anything, and the whole backfill aborts if any pairing doesn't line up rather than
tagging the wrong row.

Folder -> environment: any path component containing "interior" -> Interior; "exterior",
"underwater", "inground", or "outdoor" -> Exterior (broader than just a literal
top-level "Interior\"/"Exterior\" folder, since some of this library's own folders -
"Underwater Recessed Spot", "Inground", "Exterior Spot" - say Exterior just as clearly
without literally being nested under an "Exterior\" folder). Folders that say neither
("Downlight", "COB Strip", "Surface Fixed Downlight", ...) are left unclassified -
consistent with the rest of this app's "leave blank rather than guess" rule.
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

from app.database import SessionLocal
from app import models

LOG = r"""
  + [Downlight] Downlight\Curve Series.pdf p1: 'CURVE -Nano'
  + [Downlight] Downlight\Curve Series.pdf p2: 'CURVE -Nano'
  + [Downlight] Downlight\Curve Series.pdf p3: 'CURVE -Micro'
  + [Downlight] Downlight\Curve Series.pdf p4: 'CURVE -Micro'
  + [Downlight] Downlight\Curve Series.pdf p5: 'CURVE -Mini'
  + [Downlight] Downlight\Curve Series.pdf p6: 'CURVE -Mini'
  + [Downlight] Downlight\Curve Series.pdf p7: 'CURVE -Small'
  + [Downlight] Downlight\Curve Series.pdf p8: 'CURVE -Small'
  + [Downlight] Downlight\Curve Series.pdf p9: 'CURVE -Midi'
  + [Downlight] Downlight\Curve Series.pdf p10: 'CURVE -Midi'
  + [Flexible Neon / LED Band] Exterior\AntiGlare Flexible Linear\Anti Glare Linear Flexible 1010.pdf p1: 'LUMEA 1010BH TV'
  + [Flexible Neon / LED Band] Exterior\AntiGlare Flexible Linear\Anti Glare Linear Flexible 1010.pdf p2: 'LUMEA 1010WH TV'
  + [Flexible Neon / LED Band] Exterior\AntiGlare Flexible Linear\Anti Glare Linear Flexible 1010.pdf p3: 'LUMEA 1010LB TV'
  + [Flexible Neon / LED Band] Exterior\AntiGlare Flexible Linear\Anti Glare Linear Flexible 1010.pdf p4: 'LUMEA 1010LW TV'
  + [Flexible Neon / LED Band] Exterior\AntiGlare Flexible Linear\Anti Glare Linear Flexible 1010.pdf p5: 'LUMEA 1010BM TV'
  + [Flexible Strip Light] Exterior\COB Strip\Filo S 05.pdf p1: 'Filo S 05 - LL 106001'
  + [Flexible Strip Light] Exterior\COB Strip\Filo S 10.pdf p1: 'Filo S 10 - LL 106002'
  + [Spotlight] Exterior\Exterior Spot\FLARE MIDI.pdf p1: 'FLARE - Midi'
  + [Spotlight] Exterior\Exterior Spot\FLARE MINI..pdf p1: 'FLARE - Micro'
  + [Spotlight] Exterior\Exterior Spot\FLARE MINI.pdf p1: 'FLARE -Mini'
  + [Spotlight] Exterior\Exterior Spot\FLARE Nano.pdf p1: 'FLARE - Nano'
  + [Flexible Neon / LED Band] Exterior\Flexible Homogeneous\MIST 0408SV.pdf p1: 'MIST 0408SV'
  + [Flexible Neon / LED Band] Exterior\Flexible Homogeneous\MIST 1010TV.pdf p1: 'MIST 1010 TV'
  + [Flexible Neon / LED Band] Exterior\Flexible Homogeneous\MIST 1617DO.pdf p1: 'MIST 1617 DO'
  + [Unclassified] Exterior\Inground\DAWN Series.pdf p1: 'DAWN24 -Mini'
  + [Unclassified] Exterior\Inground\DAWN Series.pdf p2: 'DAWN24 -Small'
  + [Unclassified] Exterior\Inground\DAWN Series.pdf p3: 'DAWN24 -Midi'
  + [Wall Grazer / Wall Washer] Exterior\Linear Grazer Surface\GLANCE 1000.pdf p1: 'GLANCE 1000'
  + [Unclassified] Exterior\Media Linear\PULSE 1000 RGBW PIXEL.pdf p1: 'PULSE 1000 RGBW PIXEL'
  + [Wall Grazer / Wall Washer] Exterior\Media Linear\PULSE 1000 RGBW.pdf p1: 'PULSE 1000 RGBW'
  + [Wall Grazer / Wall Washer] Exterior\Media Linear\PULSE 1000 Static.pdf p1: 'PULSE 1000 Static'
  + [Flexible Strip Light] Exterior\SMD Strip with Profile\Vena S 10 - 60LEDS per M - Diifuser.pdf p1: 'Vena S 10 - LL 106001'
  + [Flexible Strip Light] Exterior\SMD Strip with Profile\Vena S 10 - 60LEDS per M - Lens.pdf p1: 'Vena S 10 - LL 106001'
  + [Flexible Strip Light] Exterior\SMD Strip with Profile\Vena S 15 - 60LEDS per M - Diifuser.pdf p1: 'Vena S 15 - LL 106007'
  + [Flexible Strip Light] Exterior\SMD Strip with Profile\Vena S 23 - 120LEDS per M - Diifuser.pdf p1: 'Vena S 10 - LL 106010'
  + [Unclassified] Exterior\Step Light\RASO-1 Step Light.pdf p1: 'RASO-1 (LL 139017)'
  + [Flexible Neon / LED Band] Exterior\Underwater Flexible Homogeneous\TIDE 1617SV.pdf p1: 'TIDE 1617 SV'
  + [Unclassified] Exterior\Underwater Recessed Spot\Aura Series.pdf p1: 'Aura-Nano'
  + [Unclassified] Exterior\Underwater Recessed Spot\Aura Series.pdf p2: 'Aura -Midi'
  + [Unclassified] Exterior\Underwater Surface Spot\Riva Midi.pdf p1: 'Riva -Midi'
  + [Downlight] Exterior\Wall Mounted\Solara Wall.pdf p1: 'SOLARA Wall'
  + [Track Light] Interior\48V Track Luminaire\RAYON 48V DC Track Linear Diffused.pdf p3: 'RAYONLinear Diffused - LL103007'
  + [Track Light] Interior\48V Track Luminaire\RAYON 48V DC Track Linear.pdf p1: 'RAYON Linear �LL103003'
  + [Track Light] Interior\48V Track Luminaire\RAYON 48V DC Track Linear.pdf p2: 'RAYON Linear �LL103004'
  + [Track Light] Interior\48V Track Luminaire\RAYON 48V DC Track Linear.pdf p3: 'RAYON Linear - LL103005'
  + [Track Light] Interior\48V Track Luminaire\RAYON 48V DC Track Spot.pdf p1: 'RAYON Spot � LL 103001'
  + [Track Light] Interior\48V Track Luminaire\RAYON 48V DC Track Spot.pdf p2: 'RAYON Spot � LL 103002'
  + [Track Light] Interior\48V Track Luminaire\RAYON 48V DC Track Spot.pdf p3: 'RAYON Spot - � LL 103003'
  + [Track Light] Interior\48V Track Luminaire\RAYON 48V DC Track Suspended.pdf p1: 'RAYON Suspended'
  + [Downlight] Interior\Downlight\Curve Series.pdf p1: 'CURVE -Nano'
  + [Downlight] Interior\Downlight\Curve Series.pdf p2: 'CURVE -Nano'
  + [Downlight] Interior\Downlight\Curve Series.pdf p3: 'CURVE -Micro'
  + [Downlight] Interior\Downlight\Curve Series.pdf p4: 'CURVE -Micro'
  + [Downlight] Interior\Downlight\Curve Series.pdf p5: 'CURVE -Mini'
  + [Downlight] Interior\Downlight\Curve Series.pdf p6: 'CURVE -Mini'
  + [Downlight] Interior\Downlight\Curve Series.pdf p7: 'CURVE -Small'
  + [Downlight] Interior\Downlight\Curve Series.pdf p8: 'CURVE -Small'
  + [Downlight] Interior\Downlight\Curve Series.pdf p9: 'CURVE -Midi'
  + [Downlight] Interior\Downlight\Curve Series.pdf p10: 'CURVE -Midi'
  + [Downlight] Interior\Downlight\Jade Series.pdf p1: 'JADE -Mini'
  + [Downlight] Interior\Downlight\Jade Series.pdf p3: 'JADE -Midi'
  + [Downlight] Interior\Downlight\Jade Series.pdf p4: 'JADE -Midi'
  + [Downlight] Interior\Downlight\Jade Series.pdf p5: 'JADE -Maxi'
  + [Downlight] Interior\Downlight\Jade Series.pdf p6: 'JADE -Maxi'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p1: 'NYX -Mini'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p2: 'NYX -Mini'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p3: 'NYX -Midi'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p4: 'NYX -Midi'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p5: 'NYX Square -Mini'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p6: 'NYX Square -Midi'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p7: 'NYX Dual Square -Mini'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p8: 'NYX Dual Square -Mini'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p9: 'NYX Dual Square -Midi'
  + [Downlight] Interior\Downlight\Nyx Series.pdf p10: 'NYX Dual Square -Midi'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p1: 'OZONE -Mini'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p2: 'OZONE -Mini'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p3: 'OZONE -Midi'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p4: 'OZONE -Midi'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p5: 'OZONE -Maxi'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p6: 'OZONE Square -Mini'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p7: 'OZONE Square -Mini'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p8: 'OZONE Square -Midi'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p9: 'OZONE Square -Midi'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p10: 'OZONE Dual Square -Mini'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p11: 'OZONE Dual Square -Mini'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p12: 'OZONE Dual Square -Midi'
  + [Downlight] Interior\Downlight\Ozone Series.pdf p13: 'OZONE Dual Square -Midi'
""".strip("\n")

LINE_RE = re.compile(r"^  \+ \[[^\]]*\] (?P<relpath>.+?) p(?P<page>\d+): '(?P<name>.*)'$")

# A few of these product names contain a PDF-mangled placeholder character (pdfplumber's
# best-effort decode of a glyph its font mapping doesn't recognize, e.g. an en-dash in
# some RAYON pages) - copy/pasting the console output back into this file's source can't
# reliably reproduce the exact original byte, so non-ASCII characters are normalized away
# on both sides before comparing. Real mismatches (wrong product, wrong page) still get
# caught - only this kind of already-garbled placeholder glyph is ignored.
def _normalize_name(name: str) -> str:
    return re.sub(r"[^\x00-\x7f]", "?", name)

EXTERIOR_KEYWORDS = ("exterior", "underwater", "inground", "outdoor")
INTERIOR_KEYWORDS = ("interior",)


def environment_for_path(relpath: str) -> list[str]:
    text = relpath.lower()
    result = []
    if any(kw in text for kw in INTERIOR_KEYWORDS):
        result.append("Interior")
    if any(kw in text for kw in EXTERIOR_KEYWORDS):
        result.append("Exterior")
    return result


def main():
    entries = []
    for line in LOG.splitlines():
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if not m:
            raise SystemExit(f"Couldn't parse log line: {line!r}")
        entries.append((m.group("relpath"), int(m.group("page")), m.group("name")))

    db = SessionLocal()
    candidates = (
        db.query(models.Specsheet)
        .order_by(models.Specsheet.id.desc())
        .limit(len(entries))
        .all()
    )
    candidates.reverse()  # ascending id = insertion order, matching the log's order

    if len(candidates) != len(entries):
        raise SystemExit(f"Expected {len(entries)} rows to match against, found {len(candidates)} - aborting without writing anything.")

    tagged_interior = tagged_exterior = tagged_both = left_blank = 0
    for sheet, (relpath, page, name) in zip(candidates, entries):
        if _normalize_name(sheet.product_name or "") != _normalize_name(name) or (sheet.page_number or 1) != page:
            raise SystemExit(
                f"Mismatch at specsheet #{sheet.id}: DB has {sheet.product_name!r} p{sheet.page_number}, "
                f"log expected {name!r} p{page} ({relpath}) - aborting without writing anything."
            )
        if sheet.environment:
            continue  # already tagged (e.g. this script already ran once) - don't overwrite
        env = environment_for_path(relpath)
        if env == ["Interior"]:
            tagged_interior += 1
        elif env == ["Exterior"]:
            tagged_exterior += 1
        elif len(env) == 2:
            tagged_both += 1
        else:
            left_blank += 1
        if env:
            sheet.environment = env

    db.commit()
    print(f"Tagged Interior: {tagged_interior}, Exterior: {tagged_exterior}, Both: {tagged_both}, left blank: {left_blank}")


if __name__ == "__main__":
    main()
