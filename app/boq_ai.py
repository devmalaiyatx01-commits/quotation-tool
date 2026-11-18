"""The AI model's two jobs in a BOQ import (branch FreeLLM, 28 Oct 2026): reading a file the rules
in app/boq_import.py cannot, and suggesting an NPD product for a line they could not link.

Both go through app/llm_extract.chat() - the one place the app talks to a model, with its free-only
models, its "providers that do not collect data" rule, its daily allowance and its fallbacks.

What the model may and may not do
---------------------------------
It READS and PROPOSES; it never decides. A line it read is marked so, and counts as "to check" in
the preview until a person confirms it. A product it suggests must be one of the few the app
shortlisted for that line - an id it invents is thrown away - and is also only a "check". Nothing it
returns reaches a BOQ until a person presses Create, and the create endpoint re-checks every product
id as it does for any import.

It never invents. The prompts forbid it, and the reading is held to the file: a line whose
description is not in the file's own text is dropped (a model can "tidy" text it was asked to copy).

When it cannot help - no key, the day's allowance used, a timeout, an answer that is not JSON - the
import carries on exactly as it does without a model, and the preview says why.
"""
import difflib
import io
import json
import re

from app import boq_import, llm_extract

MAX_DOCUMENT_CHARS = 120_000            # about 40 pages of text - a whole BOQ, a few model calls
CHUNK_CHARS = 14_000
# One part's answer must fit in READ_MAX_TOKENS whole, or it is refused as cut off. Measured with the
# real free model (29 Oct 2026): about 80 tokens a second, and an answer about as long as the text
# it reads once empty fields are left out.
READ_MAX_TOKENS = 16000
READ_TIMEOUT_SECONDS = 180
SUGGEST_BATCH = 15
SHORTLIST = 8

READ_PROMPT = (
    "You read a lighting BOQ or quotation (bill of quantities) that was exported to text from a "
    "spreadsheet or a PDF. Return ONLY a JSON object:\n"
    '{"project": {"project_name": "", "client": "", "project_code": "", "area": ""}, '
    '"lines": [{"floor": "", "area": "", "sno": "", "ref": "", "description": "", "make": "", '
    '"model_no": "", "finish": "", "wattage": "", "cct": "", "beam": "", "control": "", "qty": "", '
    '"unit": "", "price": "", "discount": "", "is_accessory": false}]}\n'
    "\n"
    "Rules:\n"
    "1. One entry per line item (a luminaire, an accessory, a driver), in the document's order.\n"
    "2. Copy every value EXACTLY as written in the document. Never invent, infer, translate or "
    "tidy a value. Leave a field OUT of the line when the document does not state it (no empty "
    "strings), and send is_accessory only when it is true.\n"
    "3. `description` is the item's description exactly as written, joined into one line if it "
    "wraps. Do not add the make, the quantity or anything else to it.\n"
    "4. `floor` and `area` are the headings the line sits under (e.g. a floor title, then a room "
    "name), or the line's own floor/area/location column. Repeat them on every line they cover. "
    "When the document has only one level of headings (rooms, areas, locations), they are `area`, "
    "and `floor` is left out.\n"
    "5. `price` is the unit price (before any discount), `discount` the discount percentage, "
    "both as written. Not the line total.\n"
    "6. `is_accessory` is true only for accessories, drivers, power supplies and parts that "
    "belong to a luminaire listed above them.\n"
    "7. Leave out totals, sub-totals, taxes, column headings, terms, notes and addresses.\n"
    "8. `project` holds only what the document itself labels as the project (name), the client, "
    "the project code and the area or scope, copied exactly. Leave out what it does not label. A "
    "quotation, invoice or reference number is not a project code.\n"
    "Return the JSON object and nothing else."
)

SUGGEST_PROMPT = (
    "You match lines of a lighting BOQ to products in a catalogue. For each line you are given "
    "a short list of candidate products (id, name, category, make, what it offers). Return ONLY "
    "a JSON object:\n"
    '{"choices": [{"line": 1, "product_id": 123, "reason": "same product name and wattage"}]}\n'
    "\n"
    "Rules:\n"
    "1. Choose a product ONLY if the line clearly describes that product - its name, model or "
    "code, or a description that fits it and no other candidate. Otherwise product_id is null.\n"
    "2. product_id must be one of THAT line's candidate ids. Never any other number.\n"
    "3. A wrong match is worse than none: when in doubt, null.\n"
    "4. `reason` is a few plain words a lighting designer can check.\n"
    "Return the JSON object and nothing else."
)


def available() -> bool:
    return llm_extract.is_enabled()


def status() -> dict:
    """What the page shows about the model: whether it is there, through which service, and
    how much of today's allowance this server has used."""
    return {"available": available(), "provider": llm_extract.provider(),
            "requests_today": llm_extract.requests_today(), "daily_cap": llm_extract.daily_cap()}


# --- reading a file the rules cannot -----------------------------------------------------------

def document_text(filename: str, data: bytes) -> str:
    """The file as plain text, a line per table row, for the model to read. A PDF's own text layer
    (a scanned PDF has none, and is refused with that reason); a spreadsheet's cells, "|" between."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        import fitz
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise boq_import.SheetError("This PDF could not be opened (%s)." % type(exc).__name__)
        if doc.page_count > 60:
            raise boq_import.SheetError("A PDF of %d pages is more than a BOQ - split it." % doc.page_count)
        text = "\n".join("## Page %d\n%s" % (i + 1, page.get_text()) for i, page in enumerate(doc))
        if not text.replace("## Page", "").strip(" \n0123456789"):
            raise boq_import.SheetError("This PDF has no text in it (a scan) - import the Excel version.")
        return text[:MAX_DOCUMENT_CHARS]
    if name.endswith(".csv"):
        return data.decode("utf-8-sig", errors="replace")[:MAX_DOCUMENT_CHARS]
    book = boq_import._open(filename, data)       # the same size and zip guards as the rule reader
    try:
        parts = []
        for ws in book.worksheets:
            parts.append("## Sheet: %s" % ws.title)
            for row in ws.iter_rows(max_row=boq_import.MAX_ROWS_PER_SHEET, max_col=boq_import.MAX_COLS,
                                    values_only=True):
                cells = [boq_import._line_text(v) for v in row if v is not None and boq_import._line_text(v)]
                if cells:
                    parts.append(" | ".join(cells))
            if sum(len(p) for p in parts) > MAX_DOCUMENT_CHARS:
                break
        return "\n".join(parts)[:MAX_DOCUMENT_CHARS]
    finally:
        book.close()


def _chunks(text: str):
    """Slices of whole lines - a table row is one line, so no item is cut in two."""
    chunk, size = [], 0
    for line in text.split("\n"):
        if size + len(line) > CHUNK_CHARS and chunk:
            yield "\n".join(chunk)
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        yield "\n".join(chunk)


def _folded(text):
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text or "").lower()).split())


def read_with_model(filename: str, data: bytes) -> dict:
    """The file read by the model into the same shape app/boq_import.read_workbook returns, or a
    SheetError saying why it could not be."""
    text = document_text(filename, data)
    if not text.strip():
        raise boq_import.SheetError("There is no text in this file to read.")
    folded_doc = _folded(text)
    result = {"project": {}, "floors": [], "skipped": [], "sheets": [], "discount_percent": None,
              "notes_text": None, "columns": {}, "read_by": "ai"}
    floors = {}
    last_place = ("", "")
    parts = list(_chunks(text))
    failed = []
    for number, chunk in enumerate(parts, 1):
        content = llm_extract.chat(READ_PROMPT, chunk, READ_MAX_TOKENS, READ_TIMEOUT_SECONDS)
        data_out = llm_extract._parse_json_object(content) if content else None
        if data_out is None:
            failed.append((number, chunk, llm_extract.last_error() if not content
                           else "its answer was not in the form asked for (JSON)"))
            continue
        # The cover's details, held to the file like every line: kept only if the file says them.
        cover = data_out.get("project") if isinstance(data_out.get("project"), dict) else {}
        for key in ("project_name", "client", "project_code", "area"):
            value = boq_import._line_text(cover.get(key)).strip(" :")[:300]
            if value and not result["project"].get(key) and _folded(value) and _folded(value) in folded_doc:
                result["project"][key] = value
        for entry in data_out.get("lines") or []:
            if not isinstance(entry, dict):
                continue
            description = boq_import._long_text(entry.get("description"))
            if not description:
                continue
            # Held to the file: a description the file does not contain was made up or rewritten.
            # The one addition forgiven is the line's own make in front ("InHouse EMBER -Mini..."),
            # which models add however they are asked; the make is still the file's own word.
            said = _folded(description.split("\n")[0])
            make = _folded(entry.get("make"))
            if make and said.startswith(make + " ") and make in folded_doc:
                said = said[len(make) + 1:]
                raw_make = str(entry.get("make")).strip()
                if description.strip().lower().startswith(raw_make.lower()):
                    description = description.strip()[len(raw_make):].strip(" -,:")
            if said[:60] not in folded_doc:
                result["skipped"].append({"sheet": "", "row": None, "text": description[:120],
                                          "why": "The AI model returned this, but the file does not say it - left out."})
                continue
            floor_name = boq_import._line_text(entry.get("floor"))
            area_name = boq_import._line_text(entry.get("area"))
            # Settled here, not left to the model, which did both of these on a real quotation:
            # a line sent with no heading belongs under the one before it in reading order (a
            # driver listed under its downlight), and one level of headings put in both places
            # ("LIVING ROOM > LIVING ROOM") is an area.
            if not floor_name and not area_name:
                floor_name, area_name = last_place
            if floor_name and area_name and floor_name.casefold() == area_name.casefold():
                floor_name = ""
            last_place = (floor_name, area_name)
            floor_name = floor_name or "Imported"
            area_name = area_name or "General"
            floor = floors.get(floor_name.casefold())
            if floor is None:
                floor = floors[floor_name.casefold()] = {"name": floor_name, "areas": [], "_areas": {}}
                result["floors"].append(floor)
            area = floor["_areas"].get(area_name.casefold())
            if area is None:
                area = floor["_areas"][area_name.casefold()] = {"name": area_name, "remark": "", "rows": []}
                floor["areas"].append(area)
            line = {"sheet": "", "row": None, "notes": ["Read by the AI model - check it against the file."]}
            for key, source in (("item_code", "sno"), ("reference_number", "ref"), ("model_no", "model_no"),
                                ("dimensions", None), ("finish", "finish"), ("wattage", "wattage"),
                                ("beam", "beam"), ("control", "control"), ("led_color", "cct"),
                                ("make", "make"), ("qty", "qty"), ("unit", "unit"), ("remarks", None)):
                line[key] = boq_import._line_text(entry.get(source))[:200] if source else ""
            line["description"] = description[:boq_import.FIELD_LIMITS["description"]]
            line["unit"] = line["unit"] or "Nos."
            if not line["qty"]:
                line["qty"] = "1"
                line["notes"].append("The quantity was blank - 1 is assumed.")
            price = boq_import._number(entry.get("price"))
            line["price"] = price if price is not None and price >= 0 else None
            rate = boq_import._number(entry.get("discount"))
            line["discount"] = rate if rate is not None and 0 <= rate <= 100 else None
            line["net_price"] = None
            line["is_accessory"] = bool(entry.get("is_accessory"))
            line["is_driver"] = bool(boq_import.text_names_driver(description.split("\n")[0]))
            line["is_accessory"] = line["is_accessory"] or line["is_driver"]
            if not line["led_color"]:
                line["led_color"] = boq_import._cct_from(description)
            area["rows"].append(line)
    for floor in result["floors"]:
        floor.pop("_areas", None)
    if not any(a["rows"] for f in result["floors"] for a in f["areas"]):
        reason = failed[-1][2] if failed else llm_extract.last_error()
        raise boq_import.SheetError("The AI model could not read lines from this file%s." %
                                    (" - " + reason if reason else ""))
    # A part the model could not read is said out loud: the lines it holds are missing from the
    # preview, and a BOQ that is quietly short is worse than one refused.
    for number, chunk, reason in failed:
        first = next((line.strip() for line in chunk.split("\n") if line.strip() and not line.startswith("## ")), "")
        result["skipped"].append({"sheet": "", "row": None, "text": first[:120],
                                  "why": "Part %d of %d of the file (starting here) could not be read by the AI model "
                                         "(%s) - its lines are MISSING. Check against the file, or read it again."
                                         % (number, len(parts), reason or "no answer")})
    result["sheets"].append({"name": filename or "the file", "use": "Read by the AI model (%d request%s)"
                             % (len(parts), "" if len(parts) == 1 else "s")})
    boq_import._settle_discounts(result)
    boq_import._blank_running_numbers(result)
    return result


# --- suggesting a product for a line the rules could not link ----------------------------------

def _offer(sheet) -> str:
    """What a product offers, briefly - enough for the model to tell candidates apart."""
    fields = sheet.fields or {}
    bits = []
    for key in ("wattage", "cct", "beam_angle", "finish", "ip_rating", "mounting"):
        value = fields.get(key)
        if value:
            bits.append("%s %s" % (key.replace("_", " "), str(value)[:60]))
    return "; ".join(bits)[:240]


def shortlist(catalogue, line, size=SHORTLIST):
    """The products most like the line by name - the only ones the model may choose from.

    A shared word counts by how rare it is among product names: "recessed" and "downlight" are in
    hundreds of them and say little, "NYX" is in a handful and says almost everything. Counted
    plainly, a line saying "downlight NYX style" shortlisted eight recessed downlights and not NYX."""
    import math
    text = boq_import.norm((line.get("description") or "").split("\n")[0] + " " + (line.get("model_no") or ""))
    if not text:
        return []
    words = set(w for w in text.split() if len(w) >= 3)
    total = max(1, len(catalogue.sheets))
    weight = {w: math.log(1 + total / len(catalogue.by_token[w])) for w in words if catalogue.by_token.get(w)}
    if not weight:
        return []
    candidates = {}
    for w in weight:
        for sheet in catalogue.by_token[w]:
            candidates[sheet.id] = sheet
    scored = []
    for sheet in candidates.values():
        name = boq_import.norm(sheet.product_name).split()
        name_words = set(name)
        score = sum(weight[w] for w in weight if w in name_words)
        # A product's first word is nearly always its series (NYX, EMBER, Vena): on the line, it
        # outweighs any pair of descriptive words ("recessed downlight") another product shares.
        if name and len(name[0]) >= 3 and name[0] in words:
            score += 4.0
        score += 0.5 * difflib.SequenceMatcher(None, text[:80], boq_import.norm(sheet.product_name)).ratio()
        scored.append((score, sheet))
    scored.sort(key=lambda x: (-x[0], -x[1].id))
    return [s for _score, s in scored[:size]]


def suggest_products(catalogue, lines) -> int:
    """For each line the rules left unlinked, asks the model to pick from its shortlist. Sets the
    line's match to a "check" with the model's reason; returns how many it suggested."""
    todo = []
    for line in lines:
        if line.get("is_accessory") or (line.get("match") or {}).get("product"):
            continue
        candidates = shortlist(catalogue, line)
        if candidates:
            todo.append((line, candidates))
    suggested = 0
    for start in range(0, len(todo), SUGGEST_BATCH):
        batch = todo[start:start + SUGGEST_BATCH]
        parts = []
        for n, (line, candidates) in enumerate(batch, 1):
            specs = ", ".join(v for v in (line.get("make"), line.get("finish"), line.get("wattage"),
                                          line.get("led_color"), line.get("beam")) if v)
            parts.append("### Line %d\n%s%s\nCandidates:\n%s" % (
                n, (line.get("description") or "")[:600], ("\n(" + specs + ")") if specs else "",
                "\n".join("- id %d | %s | %s | %s | %s" % (s.id, s.product_name or "", s.category or "",
                                                         s.make or "", _offer(s)) for s in candidates)))
        content = llm_extract.chat(SUGGEST_PROMPT, "\n\n".join(parts), 120 * len(batch) + 200, 60)
        if not content:
            break                      # the allowance or the service is gone: stop asking
        data = llm_extract._parse_json_object(content) or {}
        for choice in data.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            try:
                n = int(choice.get("line")) - 1
                pid = int(choice.get("product_id")) if choice.get("product_id") is not None else None
            except (TypeError, ValueError):
                continue
            if not (0 <= n < len(batch)) or pid is None:
                continue
            line, candidates = batch[n]
            picked = next((s for s in candidates if s.id == pid), None)
            if picked is None:
                continue               # not one of the shortlist: thrown away, never trusted
            reason = " ".join(str(choice.get("reason") or "").split())[:160]
            line["match"] = {
                "status": "check",
                "product": catalogue.summary(picked, "Suggested by the AI model%s - check it." %
                                             (": " + reason if reason else "")),
                "alternatives": [catalogue.summary(s, "A similar name.") for s in candidates if s.id != pid][:5],
            }
            suggested += 1
    return suggested
