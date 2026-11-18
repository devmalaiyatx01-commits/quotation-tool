"""Cheap whole-repo sanity check, to run before calling any change done.

    python scripts/maintenance/verify_repo.py

1. Byte-compiles every .py under app/, scripts/ and the root. This catches syntax errors,
   not logic errors.
2. Checks that every relative link in every markdown file points at a file that exists.
3. Flags any script under scripts/ that builds a path from `Path(__file__).parent`. That
   expression is scripts/<dir>/, not the repo root, and it fails silently (see scripts/README.md).

Exits non-zero if anything is wrong. It doesn't touch the database or the network.
"""
import py_compile
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
SKIP = {".git", "venv", ".venv", "node_modules", "__pycache__", "data", "SampleInputs",
        "SampleInputOld"}


def walk(pattern):
    for p in sorted(ROOT.rglob(pattern)):
        if not any(part in SKIP for part in p.relative_to(ROOT).parts):
            yield p


problems = 0

compiled = 0
for py in walk("*.py"):
    try:
        py_compile.compile(str(py), doraise=True)
        compiled += 1
    except py_compile.PyCompileError as e:
        problems += 1
        print("COMPILE  %s\n         %s" % (py.relative_to(ROOT), str(e).strip()))

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
links = 0
for md in walk("*.md"):
    for i, line in enumerate(md.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        for target in LINK.findall(line):
            t = target.strip()
            if t.startswith(("http://", "https://", "mailto:", "#", "<")):
                continue
            t = t.split("#")[0].split(" ")[0]
            if not t:
                continue
            links += 1
            if not (md.parent / unquote(t)).resolve().exists():
                problems += 1
                print("LINK     %s:%d -> %s" % (md.relative_to(ROOT), i, t))

ROOT_EXPR = re.compile(r"(?<!_pathlib\.)Path\(__file__\)(\.resolve\(\))?\.parent\b(?!s)")
for py in walk("scripts/**/*.py"):
    for i, line in enumerate(py.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if ROOT_EXPR.search(line) and not line.lstrip().startswith("#") and "verify_repo" not in py.name:
            problems += 1
            print("PATH     %s:%d uses Path(__file__).parent - use _REPO_ROOT"
                  % (py.relative_to(ROOT), i))

# Two top-level functions with the same name in static/app.js are silent: JavaScript keeps the
# LAST definition and the first one simply stops existing. It happened on 2026-09-21 - a new
# npdAccessoriesHtml() shadowed the NPD product editor's one - and nothing threw; the new screen
# just called the old function with the wrong argument. The file is 13,000 lines, so nobody is
# going to catch this by reading it.
JS_FUNC = re.compile(r"^(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(")
app_js = ROOT / "static" / "app.js"
if app_js.is_file():
    seen = {}
    for i, line in enumerate(app_js.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        m = JS_FUNC.match(line)
        if m:
            seen.setdefault(m.group(1), []).append(i)
    for name, lines in sorted(seen.items()):
        if len(lines) > 1:
            problems += 1
            print("JSDUP    static/app.js defines %s() %d times, at lines %s - only the last exists"
                  % (name, len(lines), ", ".join(str(n) for n in lines)))

# The same hazard in Python: a module-level name defined twice keeps the last one, so an edit
# to the first is silently thrown away. app/npd.py carried two identical GROUP_ORDER lists for
# exactly this reason - harmless while they agreed, a trap the moment one was corrected. Only
# app/ is checked, and only top-level definitions: a name redefined inside a function or under
# a try/except is usually deliberate.
PY_DEF = re.compile(r"^(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
PY_CONST = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=")
for py in sorted((ROOT / "app").glob("*.py")):
    seen = {}
    for i, line in enumerate(py.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        m = PY_DEF.match(line) or PY_CONST.match(line)
        if m:
            seen.setdefault(m.group(1), []).append(i)
    for name, lines in sorted(seen.items()):
        if len(lines) > 1:
            problems += 1
            print("PYDUP    app/%s defines %s %d times, at lines %s - only the last one exists"
                  % (py.name, name, len(lines), ", ".join(str(n) for n in lines)))

print("\ncompiled %d .py file(s), checked %d markdown link(s): %s"
      % (compiled, links, "OK" if not problems else "%d PROBLEM(S)" % problems))
sys.exit(1 if problems else 0)
