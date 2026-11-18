"""How much memory one app worker needs for the heavy things people do, measured, not guessed.

    python scripts/maintenance/memory_profile.py                 # every scenario, vs the baseline
    python scripts/maintenance/memory_profile.py --only export_pdf,boq_open
    python scripts/maintenance/memory_profile.py --record        # save today's numbers as the baseline

Why: the live VM has 954 MB of RAM for Postgres, Caddy and TWO app workers. Each worker grows to
the largest thing it has ever done and keeps that memory (Python rarely hands it back), so the VM
needs roughly 2 x the worst scenario below, all the time. On 2026-10-26 the app used 282 MB with
nothing in swap - and swap use had been behind the 502s before that. A change that makes the
worst scenario 60 MB bigger costs about 120 MB on the server, which it does not have.

How it measures: each scenario runs in a FRESH Python process, so one scenario's leftovers never
count against the next. The process imports the app (that alone is the floor every worker pays),
then runs the scenario while a thread samples the process's resident memory every 10 ms. Reported:

    after import   resident memory once the app is imported - the per-worker floor
    peak           the most it held during the scenario
    retained       what it still holds afterwards - what a worker carries from then on

What it touches: reads the local database. Exports are written to a temporary folder (never
data/exports) by calling the export functions directly, so no boq_exports row is written and
nothing is committed. Screens that go through the real app (the routes, the permission guard)
sign in as a temporary admin created here; that account and any activity rows it caused are
deleted by id at the end and the leftovers are printed - they must be zero.

Numbers are this machine's (Windows working set locally, Linux RSS on a server) - compare a run
with a baseline from the same machine and the same data, which is what this script does.
Pictures are found through the path stored on each specsheet, so a worktree with an empty data/
still exports real pictures. The script counts how many of the BOQ's products have their picture
on disk and says so when too few do, instead of reporting a misleadingly small number.
"""
# --- repo-root shim (see scripts/README.md) ------------------------------------------
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
import os
import platform
import subprocess
import tempfile
import threading
import time

BASELINE = _REPO_ROOT / "docs" / "quality" / "memory_baseline.json"
# A scenario fails when its peak grows by more than this, compared with the baseline. The
# floor of 15 MB is above the run-to-run noise measured on 2026-11-02 (same scenario, same data).
TOLERANCE_MB, TOLERANCE_PCT = 15.0, 10.0

# name -> what it stands for. "http" scenarios go through the app with a signed-in session;
# the others call the code a route calls, without the route's database write.
SCENARIOS = {
    "import":         ("floor",  "import the app - what every worker holds before its first request"),
    "boq_list":       ("http",   "GET /api/boqs - the BOQ list"),
    "boq_open":       ("http",   "GET /api/boqs/{largest} - open the largest detailed BOQ"),
    "library":        ("http",   "GET /api/specsheets - the Library"),
    "npd_products":   ("http",   "GET /api/npd/products - the NPD list"),
    "export_xlsx":    ("direct", "BOQ .xlsx export of the largest BOQ (excel_export.export_boq)"),
    "export_pdf":     ("direct", "BOQ .pdf export of the largest BOQ (pdf_export.export_boq_pdf)"),
    "quotation_xlsx": ("direct", "Quotation .xlsx of the largest BOQ (quotation_export.export_quotation)"),
    "ask_index":      ("direct", "the Ask tab's index of every product and BOQ (assistant.index_for)"),
}


# ---------------------------------------------------------------- reading this process's memory

def rss_mb():
    """Resident memory of this process right now, in MB."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return pmc.WorkingSetSize / 1048576
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    raise OSError("no VmRSS in /proc/self/status")


class PeakSampler(threading.Thread):
    """Samples rss_mb() every 10 ms until stopped; .peak is the largest value seen."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak = rss_mb()
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(0.01):
            self.peak = max(self.peak, rss_mb())

    def stop(self):
        self._stop.set()
        self.join()
        self.peak = max(self.peak, rss_mb())
        return self.peak


# ---------------------------------------------------------------- one scenario, in a child process

def run_child(name, boq_id, user_id, out_dir):
    import gc
    start = rss_mb()
    from app import main, models                      # noqa: F401  (the import IS the floor)
    from app.database import SessionLocal
    gc.collect()
    after_import = rss_mb()
    kind = SCENARIOS[name][0]
    note, t0 = "", time.perf_counter()
    sampler = PeakSampler()
    sampler.start()
    try:
        if kind == "http":
            import base64
            from itsdangerous import TimestampSigner
            from starlette.testclient import TestClient
            cookie = TimestampSigner(str(main.SESSION_SECRET)).sign(
                base64.b64encode(json.dumps({"user_id": user_id}).encode())).decode()
            client = TestClient(main.app, cookies={"session": cookie})
            path = {"boq_list": "/api/boqs", "boq_open": "/api/boqs/%d" % boq_id,
                    "library": "/api/specsheets", "npd_products": "/api/npd/products"}[name]
            r = client.get(path)
            note = "HTTP %d, %d KB" % (r.status_code, len(r.content) // 1024)
            if r.status_code != 200:
                raise RuntimeError("%s answered %d: %s" % (path, r.status_code, r.text[:200]))
        elif kind == "direct":
            db = SessionLocal()
            try:
                if name == "ask_index":
                    from app import assistant
                    boq = db.get(models.BOQ, boq_id)
                    index = assistant.index_for(db, boq.discipline or models.DEFAULT_DISCIPLINE)
                    note = "%d records" % len(index.records)
                else:
                    boq = db.get(models.BOQ, boq_id)
                    out = _pathlib.Path(out_dir) / ("%s_%s" % (name, boq_id))
                    if name == "export_xlsx":
                        from app.excel_export import export_boq
                        export_boq(boq, out.with_suffix(".xlsx"))
                        out = out.with_suffix(".xlsx")
                    elif name == "export_pdf":
                        from app.pdf_export import export_boq_pdf
                        export_boq_pdf(boq, out.with_suffix(".pdf"))
                        out = out.with_suffix(".pdf")
                    elif name == "quotation_xlsx":
                        from app.quotation_export import export_quotation
                        export_quotation(boq, out.with_suffix(".xlsx"))
                        out = out.with_suffix(".xlsx")
                    note = "%d KB file" % (out.stat().st_size // 1024)
            finally:
                db.rollback()            # exports may fill S.No in memory; never saved (as the routes do)
                db.close()
    finally:
        peak = sampler.stop()
    seconds = time.perf_counter() - t0
    gc.collect()
    end = rss_mb()
    return {"scenario": name, "python_start_mb": round(start, 1), "after_import_mb": round(after_import, 1),
            "peak_mb": round(peak, 1), "retained_mb": round(end - after_import, 1),
            "seconds": round(seconds, 2), "note": note}


# ---------------------------------------------------------------- the parent: setup, runs, cleanup

def _largest_detailed_boq(db, models):
    from sqlalchemy import func
    row = (db.query(models.BOQFloor.boq_id, func.count(models.BOQItem.id))
           .join(models.BOQRoom, models.BOQRoom.floor_id == models.BOQFloor.id)
           .join(models.BOQItem, models.BOQItem.room_id == models.BOQRoom.id)
           .join(models.BOQ, models.BOQ.id == models.BOQFloor.boq_id)
           .filter(func.coalesce(models.BOQ.mode, "detailed") != "quick")
           .group_by(models.BOQFloor.boq_id).order_by(func.count(models.BOQItem.id).desc()).first())
    if row is None:
        raise SystemExit("No detailed BOQ with items in this database - nothing to export.")
    return row[0], row[1]


def _item_count(db, models, boq_id):
    from sqlalchemy import func
    return (db.query(func.count(models.BOQItem.id))
            .join(models.BOQRoom, models.BOQRoom.id == models.BOQItem.room_id)
            .join(models.BOQFloor, models.BOQFloor.id == models.BOQRoom.floor_id)
            .filter(models.BOQFloor.boq_id == boq_id).scalar())


def _pictures_found(db, models, boq_id):
    """(products of this BOQ whose picture file exists, products of this BOQ). Pictures are found
    through the path stored on the specsheet, which may point outside this checkout."""
    # The ids first: Postgres cannot DISTINCT over the JSON columns of a whole specsheet row.
    ids = (db.query(models.BOQItem.specsheet_id)
           .join(models.BOQRoom, models.BOQRoom.id == models.BOQItem.room_id)
           .join(models.BOQFloor, models.BOQFloor.id == models.BOQRoom.floor_id)
           .filter(models.BOQFloor.boq_id == boq_id, models.BOQItem.specsheet_id.isnot(None))
           .distinct().subquery())
    sheets = (db.query(models.Specsheet.image_path, models.Specsheet.diagrams)
              .filter(models.Specsheet.id.in_(db.query(ids.c.specsheet_id))).all())
    found = 0
    for image_path, diagrams in sheets:
        paths = [image_path] + [d.get("path") for d in (diagrams or []) if isinstance(d, dict)]
        found += any(p and _pathlib.Path(p).is_file() for p in paths)
    return found, len(sheets)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated scenarios (default: all): " + ", ".join(SCENARIOS))
    ap.add_argument("--boq", type=int, help="BOQ id to use (default: the largest detailed BOQ)")
    ap.add_argument("--record", action="store_true", help="save this run as the baseline")
    ap.add_argument("--child", help=argparse.SUPPRESS)
    ap.add_argument("--uid", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.child:
        print(json.dumps(run_child(args.child, args.boq, args.uid, args.out)))
        return 0

    names = [n.strip() for n in (args.only or ",".join(SCENARIOS)).split(",") if n.strip()]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print("Unknown scenario(s): %s. Known: %s" % (", ".join(unknown), ", ".join(SCENARIOS)))
        return 2

    from app import models
    from app.database import SessionLocal
    from sqlalchemy import func

    db = SessionLocal()
    try:
        boq_id, items = (args.boq, None) if args.boq else _largest_detailed_boq(db, models)
        items = items if items is not None else _item_count(db, models, boq_id)
        found, products = _pictures_found(db, models, boq_id)
        user_floor = db.query(func.max(models.User.id)).scalar() or 0
        log_floor = db.query(func.max(models.ActivityLog.id)).scalar() or 0
        stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        user = models.User(email="claude-memprofile-%s@example.com" % stamp, password_hash="!",
                           role="admin", disciplines=list(models.DISCIPLINES))
        db.add(user)
        db.commit()
        uid = user.id
    finally:
        db.close()
    # Most of a product's pictures on disk = a realistic export. Recorded as a count, so a baseline
    # taken with pictures is never compared with a run that has none.
    pictures = found if products and found * 2 >= products else 0

    results, failed = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="memprofile_") as out_dir:
            for name in names:
                cmd = [_sys.executable, str(_pathlib.Path(__file__).resolve()), "--child", name,
                       "--boq", str(boq_id), "--uid", str(uid), "--out", out_dir]
                p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
                last = (p.stdout.strip().splitlines() or [""])[-1]
                if p.returncode or not last.startswith("{"):
                    failed.append(name)
                    tail = (p.stderr.strip().splitlines() or ["(no output)"])[-1]
                    print("FAILED  %-15s %s" % (name, tail[:200]))
                    continue
                results.append(json.loads(last))
    finally:
        db = SessionLocal()
        try:
            gone_logs = (db.query(models.ActivityLog)
                         .filter(models.ActivityLog.id > log_floor, models.ActivityLog.user_id == uid)
                         .delete(synchronize_session=False))
            db.query(models.User).filter(models.User.id == uid, models.User.id > user_floor).delete(
                synchronize_session=False)
            db.commit()
            left = (db.query(models.User).filter(models.User.id == uid).count() +
                    db.query(models.ActivityLog).filter(models.ActivityLog.user_id == uid).count())
        finally:
            db.close()
        print("Cleanup: temporary user #%d and %d activity row(s) deleted; left behind: %d" % (uid, gone_logs, left))

    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else None
    same = bool(baseline and baseline.get("machine") == platform.node() and baseline.get("boq_id") == boq_id
                and baseline.get("boq_items") == items and baseline.get("pictures") == pictures)
    print("\nBOQ #%d (%d items), pictures on disk for %d of its %d products%s, %s" % (
        boq_id, items, found, products, "" if pictures else " - TOO FEW, export numbers read low",
        platform.node()))
    print("%-15s %9s %8s %9s %8s  %s" % ("scenario", "import MB", "peak MB", "retained", "seconds",
                                          "vs baseline" if same else ""))
    worse = []
    for r in results:
        verdict = ""
        if same and r["scenario"] in baseline["scenarios"]:
            was = baseline["scenarios"][r["scenario"]]["peak_mb"]
            grew = r["peak_mb"] - was
            verdict = "%+.1f MB" % grew
            if grew > max(TOLERANCE_MB, was * TOLERANCE_PCT / 100):
                verdict += "  OVER BUDGET"
                worse.append(r["scenario"])
        print("%-15s %9.1f %8.1f %+9.1f %8.2f  %s  %s" % (r["scenario"], r["after_import_mb"], r["peak_mb"],
                                                         r["retained_mb"], r["seconds"], verdict, r["note"]))
    if results:
        top = max(results, key=lambda r: r["peak_mb"])
        print("\nWorst scenario: %s at %.1f MB - two workers that both did it need ~%.0f MB." % (
            top["scenario"], top["peak_mb"], 2 * top["peak_mb"]))
    if baseline and not same:
        print("Baseline not compared: it was recorded on another machine, BOQ or picture set "
              "(%s, BOQ #%s, %s items)." % (baseline.get("machine"), baseline.get("boq_id"), baseline.get("boq_items")))

    if args.record:
        if failed or not pictures:
            print("Not recorded: %s." % ("a scenario failed" if failed else
                                         "fewer than half of the BOQ's products have their picture on disk"))
            return 1
        commit = subprocess.run(["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({
            "recorded": datetime.date.today().isoformat(), "commit": commit, "machine": platform.node(),
            "platform": _sys.platform, "boq_id": boq_id, "boq_items": items, "pictures": pictures,
            "scenarios": {r["scenario"]: {"peak_mb": r["peak_mb"], "after_import_mb": r["after_import_mb"],
                                          "retained_mb": r["retained_mb"]} for r in results},
        }, indent=2) + "\n", encoding="utf-8")
        print("Baseline written: %s" % BASELINE.relative_to(_REPO_ROOT))
    return 1 if (failed or worse) else 0


if __name__ == "__main__":
    _sys.exit(main())
