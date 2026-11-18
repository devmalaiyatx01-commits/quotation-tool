"""An outline of a big source file, so you read 30 lines of map instead of 20,000 lines of code.

    python scripts/maintenance/code_map.py static/app.js --sections     # the ~66 sections only
    python scripts/maintenance/code_map.py static/app.js --grep npd     # functions/sections matching
    python scripts/maintenance/code_map.py app/main.py --routes --grep boqs
    python scripts/maintenance/code_map.py app/main.py --grep permission

What it prints, one line each, with the line number to read from:
- static/*.js: the section banners (`// ---------------- Library ----------------`) and every
  top-level function under them.
- *.py: every route (`GET /api/boqs/{boq_id}  -> get_boq`) and every top-level def/class.

Why it exists: static/app.js is ~23,000 lines and app/main.py ~9,500. Reading either whole costs
more context than most tasks have, and line numbers written in a doc are stale within a week. A map
generated on demand is never stale. Read the range it points at, not the file.

Reads the working tree only; no database, no network.
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
import re

JS_SECTION = re.compile(r"^// -{4,}\s*(.+?)\s*-{4,}\s*$")
JS_FUNC = re.compile(r"^(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(")
JS_CONST_FN = re.compile(r"^(?:const|let)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>")


def js_outline(text):
    """[(line, kind, name, section)] - kind is 'section' or 'fn'."""
    out, section = [], "(top of file)"
    for i, line in enumerate(text.splitlines(), 1):
        m = JS_SECTION.match(line)
        if m:
            section = m.group(1)
            out.append((i, "section", section, section))
            continue
        m = JS_FUNC.match(line) or JS_CONST_FN.match(line)
        if m:
            out.append((i, "fn", m.group(1), section))
    return out


def py_outline(text):
    out = []
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            routes = []
            for d in node.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and d.func.attr in ("get", "post", "put", "patch", "delete")
                        and d.args and isinstance(d.args[0], ast.Constant)):
                    routes.append("%s %s" % (d.func.attr.upper(), d.args[0].value))
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            for r in routes:
                out.append((start, "route", "%-7s -> %s" % (r, node.name), r))
            if not routes:
                out.append((node.lineno, "def", node.name + "()", ""))
        elif isinstance(node, ast.ClassDef):
            out.append((node.lineno, "class", "class " + node.name, ""))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # Module-level tables such as _ACTIVITY_ROUTES or _PUBLIC_PATHS: named in CAPITALS.
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.lstrip("_").isupper():
                    out.append((node.lineno, "const", t.id, ""))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file", help="e.g. static/app.js or app/main.py")
    ap.add_argument("--grep", help="only items whose name (or JS section) matches this, case-insensitive")
    ap.add_argument("--sections", action="store_true", help="JS: section banners only")
    ap.add_argument("--routes", action="store_true", help="Python: routes only")
    args = ap.parse_args()

    path = (_REPO_ROOT / args.file).resolve()
    if not path.is_file():
        print("No such file: %s" % args.file)
        return 2
    text = path.read_text(encoding="utf-8", errors="replace")
    total = text.count("\n") + 1
    pattern = re.compile(args.grep, re.I) if args.grep else None

    if path.suffix == ".js":
        items = js_outline(text)
        if args.sections:
            items = [it for it in items if it[1] == "section"]
        if pattern:
            # A function matches by its own name or by its section's title, so `--grep library`
            # lists every function in the Library section.
            items = [it for it in items if pattern.search(it[2]) or (it[1] == "fn" and pattern.search(it[3]))]
        last_section = None
        for line, kind, name, section in items:
            if kind == "section":
                print("\nL%-6d == %s ==" % (line, name))
                last_section = name
            else:
                if section != last_section:
                    print("\n        (in: %s)" % section)
                    last_section = section
                print("L%-6d   %s" % (line, name))
    else:
        items = py_outline(text)
        if args.routes:
            items = [it for it in items if it[1] == "route"]
        if pattern:
            items = [it for it in items if pattern.search(it[2])]
        for line, kind, name, _ in items:
            print("L%-6d %s" % (line, name))
    print("\n%d items shown; %s has %d lines. Read a range: lines L to the next item's L." % (
        len(items), args.file, total))
    return 0


if __name__ == "__main__":
    _sys.exit(main())
