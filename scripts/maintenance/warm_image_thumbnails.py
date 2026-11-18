"""Pre-generates the resized product photographs the app serves to grids and lists.

The app generates one on first request and reuses it forever after, so this is not required
for correctness - it moves the cost off the first person to open the Library after a deploy.
Generating forty takes about a second and a half, which is a page that hangs for no reason
anyone can see.

Run after any deploy that adds or replaces images:

    python scripts/maintenance/warm_image_thumbnails.py            # every row's picture, at the width grids use
    python scripts/maintenance/warm_image_thumbnails.py --all-widths

Safe to re-run: an existing thumbnail is left alone. Safe to delete data/thumbs entirely;
everything in it is derived and is rebuilt on demand.
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
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal, THUMBS_DIR
from app import models
from app.main import _thumbnail, THUMB_WIDTHS

GRID_WIDTH = 400


def main():
    widths = THUMB_WIDTHS if "--all-widths" in sys.argv else (GRID_WIDTH,)
    db = SessionLocal()
    paths = {s.image_path for s in db.query(models.Specsheet).all() if s.image_path}
    db.close()
    paths = sorted(p for p in paths if os.path.exists(p))
    print(f"{len(paths)} distinct product photograph(s); widths {widths}")

    made = skipped = failed = 0
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        for w in widths:
            src = Path(p)
            try:
                before = THUMBS_DIR.joinpath().exists()
                dest = _thumbnail(src, w)
                # _thumbnail returns an existing file untouched, so "made" counts only the
                # ones this run actually had to build.
                if time.time() - dest.stat().st_mtime < 5:
                    made += 1
                else:
                    skipped += 1
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"   could not resize {src.name}: {type(e).__name__}")
        if i % 250 == 0:
            print(f"   {i}/{len(paths)}", flush=True)

    total = sum(f.stat().st_size for f in THUMBS_DIR.iterdir() if f.is_file())
    print(f"\ngenerated {made}, already present {skipped}, failed {failed} "
          f"in {time.time() - t0:.0f}s")
    print(f"data/thumbs now holds {total/1024/1024:.0f} MB")


if __name__ == "__main__":
    main()
