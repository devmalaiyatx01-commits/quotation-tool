"""Count the special cases in the app's shared code, and refuse new ones that have no reason.

    python scripts/maintenance/audit_special_cases.py                 # counts per file vs main
    python scripts/maintenance/audit_special_cases.py --list app/main.py
    python scripts/maintenance/audit_special_cases.py --against HEAD~3 --new-only

A special case is a line of SHARED code that decides something by naming one brand or one
record instead of reading it from data:

- a comparison with a brand's name:            if (s.make or "") == "Exenia":
- a brand's own predicate or constant:          if duragreen.is_duragreen(s): / make == duragreen.MAKE
- a test on a brand-named string:               path.startswith("/api/npd/duragreen/")
- a hard-coded record id:                       if sheet.id == 1530:

Why count them: every one is a place the next brand has to be added by hand, and a place where
one brand's rule can leak into another's (CLAUDE.md, rule 5). The vendor framework
(vendor_order_codes, NPD structures, configurator.py) exists so that a brand's rules are DATA.
This script is the ratchet that makes the count go down over time and never quietly up:

- A brand's OWN module (app/duragreen*.py, app/exenia_codes.py) is where its rules belong, so
  lines there are not counted for that brand.
- The count is compared with another revision (main by default). Lines are matched by their
  text, not their number, so code that merely moved is not "new".
- scripts/maintenance/gate.py fails a branch that adds special cases unless its change record
  says why ("Special cases: +N because ...").

Static analysis only: it reads files and git objects, never the database or the network.
Exit code: 0 = no new special cases, 1 = new ones (listed), 2 = usage error.
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
import ast
import collections
import re
import subprocess

# The makes in the library on 2026-11-02 (SELECT DISTINCT make FROM specsheets). When a new
# brand arrives, add it here, or its special cases will not be counted.
BRANDS = ["Duragreen", "DuraGreen", "Exenia", "Lumenpulse", "InHouse", "MEAN WELL", "OSRAM", "BAG",
          "ALLOOKING", "SUNRICHER", "ORLUNA", "E-POWER", "EUCHIPS", "LTECH", "LITEX", "AI DIMMING",
          "B & R", "AMERLUX"]
SLUGS = sorted({re.sub(r"[^a-z0-9]", "", b.lower()) for b in BRANDS}, key=len, reverse=True)


def _brand_pattern(brand):
    """A brand is named when its words appear as whole words: "Exenia", "/api/npd/duragreen/code",
    "MEAN WELL" and "mean_well" do; "bags" does not. A brand with a one-letter word is matched only
    as it is spelt ("B & R", "E-POWER", spaces optional), because "b ... r" turns up in ordinary
    code: `b === r.brand` is not the brand B & R."""
    words = re.findall(r"[a-z0-9]+", brand.lower())
    if min(len(w) for w in words) == 1:
        joined = r"\s*".join(re.escape(part) for part in brand.lower().split())
    else:
        joined = r"[^a-z0-9]*".join(words)
    return re.compile(r"(?<![a-z0-9])" + joined + r"(?![a-z0-9])")


BRAND_PATTERNS = sorted({(re.sub(r"[^a-z0-9]", "", b.lower()), _brand_pattern(b)) for b in BRANDS},
                        key=lambda sp: -len(sp[0]))

SHARED = ["app/*.py", "static/app.js"]
JS_TEST = re.compile(r"===|!==|==|!=|\bcase\b|\.includes\(|\.startsWith\(|\.endsWith\(|\.indexOf\(")
JS_LITERAL = re.compile(r"(['\"`])((?:(?!\1).){1,80})\1")
JS_PREDICATE = re.compile(r"\bis(" + "|".join(s.capitalize() for s in SLUGS) + r")\w*\s*\(", re.I)
MAGIC_ID_JS = re.compile(r"\b\w*[iI]d\s*(===|!==|==|!=)\s*\d{2,}\b")


def brand_in(text):
    """The slug of the brand a piece of text names ("duragreen", "meanwell"), or None."""
    low = str(text).lower()
    return next((slug for slug, pattern in BRAND_PATTERNS if pattern.search(low)), None)


def owner_slugs(path):
    """The brands a file belongs to: app/duragreen_sheet.py belongs to duragreen."""
    name = _pathlib.PurePosixPath(path).name.lower()
    return {s for s in SLUGS if name.startswith(s)}


# ---------------------------------------------------------------- Python, from the syntax tree

def _strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for e in node.elts:
            yield from _strings(e)


def _brand_of_name(node):
    """`duragreen.MAKE`, `duragreen.is_duragreen`, `is_exenia` - a brand named by an identifier."""
    if isinstance(node, ast.Attribute):
        return brand_in(node.attr) or _brand_of_name(node.value)
    if isinstance(node, ast.Name):
        return brand_in(node.id.replace("_", " "))
    return None


def _is_magic_id(cmp):
    sides = [cmp.left] + list(cmp.comparators)
    named_id = any(isinstance(s, (ast.Attribute, ast.Name)) and
                   re.search(r"(^|_)id$", s.attr if isinstance(s, ast.Attribute) else s.id) for s in sides)
    number = any(isinstance(s, ast.Constant) and type(s.value) is int and s.value > 9 for s in sides) or \
        any(isinstance(s, (ast.Tuple, ast.List, ast.Set)) and
            any(isinstance(e, ast.Constant) and type(e.value) is int and e.value > 9 for e in s.elts)
            for s in sides)
    return named_id and number


def python_cases(path, source):
    owned = owner_slugs(path)
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise SystemExit("%s does not parse: %s" % (path, e))
    lines = source.splitlines()
    found = {}
    for node in ast.walk(tree):
        kind = None
        if isinstance(node, ast.Compare):
            if _is_magic_id(node):
                kind = "record id"
            else:
                for side in [node.left] + list(node.comparators):
                    b = next((brand_in(s) for s in _strings(side) if brand_in(s)), None) or _brand_of_name(side)
                    if b and b not in owned:
                        kind = b
                        break
        elif isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if fname.startswith("is_") and brand_in(fname[3:].replace("_", " ")):
                b = brand_in(fname[3:].replace("_", " "))
                kind = b if b not in owned else None
            elif fname in ("startswith", "endswith") and node.args:
                b = next((brand_in(s) for s in _strings(node.args[0]) if brand_in(s)), None)
                kind = b if b and b not in owned else None
        if kind and node.lineno not in found:
            found[node.lineno] = (kind, lines[node.lineno - 1].strip())
    return found


# ---------------------------------------------------------------- JavaScript, line by line
# app.js has no parser in this repo (and no package may be installed for one), so it is read a
# line at a time: a line counts when it both TESTS something and names a brand in a string
# literal or a brand predicate. Text a person reads ("Show all DuraGreen") does not test anything.

def js_cases(path, source):
    found = {}
    for i, line in enumerate(source.splitlines(), 1):
        s = line.strip()
        if s.startswith("//") or s.startswith("*"):
            continue
        kind = None
        if MAGIC_ID_JS.search(s):
            kind = "record id"
        elif JS_PREDICATE.search(s):
            kind = brand_in(JS_PREDICATE.search(s).group(1))
        elif JS_TEST.search(s):
            for m in JS_LITERAL.finditer(s):
                literal = m.group(2)
                if "<" in literal or len(literal) > 40:     # markup or a sentence, not a key
                    continue
                if brand_in(literal):
                    kind = brand_in(literal)
                    break
        if kind:
            found[i] = (kind, s)
    return found


# ---------------------------------------------------------------- reading a revision

def _git(*args):
    return subprocess.run(["git", "-C", str(_REPO_ROOT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def files_at(rev):
    """{path: source} for the shared files at a revision, or in the working tree when rev is None."""
    out = {}
    if rev is None:
        for pattern in SHARED:
            for p in sorted(_REPO_ROOT.glob(pattern)):
                out[p.relative_to(_REPO_ROOT).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
        return out
    listed = _git("ls-tree", "-r", "--name-only", rev, "app", "static/app.js")
    if listed.returncode:
        raise SystemExit("git cannot read revision %r: %s" % (rev, listed.stderr.strip()))
    for path in listed.stdout.split():
        if path == "static/app.js" or re.fullmatch(r"app/[^/]+\.py", path):
            out[path] = _git("show", "%s:%s" % (rev, path)).stdout
    return out


def cases_of(files):
    return {path: (js_cases if path.endswith(".js") else python_cases)(path, src)
            for path, src in files.items()}


def compare(rev="main"):
    """(per-file rows, new special cases). Shared by gate.py."""
    now, then = cases_of(files_at(None)), cases_of(files_at(rev))
    rows, new = [], []
    for path in sorted(set(now) | set(then)):
        a, b = now.get(path, {}), then.get(path, {})
        before = collections.Counter(text for _, text in b.values())
        for line, (kind, text) in sorted(a.items()):
            if before[text]:
                before[text] -= 1
            else:
                new.append((path, line, kind, text))
        if a or b:
            rows.append((path, len(b), len(a)))
    return rows, new


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--against", default="main", help="revision to compare with (default: main)")
    ap.add_argument("--list", metavar="FILE", help="print every special case in one file (working tree)")
    ap.add_argument("--new-only", action="store_true", help="print only the new special cases")
    args = ap.parse_args()

    if args.list:
        path = args.list.replace("\\", "/")
        files = files_at(None)
        if path not in files:
            print("Not a shared file: %s (shared: %s)" % (path, ", ".join(SHARED)))
            return 2
        by_kind = collections.Counter()
        for line, (kind, text) in sorted(cases_of({path: files[path]})[path].items()):
            by_kind[kind] += 1
            print("%s:%d  [%s]  %s" % (path, line, kind, text[:140]))
        print("\n%d special cases in %s: %s" % (sum(by_kind.values()), path,
                                                ", ".join("%s %d" % kv for kv in by_kind.most_common())))
        return 0

    rows, new = compare(args.against)
    if not args.new_only:
        print("%-28s %8s %8s %6s" % ("file", args.against[:8], "now", "change"))
        for path, then, now in sorted(rows, key=lambda r: -r[2]):
            print("%-28s %8d %8d %+6d" % (path, then, now, now - then))
        print("%-28s %8d %8d %+6d" % ("TOTAL", sum(r[1] for r in rows), sum(r[2] for r in rows),
                                      sum(r[2] - r[1] for r in rows)))
    if new:
        print("\n%d new special case(s) compared with %s:" % (len(new), args.against))
        for path, line, kind, text in new:
            print("  %s:%d  [%s]  %s" % (path, line, kind, text[:140]))
        print("\nMove the rule into data (NPD, vendor_code_rules, configurator.py) or the brand's own "
              "module, or give the reason in the change record: \"Special cases: +%d because ...\"." % len(new))
        return 1
    print("\nNo new special cases compared with %s." % args.against)
    return 0


if __name__ == "__main__":
    _sys.exit(main())
