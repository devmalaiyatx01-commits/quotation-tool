"""Ask the app: a short question answered from the app's own records by the free AI model - a
small retrieval-augmented generation ("RAG") over the catalogue and the BOQs.

Two halves, and only the second costs anything:

1. Retrieval, here, free. Every approved product of the tool the person is in, and - for someone
   who may see BOQs - every BOQ, is written out as a short text record ("P1531 ...", "B45 ...").
   A question is matched to them by its words, each word counting by how rare it is (BM25, the
   ranking search engines grew up on), so "NYX" outweighs "downlight" and "12W"/"3000K"/"IP65"
   find the products that print them. No embeddings and no vector database: no package to
   install, nothing to keep in step, and the ranking can be read and explained. A question that
   matches no record is answered here, without asking the model at all.

Before either: a request to find products by what they are ("nearly 1400 lm, cut-out under 100 mm")
is not a word search at all. app/catalogue_query.py reads its constraints and checks them against
the catalogue's values, and the answer is written from those values without asking a model.

2. Generation, one request through llm_extract.chat(): the question and the best-matching records,
   and the rule to answer from those records only. The answer is then checked, not trusted: a
   source it cites that it was not given is dropped, and an amount of money it names that none of
   its records holds is pointed out as something to check.

The model reads and proposes; it changes nothing. Questions come out of the same daily allowance
as reading files (llm_extract.daily_cap), and may use only ASK_SHARE of it, so a busy afternoon of
questions never leaves an import without the model.
"""
import math
import re
import threading
import time
from collections import Counter

from sqlalchemy.orm import selectinload

from . import catalogue_query, llm_extract, match_index, models, npd

ASK_SHARE = 0.5               # of the day's AI requests; the rest stays for reading files
CONTEXT_PRODUCTS = 12
CONTEXT_BOQS = 6
CONTEXT_CHARS = 14000         # about 4,000 tokens of records - small next to the models' windows
RECORD_CHARS = 900
QUESTION_CHARS = 500
INDEX_SECONDS = 120           # the records are rebuilt at most this often per tool
ANSWER_TOKENS = 700

SYSTEM_PROMPT = (
    "You answer questions from people at a lighting design studio, about their own product "
    "catalogue and their BOQs (bills of quantities). You are given RECORDS from the studio's app "
    "and a QUESTION. Return ONLY a JSON object:\n"
    '{"answer": "...", "sources": ["P12", "B3"]}\n'
    "\n"
    "Rules:\n"
    "1. Use only what the RECORDS say. Never use outside knowledge for a product, a specification "
    "or a price. If the records do not answer the question, say so in one sentence and say what "
    "to search for instead.\n"
    "2. `sources` lists the id of every record the answer uses, and no other.\n"
    "3. Copy prices, codes and specifications exactly as the records write them. You may add up "
    "or compare numbers the records give; say when you have. A product's \"used in\" line is the "
    "complete list of the BOQs it is on.\n"
    "4. Plain English, a short paragraph or a short list, at most about 120 words. Call products "
    "and BOQs by their names, not only their ids.\n"
    "5. You cannot change anything in the app. If asked to, say what the person can do in the "
    "app instead.\n"
    "6. Never say a product has a value its record does not state, and never give a value of one "
    "product as another's. A product's name is not a specification: \"100 Fixed\" says nothing "
    "about its cut-out unless its record does. A lumen figure marked estimated is an estimate; "
    "say so when you use it.\n"
    "Return the JSON object and nothing else."
)

_WORD = re.compile(r"[a-z0-9]+")
# Words a question is made of that say nothing about which record it is about.
_STOP = set("a an and are as at be by can do does for from have how i in is it its me my of on or "
            "our please show the their them there these this to us was we what when where which who "
            "with you your tell give list find any all about".split())


def _tokens(text):
    """The words that can tell records apart, a plural read as its singular ("downlights", "BOQs")."""
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") and not w[0].isdigit() else w
            for w in _WORD.findall(str(text or "").lower()) if w not in _STOP]


def money(value, currency="INR") -> str:
    """₹N - the Indian grouping the studio's quotations use; another currency by its code."""
    whole, _, frac = ("%.2f" % abs(float(value))).partition(".")
    if len(whole) > 3:
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", whole[:-3])
        whole = head + "," + whole[-3:]
    text = whole + ("" if frac == "00" else "." + frac)
    sign = "-" if float(value) < 0 else ""
    return sign + ("₹" + text if (currency or "INR").upper() == "INR" else "%s %s" % (currency, text))


def _qty(text):
    try:
        return float(str(text or "").replace(",", "").split()[0])
    except (ValueError, IndexError):
        return None


# --- the records --------------------------------------------------------------------------------

def _product_record(sheet) -> dict:
    title = " ".join(str(sheet.product_name or sheet.filename or "").split())
    head = "P%d · %s · %s · %s" % (sheet.id, title, sheet.make or "make not given", sheet.typology or "typology not given")
    specs = []
    for key, value in sorted((sheet.fields or {}).items()):
        text = " ".join(str(value or "").split())
        if text and key not in ("accessories", "description"):
            specs.append("%s %s" % (key.replace("_", " "), text[:80]))
    accessories = " ".join(str((sheet.fields or {}).get("accessories") or "").split())
    lines = [head]
    # A lumen figure is what people ask for most and DuraGreen never prints one: without this line a
    # question about lumens reached the model with nothing to answer from, and it made one up.
    values = match_index.product_entry(sheet, sheet.params)
    if values.get("lm"):
        lines.append("lumens: %s%s" % (" / ".join("%g lm" % iv[0] if iv[0] == iv[1] else "%g-%g lm" % iv for iv in values["lm"]),
                                       " (estimated: published efficacy x wattage)" if values.get("lme") else ""))
    adders = ["%s %s +%s" % (field.replace("_", " "), option, money(amount))
              for field, options in sorted((sheet.price_adders or {}).items()) if isinstance(options, dict)
              for option, amount in options.items() if amount]
    if npd.priced_by_rule(sheet) and (sheet.base_price or adders):
        # Never "base ₹N": products priced by their options alone have no base price to state.
        lines.append("price in NPD: %s%s" % ("base " + money(sheet.base_price) if sheet.base_price else "by its options",
                                             ("; options " + "; ".join(adders[:10])) if adders else ""))
    elif npd.priced_by_rule(sheet):
        lines.append("price: none entered in NPD yet")
    else:
        lines.append("price: not set in NPD - priced in each BOQ")
    # Last, because a record is cut at RECORD_CHARS: the long list once cut off the price line, and
    # "what is the NPD price of NYX -Midi?" was answered "not recorded".
    if specs:
        lines.append("specification: " + "; ".join(specs))
    if accessories:
        lines.append("accessories: " + accessories[:200])
    text = "\n".join(lines)[:RECORD_CHARS]
    return {"id": "P%d" % sheet.id, "kind": "product", "ref": sheet.id, "title": "%s (%s)" % (title, sheet.make or "-"),
            "name": title, "text": text}


def _boq_record(boq, product_names) -> dict:
    lines, total, priced = [], 0.0, True
    count = 0
    for floor in boq.floors:
        for room in floor.rooms:
            for item in room.items:
                count += 1
                qty = _qty(item.qty)
                if qty is not None and item.price:
                    total += qty * float(item.price)
                elif qty is None:
                    priced = False
                if len(lines) < 25:
                    name = " ".join(str(item.description or "").split("\n")[0].split())[:70]
                    # A line linked to a product says which, when its own words do not: people
                    # retype descriptions, and "which BOQs used X" is asked by the product's name.
                    linked = product_names.get(item.specsheet_id, "")
                    if linked and linked.lower() not in name.lower():
                        name = "%s [product %s]" % (name, linked)
                    lines.append("%s ×%s %s @ %s (%s > %s)" % (
                        name or "unnamed line", item.qty or "1", item.unit or "", money(item.price, boq.currency) if item.price else "no price",
                        floor.name, room.name))
    who = (boq.created_by.email.split("@")[0] if getattr(boq, "created_by", None) and boq.created_by.email else "")
    head = "B%d · BOQ \"%s\"%s%s%s%s · %d floor(s), %d line(s)" % (
        boq.id, boq.project_name, (" · client " + boq.client) if boq.client else "",
        (" · code " + boq.project_code) if boq.project_code else "", (" · dated " + boq.boq_date) if boq.boq_date else "",
        (" · made by " + who) if who else "", len(boq.floors), count)
    money_line = "total before discount %s%s%s" % (money(total, boq.currency), "" if priced else " (some quantities not numbers)",
                                                    (" · standard discount %g%%" % boq.discount_percent) if boq.discount_percent else "")
    text = "\n".join([head, money_line, "lines: " + "; ".join(lines) + (" ..." if count > len(lines) else "")])[:RECORD_CHARS]
    return {"id": "B%d" % boq.id, "kind": "boq", "ref": boq.id, "title": boq.project_name, "name": boq.project_name, "text": text}


def _usage_line(entries) -> str:
    """Where a product is used, worked out from the BOQ lines linked to it - complete, which a
    handful of retrieved BOQs could never be ("which BOQs used NYX -Midi" is 21 BOQs on the local
    copy, and a question is given 6)."""
    by_boq = {}
    for boq_id, name, qty, price, currency in entries:
        seen = by_boq.setdefault(boq_id, [name, 0.0, set(), currency])
        seen[1] += qty or 0
        if price:
            seen[2].add(price)
    parts = ["B%d \"%s\" ×%g @ %s" % (bid, name[:40], qty, " / ".join(money(p, cur) for p in sorted(prices)) or "no price")
             for bid, (name, qty, prices, cur) in sorted(by_boq.items())]
    shown = parts[:12]
    return "used in %d BOQ(s): %s%s" % (len(by_boq), "; ".join(shown),
                                        "; and %d more BOQ(s)" % (len(parts) - len(shown)) if len(parts) > len(shown) else "")


class _Index:
    """The records of one tool and their word statistics, for BM25."""

    def __init__(self, records, usage=None):
        self.records = records
        self.by_id = {r["id"]: r for r in records}
        self.usage = usage or {}                     # product record id -> its "used in" line
        self.counts = [Counter(_tokens(r["text"])) for r in records]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = (sum(self.lengths) / len(self.lengths)) if records else 1.0
        self.df = Counter()
        for c in self.counts:
            self.df.update(c.keys())
        # A product's first name word is its series ("NYX", "EMBER"): named in a question, it says
        # more than any count of shared words (the same lesson as boq_ai.shortlist). And a whole
        # name written out in the question ("NYX -Midi", a project's name) says more again: without
        # it, "NYX -Midi" ranked below "NYX Square -Midi", which shares every word of it.
        self.series = [(_tokens(r["name"])[:1] or [""])[0] if r["kind"] == "product" else "" for r in records]
        self.names = [tuple(_tokens(r["name"])) for r in records]

    def search(self, question, kinds, limit):
        said = _tokens(question)
        words = list(dict.fromkeys(said))
        n = len(self.records)
        scored = []
        for i, record in enumerate(self.records):
            if record["kind"] not in kinds:
                continue
            counts, score = self.counts[i], 0.0
            for word in words:
                tf = counts.get(word)
                if not tf:
                    continue
                idf = math.log(1 + (n - self.df[word] + 0.5) / (self.df[word] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * self.lengths[i] / self.average))
            if self.series[i] and len(self.series[i]) >= 3 and self.series[i] in words:
                score += 3.0
            name = self.names[i]
            if name and (len(name) > 1 or len(name[0]) >= 3) and any(
                    tuple(said[k:k + len(name)]) == name for k in range(len(said) - len(name) + 1)):
                score += 4.0
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return [(score, self.records[i]) for score, i in scored[:limit]]


_CACHE = {}
_CACHE_LOCK = threading.Lock()


def index_for(db, discipline) -> _Index:
    """The tool's records, rebuilt at most every INDEX_SECONDS: a product approved or a BOQ saved
    a minute ago is found; asking ten questions does not read the whole catalogue ten times."""
    with _CACHE_LOCK:
        built = _CACHE.get(discipline)
        if built and time.time() - built[0] < INDEX_SECONDS:
            return built[1]
        products = (db.query(models.Specsheet)
                    .filter(models.Specsheet.discipline == discipline, models.Specsheet.approval_status == "approved")
                    .order_by(models.Specsheet.id).all())
        boqs = (db.query(models.BOQ)
                .options(selectinload(models.BOQ.floors).selectinload(models.BOQFloor.rooms).selectinload(models.BOQRoom.items),
                         selectinload(models.BOQ.created_by))
                .filter(models.BOQ.discipline == discipline).order_by(models.BOQ.id).all())
        names = {s.id: " ".join(str(s.product_name or "").split()) for s in products}
        used = {}
        for b in boqs:
            for floor in b.floors:
                for room in floor.rooms:
                    for item in room.items:
                        if item.specsheet_id in names:
                            used.setdefault("P%d" % item.specsheet_id, []).append(
                                (b.id, b.project_name, _qty(item.qty), float(item.price or 0), b.currency))
        # Products sharing a name (NYX -Mini With Trim and Trimless) are often on the same BOQs. Asked
        # about "NYX -Mini", the real model added its two lines' counts (9 + 4) and said 13 BOQs; it
        # is 9. So the count across the name is worked out here, and the model has nothing to add up.
        siblings = {}
        for s in products:
            siblings.setdefault(names[s.id].lower(), []).append("P%d" % s.id)
        lines = {}
        for pid, entries in used.items():
            line = _usage_line(entries)
            name = names[int(pid[1:])]
            family = siblings.get(name.lower(), [])
            if len(family) > 1:
                together = {e[0] for sib in family for e in used.get(sib, [])}
                line += "; all %d products named \"%s\" together are on %d BOQ(s)" % (len(family), name, len(together))
            lines[pid] = line
        index = _Index([_product_record(s) for s in products] + [_boq_record(b, names) for b in boqs], lines)
        _CACHE[discipline] = (time.time(), index)
        return index


# --- the allowance -----------------------------------------------------------------------------

def budget() -> dict:
    """What the page shows: the day's allowance on this server, what is used, and how many
    questions are left before the share kept for reading files."""
    cap = llm_extract.daily_cap()
    used = llm_extract.requests_today()
    ask_cap = max(1, int(cap * ASK_SHARE))
    return {"available": llm_extract.is_enabled(), "per_day": cap, "used": used,
            "questions_left": max(0, ask_cap - used), "questions_per_day": ask_cap,
            "plan": llm_extract.plan().get("note", "")}


# --- answering -----------------------------------------------------------------------------------

_AMOUNT = re.compile(r"(?:₹|\brs\.?|\binr|\busd|\beur|\bgbp)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)


_SPEC = re.compile(r"(?<![\d.])([0-9][0-9,]*(?:\.[0-9]+)?)\s*(lumens?|lm|mm|watts?|w|k)\b", re.I)
_SPEC_UNITS = {"lumen": "lm", "lumens": "lm", "watt": "w", "watts": "w"}


def _plain_number(text):
    digits = str(text).replace(",", "")
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
    return digits


def ask(db, question, discipline, can_see_boqs) -> dict:
    """{"answer", "sources", "notes", "asked_model", "model", "budget"}, or raises ValueError with
    a reason a person can read when the question cannot be answered at all."""
    question = " ".join(str(question or "").split())[:QUESTION_CHARS]
    if len(_tokens(question)) == 0:
        raise ValueError("Ask about a product, a specification (like 12W 3000K IP65) or a BOQ.")
    # Finding products by what they are needs no model and uses none of the day's allowance, and it
    # goes first so it still works with the model off or the allowance spent.
    catalogue = catalogue_query.catalogue_for(db, discipline)
    reading = catalogue_query.read_request(question, catalogue)
    if (catalogue_query.could_use_model(question, reading) and llm_extract.is_enabled()
            and budget()["questions_left"] > 0):
        llm_extract.LAST_ERROR = ""
        reading = catalogue_query.merge_reading(reading, catalogue_query.interpret(question, catalogue))
    if catalogue_query.wants_catalogue(question, reading):
        return {**catalogue_query.answer(reading, catalogue), "budget": budget()}
    if not llm_extract.is_enabled():
        raise ValueError("The AI model is not switched on on this server, so only searches by specification "
                         "(like 12W 3000K IP65) can be answered.")
    index = index_for(db, discipline)
    kinds = {"product", "boq"} if can_see_boqs else {"product"}
    # The best of each kind, then most relevant first whatever its kind: the context has room for
    # only so much, and twelve products added ahead of every BOQ once left out the very BOQ a
    # question named.
    scored = index.search(question, {"product"}, CONTEXT_PRODUCTS)
    if can_see_boqs:
        scored += index.search(question, {"boq"}, CONTEXT_BOQS)
    found = [record for _score, record in sorted(scored, key=lambda pair: -pair[0])]
    if not found:
        return {"answer": "Nothing in the %s matches those words. Try a product's name, a "
                          "specification such as 12W 3000K IP65, or a project's name." % (
                              "catalogue or the BOQs" if "boq" in kinds else "catalogue"),
                "sources": [], "notes": [], "asked_model": False, "model": "", "budget": budget()}
    plan = budget()
    if plan["questions_left"] <= 0:
        raise ValueError("Today's questions are used up (%d of the day's %d AI requests; the rest are kept "
                         "for reading files). They renew at 05:30 IST." % (plan["questions_per_day"], plan["per_day"]))
    context, given = [], {}
    for record in found:
        text = record["text"]
        if can_see_boqs and record["id"] in index.usage:
            text += "\n" + index.usage[record["id"]]
        if sum(len(t) for t in context) + len(text) > CONTEXT_CHARS:
            break
        context.append(text)
        given[record["id"]] = record
    # A BOQ named in a product's "used in" line was given too - by that line.
    for bid in re.findall(r"\bB\d+\b", "\n".join(context)):
        if bid in index.by_id and can_see_boqs:
            given.setdefault(bid, index.by_id[bid])
    llm_extract.LAST_ERROR = ""
    content = llm_extract.chat(SYSTEM_PROMPT, "QUESTION: %s\n\nRECORDS:\n\n%s" % (question, "\n\n".join(context)),
                               ANSWER_TOKENS, 60)
    reply = llm_extract._parse_json_object(content) if content else None
    if not reply or not str(reply.get("answer") or "").strip():
        raise ValueError("The AI model could not answer: %s." % (
            llm_extract.last_error() or "its answer was not in the form asked for"))
    answer = str(reply["answer"]).strip()[:2000]
    notes = []
    cited = [str(s).strip().upper() for s in (reply.get("sources") or []) if isinstance(s, (str, int))]
    sources = [given[s] for s in dict.fromkeys(cited) if s in given]
    if len(sources) < len(set(cited)):
        notes.append("The model cited a record it was not given; it is left out.")
    # Every amount of money the answer names should be in a record it was given - or be a sum it
    # worked out, which rule 3 asks it to say. Either way a person checks it before using it.
    held = {_plain_number(n) for t in context for n in re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", t)}
    strays = [m.group(0) for m in _AMOUNT.finditer(answer) if _plain_number(m.group(1)) not in held]
    if strays:
        notes.append("%s is not in the records the model was given - a sum it worked out, or a mistake. "
                     "Check it." % ", ".join(dict.fromkeys(strays)))
    # The same check for the values a designer specifies on: a figure with its unit that no record
    # holds is either worked out or wrong, and either way it is not the catalogue's.
    joined = " ".join(context).lower()
    stray_specs = []
    for number, unit in _SPEC.findall(answer):
        short = _SPEC_UNITS.get(unit.lower(), unit.lower())
        if not re.search(r"(?<![\d.])" + re.escape(_plain_number(number)) + r"\s*" + short + r"\b", joined.replace(",", "")):
            stray_specs.append("%s %s" % (number, unit))
    if stray_specs:
        notes.append("%s is not in the records the model was given. Check it against the spec sheet." % ", ".join(dict.fromkeys(stray_specs)))
    return {"answer": answer,
            "sources": [{"id": s["id"], "kind": s["kind"], "ref": s["ref"], "title": s["title"]} for s in sources],
            "notes": notes, "asked_model": True, "model": llm_extract.LAST_MODEL, "budget": budget(),
            "records_given": len(given)}
