"""Checks every specsheet's parsed options against what each field is supposed to hold.

A field name is a promise about its contents. "CCT" means a colour temperature; if it
holds "CRI92 3000K" then the Configurator labels a colour-rendering grade as a colour
temperature, the BOQ's CCT column records one, and the exported quotation states one. The
same mistake in the other direction puts a wattage inside a beam angle ("Narrow Spot 11 -
8,2W").

Both come from the same well-meant shortcut: a manufacturer sells two coupled properties
as one line item, so the importer stored the line as one option. That is the right data
and the wrong shape - this app already models a coupling properly, as two fields plus a
ConfigRule that narrows the second when the first is chosen (see app/config_rules.py).

This reports, per make, every option value that fails its field's test, so the fixes can
be targeted rather than guessed at. Read-only; it never writes.
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
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models

CRI_TOKEN = re.compile(r"\bCRI\s?\d{2,3}", re.I)
CCT_TOKEN = re.compile(r"\b\d{4}\s*K\b", re.I)
WATT_TOKEN = re.compile(r"\b\d{1,3}(?:[.,]\d)?\s*W\b", re.I)
LUMEN_TOKEN = re.compile(r"\b\d{2,6}\s*lm\b", re.I)
IP_TOKEN = re.compile(r"\bIP\s?\d{2}\b", re.I)
DEG_TOKEN = re.compile(r"\d{1,3}\s*°")


def bad_cct(v):
    """A CCT is a temperature, a tunable range, or a named colour mode - never a CRI."""
    return "carries a CRI grade" if CRI_TOKEN.search(v) else ""


def bad_cri(v):
    """A CRI grade is a number, optionally qualified. It is not a temperature."""
    return "carries a colour temperature" if CCT_TOKEN.search(v) else ""


def bad_beam(v):
    """A beam is an angle or a named distribution. The power drawn with that optic fitted
    is a different property, and belongs in wattage."""
    if WATT_TOKEN.search(v):
        return "carries a wattage"
    if LUMEN_TOKEN.search(v):
        return "carries a lumen output"
    return ""


def bad_wattage(v):
    return "carries a colour temperature" if CCT_TOKEN.search(v) else ""


def bad_ip(v):
    return "" if IP_TOKEN.search(v) or not v.strip() else "is not an IP rating"


# Some makes sell output as a named tier rather than a figure - Lumenpulse's Element family
# is ordered as Soft/Regular/High Output. That is a real, orderable option, not a parse
# failure, so it passes.
OUTPUT_TIER = re.compile(r"\b(soft|regular|standard|high|max(?:imum)?|low)\s+output\b", re.I)


def bad_lumen(v):
    if not v.strip() or LUMEN_TOKEN.search(v) or OUTPUT_TIER.search(v):
        return ""
    return "is neither a lumen figure nor a named output tier"


CHECKS = {
    "cct": bad_cct,
    "cri": bad_cri,
    "beam_angle": bad_beam,
    "wattage": bad_wattage,
    "ip_rating": bad_ip,
    "lumen_output": bad_lumen,
}


def main():
    db = SessionLocal()
    rows = db.query(models.Specsheet).all()

    by_make = defaultdict(lambda: defaultdict(Counter))
    examples = defaultdict(list)
    affected = defaultdict(set)
    for s in rows:
        for field, check in CHECKS.items():
            for v in (s.options or {}).get(field) or []:
                why = check(v)
                if why:
                    by_make[s.make][field][why] += 1
                    affected[(s.make, field)].add(s.id)
                    if len(examples[(s.make, field, why)]) < 3:
                        examples[(s.make, field, why)].append((s.product_name, v))

    print(f"specsheets checked: {len(rows)}")
    print()
    if not by_make:
        print("every parsed option matches the field it is stored in.")
        db.close()
        return

    for make in sorted(by_make):
        print(f"=== {make}")
        for field in sorted(by_make[make]):
            for why, n in by_make[make][field].most_common():
                sheets = len(affected[(make, field)])
                print(f"    {field:<12} {why:<28} {n:>4} value(s) across {sheets} product(s)")
                for name, v in examples[(make, field, why)]:
                    print(f"        {name[:34]:<36} {v[:58]!r}")
        print()
    db.close()


if __name__ == "__main__":
    main()
