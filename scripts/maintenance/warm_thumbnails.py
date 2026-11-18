"""Pre-build the thumbnail cache, so nobody's first visit to the Library pays for it.

Thumbnails are generated on demand and cached on disk, keyed on the source file and the
recipe. That is fine once the cache is warm, but a cold one - a fresh deployment, a restored
server, or the recipe being changed, which retires every existing thumbnail at once - turns
the Library grid into several hundred image requests that each resize a 2000px photograph
before answering. On a one-core VM that is the difference between a page that appears and a
page that crawls.

    python scripts/maintenance/warm_thumbnails.py                # every width the app serves (200, 400, 800)
    python scripts/maintenance/warm_thumbnails.py --width 400    # just the grid size, the one that matters most
    python scripts/maintenance/warm_thumbnails.py --check        # report what is missing, build nothing

Safe to re-run and safe to interrupt: each thumbnail is an independent cache entry, already
built ones are skipped, and nothing here writes to the database.
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
import time
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal
from app import models
from app.main import _thumbnail, THUMB_WIDTHS, _thumb_dest


def sources(db):
    """Every distinct product picture on disk, once - two specsheets sharing a photograph
    share its thumbnail too, so building it twice would be wasted work."""
    seen = {}
    for (path,) in db.query(models.Specsheet.image_path).filter(
            models.Specsheet.image_path.isnot(None)).all():
        if not path:
            continue
        p = Path(path)
        if p.exists():
            seen.setdefault(str(p), p)
    return list(seen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, action="append",
                    help="only this width (repeatable); default is every width the app serves")
    ap.add_argument("--check", action="store_true", help="report what is missing, build nothing")
    args = ap.parse_args()
    widths = args.width or list(THUMB_WIDTHS)

    db = SessionLocal()
    srcs = sources(db)
    db.close()
    print("product pictures on disk: %d" % len(srcs))
    print("widths: %s" % ", ".join(str(w) for w in widths))

    missing = [(s, w) for w in widths for s in srcs if not _thumb_dest(s, w).exists()]
    print("thumbnails already cached: %d of %d"
          % (len(srcs) * len(widths) - len(missing), len(srcs) * len(widths)))
    if args.check:
        print("missing: %d (nothing built - --check)" % len(missing))
        return
    if not missing:
        print("nothing to do.")
        return

    print("building %d ..." % len(missing))
    built = failed = 0
    t0 = time.perf_counter()
    for i, (src, w) in enumerate(missing, 1):
        try:
            _thumbnail(src, w)
            built += 1
        except Exception as exc:
            failed += 1
            if failed <= 5:
                print("   could not resize %s: %s" % (src.name, exc))
        if i % 100 == 0 or i == len(missing):
            rate = i / max(time.perf_counter() - t0, 1e-9)
            print("   %d/%d  (%.1f/s, %.0fs elapsed)"
                  % (i, len(missing), rate, time.perf_counter() - t0))

    print()
    print("built %d, failed %d, in %.0fs" % (built, failed, time.perf_counter() - t0))


if __name__ == "__main__":
    main()
