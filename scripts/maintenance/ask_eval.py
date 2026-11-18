"""Runs the designer's question bank (docs/quality/ask_questions.json) against the real catalogue, and
checks each answer against the raw values in the database - read a second way here, so a mistake in
the reader cannot mark its own work.

This is how Ask is "trained": no model's weights change. Each way of asking is a row in the bank; when
one fails, the vocabulary in app/catalogue_query.py gets a row, and this shows nothing else broke.

    python scripts/maintenance/ask_eval.py             # no AI request, nothing written
    python scripts/maintenance/ask_eval.py --verbose   # also every reading and the top results
    python scripts/maintenance/ask_eval.py --model     # also what the AI model reads for each question
                                                       # that left words unread (one request each)
"""
import json
import re
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import catalogue_query as cq, models  # noqa: E402
from app.database import SessionLocal  # noqa: E402


HOSTILE_SECONDS = 1.0


def near(a, b):
    return a is not None and abs(float(a) - float(b)) < 0.51


def span(part, expected):
    return part and near(part.get("lo"), expected[0]) and near(part.get("hi"), expected[1])


def reads_problems(reads, r):
    """What the reading should have understood and did not."""
    bad = []
    for key, want in reads.items():
        ok = True
        if key == "make":
            ok = want in (r.get("make") or {}).get("values", [])
        elif key == "not_make":
            ok = sorted(r.get("not_make") or []) == sorted(want)
        elif key == "typology":
            ok = (r.get("typology") or {}).get("says") == want
        elif key in ("lumens", "wattage", "cct", "beam", "cutout", "size"):
            ok = span(r.get(key), want)
        elif key.endswith(("_below", "_max", "_from", "_above")):
            kind, _, side = key.rpartition("_")
            part = r.get(kind) or {}
            ok = near(part.get("hi"), want) if side in ("below", "max") else near(part.get("lo"), want)
            ok = ok and bool(part.get("strict")) == (side in ("below", "above"))
        elif key == "ip":
            ok = (r.get("ip") or {}).get("digits") == want
        elif key in ("cri", "ugr"):
            ok = (r.get(key) or {}).get("n") == want
        elif key == "control":
            ok = sorted((r.get("control") or {}).get("any") or []) == sorted(want)
        elif key == "features":
            ok = sorted(f["says"] for f in r.get("features") or []) == sorted(want)
        elif key == "shape":
            ok = (r.get("shape") or {}).get("value") == want
        elif key == "finish":
            ok = (r.get("finish") or {}).get("words") == want
        elif key == "order":
            ok = (r.get("order") or {}).get("key") == want
        elif key == "count":
            ok = bool(r.get("count"))
        elif key == "tunable":
            ok = "tunable" in r
        elif key == "per_metre":
            ok = bool((r.get("lumens") or {}).get("per_metre"))
        if not ok:
            bad.append("%s should read %s" % (key, want))
    return bad


# --- the second opinion: raw columns, read plainly -------------------------------------------------

def raw_lookup(db):
    S = models.Specsheet
    rows = db.query(S.id, S.make, S.product_name, S.fields, S.options, S.params).filter(S.approval_status == "approved").all()
    return {r.id: r for r in rows}


def hole(row):
    numbers = []
    for part in re.split(r"/|\bor\b", str((row.fields or {}).get("cutout_dimensions") or "")):
        found = re.findall(r"\d+(?:\.\d+)?", part)
        if found:
            numbers.append(max(float(n) for n in found))
    return numbers


def efficacy_of(row):
    for section in (row.params or {}).values():
        if isinstance(section, dict):
            for key, value in section.items():
                if "efficacy" in str(key).lower():
                    found = re.findall(r"\d+(?:\.\d+)?", str(value))
                    if found:
                        return float(found[0])
    return None


def second_opinion(reading, row_out, raw, product):
    """Does the raw record really hold what the answer claims?"""
    bad = []
    row = raw[row_out["id"]]
    if "make" in reading and row.make not in reading["make"]["values"]:
        bad.append("make is %s" % row.make)
    cut = reading.get("cutout")
    if cut:
        values = hole(row)
        lo = cut["lo"] if cut.get("lo") is not None else -1e9
        hi = cut["hi"] if cut.get("hi") is not None else 1e9
        if not any(lo <= v <= hi for v in values):
            bad.append("cut-out %s is not %s" % ((row.fields or {}).get("cutout_dimensions"), cut["says"]))
    # Wattage and IP from the raw text, read plainly. Per-metre strips are left to the reader.
    watts = reading.get("wattage")
    if watts and not watts.get("per_metre"):
        offered = [float(n) for t in [str((row.fields or {}).get("wattage") or "")] + [str(o) for o in (row.options or {}).get("wattage") or []]
                   for n in re.findall(r"(\d+(?:\.\d+)?)\s*W(?!/)", t)]
        spans = [(float(a), float(b)) for t in [str((row.fields or {}).get("wattage") or "")] for a, b in re.findall(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*W", t)]
        lo = watts["lo"] if watts.get("lo") is not None else -1e9
        hi = watts["hi"] if watts.get("hi") is not None else 1e9
        if not any(lo <= v <= hi for v in offered) and not any(a <= hi and b >= lo for a, b in spans):
            bad.append("wattage %s is not %s" % ((row.fields or {}).get("wattage"), watts["says"]))
    ip = reading.get("ip")
    if ip:
        stated = re.findall(r"IP\s?(\d)(\d)", " ".join([str((row.fields or {}).get("ip_rating") or "")] + [str(o) for o in (row.options or {}).get("ip_rating") or []]), re.I)
        if not any(a >= ip["digits"][0] and b >= ip["digits"][1] for a, b in stated):
            bad.append("IP %s is not %s" % ((row.fields or {}).get("ip_rating"), ip["says"]))
    lm = reading.get("lumens")
    if lm and product.get("lme"):
        watts = [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)\s*W", str((row.fields or {}).get("wattage") or ""))]
        eff = efficacy_of(row)
        lo = lm["lo"] if lm.get("lo") is not None else -1e9
        hi = lm["hi"] if lm.get("hi") is not None else 1e9
        if not (eff and any(lo <= round(eff * w) <= hi for w in watts)) and watts:
            bad.append("efficacy %s x %s gives no lumens in %s" % (eff, watts, lm["says"]))
    return bad


def main():
    verbose = "--verbose" in sys.argv
    use_model = "--model" in sys.argv
    bank = json.loads((_REPO_ROOT / "docs" / "quality" / "ask_questions.json").read_text(encoding="utf-8"))
    db = SessionLocal()
    catalogue = cq.build_catalogue(db, models.DEFAULT_DISCIPLINE)
    raw = raw_lookup(db)
    failed = 0
    stages = {}
    for q in bank["questions"]:
        reading = cq.read_request(q["ask"], catalogue)
        if use_model and cq.could_use_model(q["ask"], reading):
            reading = cq.merge_reading(reading, cq.interpret(q["ask"], catalogue))
        route = "catalogue" if cq.wants_catalogue(q["ask"], reading) else "records"
        problems = []
        if route != q["route"]:
            problems.append("went to %s, should go to %s" % (route, q["route"]))
        if q["route"] == "catalogue" and route == "catalogue":
            problems += reads_problems(q.get("reads") or {}, reading)
            if q.get("not_left") and reading.get("left"):
                problems.append("left unread: %s" % reading["left"])
            for key in q.get("absent") or []:
                if key in reading:
                    problems.append("must not read %s: %s" % (key, reading[key]))
            for word in q.get("left_has") or []:
                if word not in (reading.get("left") or []):
                    problems.append("should report %r as not understood, left is %s" % (word, reading.get("left")))
            found = cq.run(reading, catalogue, limit=2000)
            names = [r["name"] for r in found["rows"]]
            if "min" in q and found["total"] < q["min"]:
                problems.append("%d results, wanted at least %d" % (found["total"], q["min"]))
            if "max" in q and found["total"] > q["max"]:
                problems.append("%d results, wanted at most %d" % (found["total"], q["max"]))
            for name in q.get("exclude") or []:
                if name in names:
                    problems.append("must not offer %s" % name)
            for name in q.get("include") or []:
                if name not in names:
                    problems.append("must offer %s" % name)
            if q.get("sorted"):
                prices = [catalogue["by_id"][r["id"]].get("price") for r in found["rows"]]
                prices = [p for p in prices if p]
                if prices != sorted(prices, reverse=q["sorted"].startswith("-")):
                    problems.append("not in %s order: %s" % (q["sorted"], prices[:8]))
            for out in found["rows"]:
                problems += ["%s: %s" % (out["name"], b) for b in second_opinion(reading, out, raw, catalogue["by_id"][out["id"]])]
            text = cq.answer(reading, catalogue)["answer"]
            if verbose:
                print("    read: %s" % "; ".join(cq.describe(reading)))
                print("    %d found; %s" % (found["total"], "; ".join(names[:3])))
                if reading.get("left"):
                    print("    left: %s" % reading["left"])
            if not text.strip():
                problems.append("empty answer")
        stage = stages.setdefault(q["stage"], [0, 0])
        stage[0] += 1
        if problems:
            failed += 1
            print("FAIL %-9s %s" % (q["id"], q["ask"]))
            for p in problems[:6]:
                print("       - %s" % p)
        else:
            stage[1] += 1
            if verbose:
                print("ok   %-9s %s" % (q["id"], q["ask"]))
    for q in bank["sentences"]:
        blocks, left_out = cq.requirements_from_text(q["text"], catalogue)
        problems = []
        if len(blocks) != q["rows"]:
            problems.append("%d rows, wanted %d: %s" % (len(blocks), q["rows"], [b["block"] for b in blocks]))
        elif blocks:
            for which, block in (("first", blocks[0]), ("last", blocks[-1])):
                for key, want in (q.get(which) or {}).items():
                    got = block.get(key)
                    if key == "only_ids_min":
                        if len(block.get("only_ids") or []) < want:
                            problems.append("%s row names no product" % which)
                    elif key == "kinds_min":
                        if len(block.get("kinds") or []) < want:
                            problems.append("%s row's kinds are %s" % (which, block.get("kinds")))
                    elif key == "no_fields":
                        if any(k in block["fields"] for k in want):
                            problems.append("%s row must not have %s: %s" % (which, want, block["fields"]))
                    elif key == "warning_has":
                        if want not in (block.get("warning") or ""):
                            problems.append("%s row's warning %r should mention %s" % (which, block.get("warning"), want))
                    elif key == "fields":
                        wrong = {k: got.get(k) for k, v in want.items() if got.get(k) != v}
                        if wrong:
                            problems.append("%s row's fields %s, wanted %s" % (which, got, want))
                    elif got != want:
                        problems.append("%s row's %s is %r, wanted %r" % (which, key, got, want))
        if len(left_out) < q.get("ignored_min", 0):
            problems.append("%d left out and reported, wanted at least %d" % (len(left_out), q["ignored_min"]))
        stage = stages.setdefault("Match tab sentences", [0, 0])
        stage[0] += 1
        if problems:
            failed += 1
            print("FAIL %-9s %s" % (q["id"], q["text"]))
            for p in problems:
                print("       - %s" % p)
        else:
            stage[1] += 1
    from types import SimpleNamespace
    from app import assistant, llm_extract, main as app_main
    fake = SimpleNamespace(session={}, headers={}, cookies={}, query_params={}, state=SimpleNamespace())
    # Inputs built to make a regular expression or a number blow up. One such question once stalled a
    # whole server worker, so each must finish quickly and answer in words, through Ask's and Match's
    # real paths. The model is off for this: its waiting is not what is being timed.
    model_was = llm_extract.is_enabled
    llm_extract.is_enabled = lambda: False
    # Built first: the first request after 120 s rebuilds this, in a second or so, whatever it asks.
    # That is the catalogue's cost, not the input's, and would fail whichever input came first.
    cq.catalogue_for(db, models.DEFAULT_DISCIPLINE)
    stage = stages.setdefault("Hostile input", [0, 0])
    for raw_text in bank["hostile"]:
        stage[0] += 1
        began = time.perf_counter()
        try:
            try:
                assistant.ask(db, raw_text, models.DEFAULT_DISCIPLINE, True)
            except ValueError:
                pass                    # a reason in words is a right answer to nonsense
            app_main.requirement_match(request=fake, payload={"text": raw_text[:2000], "typed": True, "extract_only": True}, db=db)
            took = time.perf_counter() - began
            if took > HOSTILE_SECONDS:
                raise ValueError("took %.2f s" % took)
            stage[1] += 1
        except Exception as exc:  # any crash or stall here is what this exists to find
            failed += 1
            print("FAIL hostile %r: %r" % (raw_text[:50], exc))
    llm_extract.is_enabled = model_was
    for q in bank["sentences"]:
        stage = stages.setdefault("Match route", [0, 0])
        stage[0] += 1
        try:
            out = app_main.requirement_match(request=fake, payload={"text": q["text"], "typed": True, "extract_only": True}, db=db)
            if q["rows"] and len(out["results"]) != q["rows"]:
                raise ValueError("%d rows, wanted %d" % (len(out["results"]), q["rows"]))
            stage[1] += 1
        except Exception as exc:  # any crash in the route is the failure this is here to find
            failed += 1
            print("FAIL route %-9s %s: %r" % (q["id"], q["text"], exc))
    print()
    for name, (total, good) in stages.items():
        print("%-26s %d of %d" % (name, good, total))
    print("\n%s: %d of %d failed" % ("FAILED" if failed else "PASSED", failed, sum(s[0] for s in stages.values())))
    db.rollback()
    db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
