"""The checks every change passes before review, in one command. See DEVELOPMENT.md, "Gates".

    python scripts/maintenance/gate.py            # the quick gate (seconds): run it before every commit
    python scripts/maintenance/gate.py --full     # + the memory profile (about a minute): before review
    python scripts/maintenance/gate.py --base HEAD~2

Each check prints PASS, WARN or FAIL with the evidence. Any FAIL exits 1. A WARN is something a
person (or the repo-reviewer agent) must look at and answer in the change record; it does not block.

  change record   docs/changes/*.md names this branch ("Branch: <name>") and fills Why, Evidence,
                  Acceptance and Rollback - a change without a reason is not reviewed
  repo            scripts/maintenance/verify_repo.py: everything compiles, every doc link resolves
  scope           the files this branch changes, so a mixed diff is seen before it is shipped;
                  a migration together with app code is flagged (FinalDeployment.md: never both)
  special cases   no new brand/record special cases in shared code without "Special cases: +N because"
  context anchors every `file::name` in docs/context/*.md still exists - a stale map misleads
  leftovers       in app/, static/, templates/: a debugging stop fails; print() and console.log warn
  secrets         an API key or password written into a changed line fails
  escaping        new HTML in app.js with ${...} that is not escapeHtml()/attr() warns
  size            how much app/main.py and static/app.js grew - information, read with the diff
  memory (--full) scripts/maintenance/memory_profile.py against docs/quality/memory_baseline.json

Reads git and the working tree; only --full touches the local database (see memory_profile.py).
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
import re
import subprocess

# A Windows console prints in cp1252; a quoted line of app.js holding "▾" crashed the gate (and the
# pre-commit hook with it) on 17 Nov 2026. Anything the console cannot show is escaped instead.
try:
    _sys.stdout.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass

_sys.path.insert(0, str(_REPO_ROOT / "scripts" / "maintenance"))
import audit_special_cases  # noqa: E402  (a sibling script; one definition of "special case")

CHANGES = _REPO_ROOT / "docs" / "changes"
CONTEXT = _REPO_ROOT / "docs" / "context"
REQUIRED = ("Why", "Evidence", "Acceptance", "Rollback")
results = []


def git(*args):
    p = subprocess.run(["git", "-C", str(_REPO_ROOT), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.stdout


def report(status, name, detail, lines=()):
    results.append(status)
    print("%-5s %-16s %s" % (status, name, detail))
    for line in lines:
        print("      " + line)


# Untracked files count only inside the source tree. The main folder also holds hundreds of the
# owner's own untracked files (catalogue PDFs, reports, spreadsheets) that are never committed;
# counting them listed 833 "changed" files and let a password-like line in a private note fail
# every commit. A new file outside these folders is seen once it is `git add`ed, because the
# tracked diff includes the index - which is what the pre-commit hook checks.
SOURCE_DIRS = ("app/", "static/", "templates/", "scripts/", "docs/", ".claude/", ".githooks/")


def changed_files(base):
    # -z: NUL-separated, so a path with spaces ("Duragreen Catalogue/...") stays one path.
    tracked = set(git("diff", "--name-only", "-z", base).split("\0"))
    untracked = {f for f in git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
                 if f.startswith(SOURCE_DIRS)}
    return sorted((tracked | untracked) - {""})


def added_lines(base, path):
    """The lines this branch adds to a file (committed or not), or the whole file if it is new."""
    at_base = subprocess.run(["git", "-C", str(_REPO_ROOT), "cat-file", "-e", "%s:%s" % (base, path)],
                             capture_output=True).returncode == 0
    if not at_base:
        p = _REPO_ROOT / path
        return p.read_text(encoding="utf-8", errors="replace").splitlines() if p.is_file() else []
    diff = git("diff", "-U0", base, "--", path)     # base -> working tree: commits + uncommitted
    return [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]


# ---------------------------------------------------------------- the checks

def check_change_record(branch):
    if branch in ("main", "HEAD"):
        report("WARN", "change record", "on %s - gates are for a branch; start one (DEVELOPMENT.md, step 2)" % branch)
        return None
    for md in sorted(CHANGES.glob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^Branch:\s*`?%s`?\s*$" % re.escape(branch), text, re.M):
            empty = []
            for field in REQUIRED:
                m = re.search(r"^## %s\b.*?\n(.*?)(?=^## |\Z)" % field, text, re.M | re.S)
                body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip() if m else ""
                if len(body) < 20:
                    empty.append(field)
            if empty:
                report("FAIL", "change record", "%s: fill %s" % (md.relative_to(_REPO_ROOT).as_posix(),
                                                              ", ".join(empty)))
            else:
                report("PASS", "change record", md.relative_to(_REPO_ROOT).as_posix())
            return text
    report("FAIL", "change record", "no docs/changes/*.md says \"Branch: %s\" - copy docs/changes/TEMPLATE.md" % branch)
    return None


def check_repo():
    p = subprocess.run([_sys.executable, str(_REPO_ROOT / "scripts" / "maintenance" / "verify_repo.py")],
                       capture_output=True, text=True, cwd=str(_REPO_ROOT))
    last = (p.stdout.strip().splitlines() or ["(no output)"])[-1]
    if p.returncode:
        report("FAIL", "repo", last, p.stdout.strip().splitlines()[:15])
    else:
        report("PASS", "repo", last)


def check_scope(files):
    migrations = [f for f in files if f.startswith("scripts/migrations/")]
    app_code = [f for f in files if f.startswith(("app/", "static/", "templates/"))]
    detail = "%d file(s) changed" % len(files)
    if migrations and app_code:
        report("WARN", "scope", detail + " - a migration AND app code: ship them separately if you can "
               "(rollback stays unambiguous) or say why not in the change record", files[:40])
    else:
        report("PASS", "scope", detail, files[:40])


def check_special_cases(base, record):
    rows, new = audit_special_cases.compare(base)
    total = sum(r[2] for r in rows)
    if not new:
        report("PASS", "special cases", "none added (%d in shared code; the number may only go down)" % total)
        return
    said = re.search(r"^Special cases:\s*\+(\d+)\s+because\s+\S", record or "", re.M | re.I)
    lines = ["%s:%d [%s] %s" % (p, n, k, t[:110]) for p, n, k, t in new]
    if said and int(said.group(1)) >= len(new):
        report("WARN", "special cases", "%d added, explained in the change record - the reviewer judges "
               "the reason" % len(new), lines)
    else:
        report("FAIL", "special cases", "%d added without a reason. Put the rule in data or the brand's "
               "module, or write \"Special cases: +%d because ...\"" % (len(new), len(new)), lines)


ANCHOR = re.compile(r"`([\w./-]+\.(?:py|js|css|html))::([A-Za-z_$][\w$]*)`")


def check_anchors():
    missing, count = [], 0
    for md in sorted(CONTEXT.glob("*.md")):
        for i, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            for path, name in ANCHOR.findall(line):
                count += 1
                f = _REPO_ROOT / path
                src = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
                defined = re.search(r"(^|\n)\s*(async\s+def|def|class|function|async\s+function|const|let|var)\s+%s\b"
                                    r"|(^|\n)%s\s*[:=]" % (re.escape(name), re.escape(name)), src)
                if not defined:
                    missing.append("%s:%d  %s::%s" % (md.relative_to(_REPO_ROOT).as_posix(), i, path, name))
    if missing:
        report("FAIL", "context anchors", "%d of %d no longer exist - update the context pack" % (len(missing), count),
               missing)
    else:
        report("PASS", "context anchors", "%d anchors in docs/context/ all resolve" % count)


SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[0-9a-f]{20,}|AKIA[0-9A-Z]{16}|"
                    r"(password|passwd|secret|api_key|apikey)\s*[:=]\s*[\"'][^\"'\s]{6,}[\"'])", re.I)


def check_leftovers_and_secrets(base, files):
    fails, warns, secrets = [], [], []
    for f in files:
        if not f.endswith((".py", ".js", ".html", ".md", ".json", ".bat", ".sh")) or not (_REPO_ROOT / f).is_file():
            continue
        for line in added_lines(base, f):
            s = line.strip()
            if SECRET.search(s) and "example" not in s.lower() and "<" not in s:
                # Never echo the value itself: this output ends up in logs and chats.
                secrets.append("%s: %s" % (f, SECRET.sub("<redacted>", s)[:100]))
            if not f.startswith(("app/", "static/", "templates/")):
                continue            # leftovers matter in what ships; scripts print by design
            if re.search(r"\bdebugger;|\bbreakpoint\(\)|\bpdb\.set_trace\(", s):
                fails.append("%s: %s" % (f, s[:110]))
            elif (f.startswith("app/") and re.match(r"print\(", s)) or \
                    (f == "static/app.js" and "console.log(" in s):
                warns.append("%s: %s" % (f, s[:110]))
    if secrets:
        report("FAIL", "secrets", "a key or password in a changed line - remove it; secrets live in the "
               "environment", secrets)
    else:
        report("PASS", "secrets", "none in the changed lines")
    if fails:
        report("FAIL", "leftovers", "debugging stops left in", fails)
    elif warns:
        report("WARN", "leftovers", "print()/console.log added - keep only if it is meant for the log", warns[:15])
    else:
        report("PASS", "leftovers", "none")


SAFE_INTERP = re.compile(r"^\s*(escapeHtml|attr|money|fmt\w*|formatMoney|Number|String\(Number|\w+Html|"
                         r"\w+\.length|\w+\s*\?\s*\"|i|n|idx|count)\b")


def check_escaping(base, files):
    if "static/app.js" not in files:
        report("PASS", "escaping", "app.js unchanged")
        return
    doubtful = []
    for line in added_lines(base, "static/app.js"):
        if not re.search(r"<[a-z]", line):
            continue
        for expr in re.findall(r"\$\{([^}]{1,80})\}", line):
            if not SAFE_INTERP.match(expr):
                doubtful.append("${%s}  in: %s" % (expr.strip(), line.strip()[:90]))
    if doubtful:
        report("WARN", "escaping", "%d interpolation(s) in new HTML not visibly escaped - each must be "
               "escapeHtml()/attr() or HTML this code built" % len(doubtful), doubtful[:20])
    else:
        report("PASS", "escaping", "every ${...} in new HTML is escaped or built HTML")


def check_size(base):
    parts = []
    for f in ("app/main.py", "static/app.js"):
        now = (_REPO_ROOT / f).read_text(encoding="utf-8", errors="replace").count("\n")
        then = git("show", "%s:%s" % (base, f)).count("\n")
        parts.append("%s %d (%+d)" % (f, now, now - then))
    report("INFO", "size", ", ".join(parts))


def check_memory():
    p = subprocess.run([_sys.executable, str(_REPO_ROOT / "scripts" / "maintenance" / "memory_profile.py")],
                       capture_output=True, text=True, cwd=str(_REPO_ROOT))
    out = [l for l in p.stdout.splitlines() if l.strip()]
    over = [l for l in out if "OVER BUDGET" in l or l.startswith("FAILED")]
    worst = next((l for l in out if l.startswith("Worst scenario")), "no result")
    if p.returncode:
        report("FAIL", "memory", "a scenario failed or grew past the budget - " + worst, over or out[-12:])
    elif any(l.startswith("Baseline not compared") for l in out):
        report("WARN", "memory", "baseline is from another machine or data set - not compared. " + worst)
    else:
        report("PASS", "memory", worst)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="main", help="what the branch is compared with (default: main)")
    ap.add_argument("--full", action="store_true", help="also run the memory profile (about a minute)")
    args = ap.parse_args()

    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    base = git("merge-base", args.base, "HEAD").strip() or args.base
    files = changed_files(base)
    print("Gate for %s, compared with %s (%s)\n" % (branch, args.base, base[:8]))

    record = check_change_record(branch)
    check_repo()
    check_scope(files)
    check_special_cases(base, record)
    check_anchors()
    check_leftovers_and_secrets(base, files)
    check_escaping(base, files)
    check_size(base)
    if args.full:
        check_memory()

    fails, warns = results.count("FAIL"), results.count("WARN")
    print("\n%s - %d FAIL, %d WARN. %s" % ("FAILED" if fails else "PASSED", fails, warns,
          "Fix every FAIL." if fails else ("Answer every WARN in the change record." if warns else "")))
    return 1 if fails else 0


if __name__ == "__main__":
    _sys.exit(main())
