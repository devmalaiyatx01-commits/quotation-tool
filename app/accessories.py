"""The accessory catalogue: every accessory the library knows, and the products it goes with.

Why this is derived, not a table of its own
-------------------------------------------
Which accessories exist has never been typed in anywhere. It is already recorded, per product,
in three places that are each the right place for it:

  * `Specsheet.options["accessories"]` (or the parsed `fields["accessories"]`) - the accessories
    that product's own datasheet lists. This is the association: "Honeycomb goes with this
    downlight" is a fact about the downlight, printed on its datasheet.
  * a `PriceEntry` scoped to that specsheet whose config carries an `accessory` key - what that
    accessory costs *under that product*. The same Snoot is a different price under a different
    fixture, which is why the price belongs to the pair and not to the accessory.
  * `Specsheet.params["Accessory codes (Exenia)"]` - the manufacturer's own order code for it.

A second table listing accessories would have to be kept in step with all three, and the day it
drifts, the app quotes something the datasheet does not offer. So the catalogue is built from
those on request instead: it can never disagree with them, and uploading a specsheet adds its
accessories to it with no further step.

What this module adds on top is the thing nobody could see before: the view from the accessory's
side. "Honeycomb" is listed by 284 products across two brands, and until now the only way to
learn that was to open 284 specsheets.

Names that are the same thing
-----------------------------
Datasheets spell the same accessory differently - HONEYCOMB, Honeycomb; SNOOT, Snoot; Elliptical
Lens, Elliptical lens. Those are one accessory, so they are grouped on a key that ignores case,
punctuation and spacing, and the group is shown under the spelling most of its products use.
Nothing is renamed in the database: each product keeps the words its own datasheet printed, which
is what its BOQ line should say.

Spelling differences that are not just punctuation - "louver" and "louvre" - are left as separate
entries on purpose. Merging them would be this module deciding two words mean the same thing,
which is a judgement about the products, not about the text.

InHouse is not in here
----------------------
Grouping by name has a limit no spelling rule can fix: two products can list the same name and
take different physical accessories, or take the very same one. InHouse's accessories are therefore
a library of records that products pick from (app/accessory_parts.py, PART_BRANDS), and its
products are left out of everything in this module - the catalogue, renaming a spelling, the tidy
plan, the spelling report. This module is the by-name view of the other brands' datasheets.
"""
import re
import time
from collections import Counter, defaultdict

from sqlalchemy.orm import load_only

from app import models
from app.product_codes import NOT_APPLICABLE


def fold(name: str) -> str:
    """The key two spellings of one accessory share: letters and digits only, lower case."""
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


# An "accessory" longer than this is a sentence the parser picked up ("Bluetooth accessory to be
# used in combination with a Dali driver"), not a name anyone would order by.
MAX_NAME = 60


def is_name(text) -> bool:
    """Whether this reads as an accessory's name rather than parsing debris."""
    name = str(text or "").strip()
    return bool(name) and len(name) <= MAX_NAME and bool(fold(name))


def split_options(raw) -> list:
    """A datasheet's accessories field into separate names.

    Splits on "/" and on commas, except a comma between digits - Exenia's "Connector 3x1,5mmq -
    2 outputs IP68" is one accessory with a decimal comma in it, and splitting there invented two
    that do not exist. The same rule main.py's _split_field_options uses, kept here so the
    catalogue does not depend on a private helper in the route file.
    """
    text = str(raw or "")
    if not text.strip():
        return []
    parts = re.split(r"\s*/\s*|\s*,(?!\d)\s*", text)
    return [p.strip() for p in parts if p.strip()]


def is_none(text) -> bool:
    """Whether this is a datasheet's way of saying "no accessory" - InHouse prints "NIL / HONEYCOMB
    / SPREAD LENS" to say a plain fixture is an option. It is not an accessory, and counting it as
    one put "NIL" near the top of the catalogue, listed by 117 products."""
    return str(text or "").strip().upper() in NOT_APPLICABLE


def _names_of(sheet) -> list:
    """The accessories one specsheet lists, from its options first and its parsed field after.

    `options` already holds them as a list, so it is trusted whenever it has one; the raw field
    is only split when it does not.
    """
    listed = [a for a in (sheet.options or {}).get("accessories") or [] if str(a).strip()]
    if not listed:
        listed = split_options((sheet.fields or {}).get("accessories"))
    return [n for n in listed if is_name(n) and not is_none(n)]


def build(db, discipline=None) -> dict:
    """The whole catalogue: {accessories: [...], count} , heaviest first.

    Each entry is one accessory, with every product that lists it, that product's own price for
    it where one is recorded, and its order code where the manufacturer gave one.
    """
    # Only the columns the catalogue reads. A Specsheet carries several large JSON blobs
    # (raw_text, parsed pages) that would otherwise be loaded 1,300 times to be thrown away.
    from app import accessory_parts       # imported here: it imports this module
    query = db.query(models.Specsheet).options(load_only(
        models.Specsheet.id, models.Specsheet.product_name, models.Specsheet.filename,
        models.Specsheet.make, models.Specsheet.series, models.Specsheet.family,
        models.Specsheet.typology, models.Specsheet.options, models.Specsheet.fields,
        models.Specsheet.params))
    if discipline:
        query = query.filter(models.Specsheet.discipline == discipline)
    sheets = query.all()

    # {specsheet id: {accessory name: price}} - a price belongs to the pair, never to the
    # accessory alone, because the same part costs differently under different fixtures.
    priced = defaultdict(dict)
    for entry in db.query(models.PriceEntry).options(load_only(
            models.PriceEntry.id, models.PriceEntry.specsheet_id,
            models.PriceEntry.config, models.PriceEntry.price)).all():
        config = entry.config or {}
        name = config.get("accessory")
        if name and entry.specsheet_id and entry.price is not None:
            priced[entry.specsheet_id][fold(name)] = float(entry.price)

    groups = defaultdict(lambda: {"spellings": Counter(), "products": []})
    for sheet in sheets:
        # InHouse's accessories are its accessory library, not names (app/accessory_parts.py).
        if accessory_parts.is_part_brand(sheet.make):
            continue
        codes = (sheet.params or {}).get("Accessory codes (Exenia)") or {}
        for name in _names_of(sheet):
            key = fold(name)
            group = groups[key]
            group["spellings"][name] += 1
            group["products"].append({
                "id": sheet.id,
                "name": sheet.product_name or sheet.filename or "",
                "make": sheet.make or "",
                "family": sheet.family or "",
                "typology": sheet.typology or "",
                "as_written": name,
                "price": priced.get(sheet.id, {}).get(key),
                "code": codes.get(name) if isinstance(codes.get(name), str) else None,
            })

    out = []
    for key, group in groups.items():
        products = sorted(group["products"], key=lambda p: (p["make"], p["name"]))
        prices = [p["price"] for p in products if p["price"] is not None]
        spellings = group["spellings"]
        out.append({
            "key": key,
            "name": _display_name(spellings),
            "spellings": [s for s, _n in spellings.most_common()],
            "makes": sorted({p["make"] for p in products if p["make"]}),
            "typologies": sorted({p["typology"] for p in products if p["typology"]}),
            "count": len(products),
            "priced": len(prices),
            "price_low": min(prices) if prices else None,
            "price_high": max(prices) if prices else None,
            "products": products,
        })
    out.sort(key=lambda a: (-a["count"], a["name"].upper()))
    return {"accessories": out, "count": len(out)}


# The catalogue, kept for a minute. Building it reads every specsheet in the library (~230ms),
# and the screens that use it ask for it again on every search keystroke. What can be up to a
# minute stale here is only how many OTHER products share an accessory - a product's own list is
# always read live, from the product itself, wherever it actually decides anything.
_CACHE = {}
CACHE_SECONDS = 60


def build_cached(db, discipline=None) -> dict:
    now = time.time()
    hit = _CACHE.get(discipline)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = build(db, discipline)
    _CACHE[discipline] = (now, value)
    return value


def forget():
    """Drop the cache - called when a specsheet's accessories change."""
    _CACHE.clear()


def _display_name(spellings: Counter) -> str:
    """The spelling to show a group under: the one most of its products use.

    A tie goes to the one that is not shouting - datasheets that print everything in capitals
    would otherwise name every accessory in the catalogue.
    """
    top = spellings.most_common()
    best = max(top, key=lambda item: (item[1], not item[0].isupper(), -len(item[0])))
    return best[0]


def for_specsheet(db, sheet) -> list:
    """One product's accessories, each with what else in the library also lists it.

    This is the Specsheet tab's side of the same catalogue: standing on a product, the useful
    extra fact about "Honeycomb" is that 283 other products take one too.
    """
    from app import accessory_parts
    if accessory_parts.is_part_brand(sheet.make):
        return accessory_parts.for_specsheet(db, sheet)
    everything = build_cached(db)["accessories"]
    by_key = {a["key"]: a for a in everything}
    mine = []
    codes = (sheet.params or {}).get("Accessory codes (Exenia)") or {}
    for name in _names_of(sheet):
        group = by_key.get(fold(name))
        if not group:
            continue
        here = next((p for p in group["products"] if p["id"] == sheet.id), None)
        mine.append({
            "key": group["key"],
            "name": name,
            "catalogue_name": group["name"],
            "price": here["price"] if here else None,
            "code": codes.get(name) if isinstance(codes.get(name), str) else None,
            "also_on": group["count"] - 1,
            "makes": group["makes"],
        })
    return mine


# --- making one accessory's spelling consistent ------------------------------------------------
#
# An accessory's name is not only a label: it is the key that ties three things together. A
# PriceEntry finds its accessory by an exact string in its config, and a product's order codes are
# a dict keyed by that same string. So renaming the name in one place and not the others does not
# tidy the data, it breaks it - the price and the code stop resolving and the accessory silently
# goes back to costing nothing. Every rename here moves all of them together, or none of them.


def _rank(name: str) -> tuple:
    """How good a spelling is as the one to keep. Higher is better.

    Not simply the commonest: on this library the commonest spelling of three accessories is the
    one missing a space ("EXTENSIONARM" on 5 products against "EXTENSION ARM" on 2). A missing
    word boundary is a defect, never a house style, so more words wins first; then a spelling
    that is not shouting, since a datasheet printed entirely in capitals says nothing about how
    the part is named; and only then how many products use it.
    """
    words = len([w for w in re.split(r"[^0-9A-Za-z]+", name) if w])
    return (words, 0 if name.isupper() else 1, len(name))


def canonical_name(spellings) -> str:
    """The spelling to make a group consistent on, from {name: how many products}."""
    counts = spellings if isinstance(spellings, Counter) else Counter(spellings)
    return max(counts, key=lambda n: (_rank(n), counts[n], n))


def plan_tidy(db) -> list:
    """Every accessory spelled more than one way, with the spelling to settle on.

    Only groups that already fold to the same key - a difference of case, punctuation or spacing.
    Two names that differ by a real word ("louver" and "louvre") are NOT in here: whether those
    are one part is a judgement about the products, and belongs to a person, not to a rule.
    InHouse products are left out, as rename() leaves them (app/accessory_parts.py).
    """
    from app import accessory_parts
    groups = defaultdict(Counter)
    for sheet in db.query(models.Specsheet).options(load_only(
            models.Specsheet.id, models.Specsheet.make, models.Specsheet.options,
            models.Specsheet.fields)).all():
        if accessory_parts.is_part_brand(sheet.make):
            continue
        for name in _names_of(sheet):
            groups[fold(name)][name] += 1
    out = []
    for key, counts in groups.items():
        if len(counts) < 2:
            continue
        keep = canonical_name(counts)
        out.append({"key": key, "keep": keep,
                    "variants": [{"name": n, "products": c} for n, c in counts.most_common()],
                    "products": sum(c for n, c in counts.items() if n != keep)})
    out.sort(key=lambda g: -g["products"])
    return out


def rename(db, from_key: str, to_name: str) -> dict:
    """Every product listing this accessory, under any spelling, now lists it as `to_name`.

    Moves the four places a name lives together: the product's own accessory list, the raw parsed
    field when that is all the product has, the accessory's PriceEntry, and its order code. When
    `to_name` folds to a different key this is a merge - the two accessories become one - which is
    exactly the same operation.

    Returns what it touched. Writes nothing when `to_name` is not a usable name.

    InHouse products are left as they are, with their prices: their accessories are records in the
    accessory library (app/accessory_parts.py), renamed there.
    """
    from app import accessory_parts
    to_name = " ".join(str(to_name or "").split())
    if not is_name(to_name) or is_none(to_name):
        raise ValueError("That is not a usable accessory name.")
    keys = {from_key, fold(to_name)}
    touched = {"to": to_name, "products": 0, "prices": 0, "codes": 0, "was": Counter()}
    on_parts = {s.id for s in db.query(models.Specsheet).options(load_only(
        models.Specsheet.id, models.Specsheet.make)).all() if accessory_parts.is_part_brand(s.make)}

    for sheet in db.query(models.Specsheet).all():
        if sheet.id in on_parts:
            continue
        changed = False
        listed = [a for a in (sheet.options or {}).get("accessories") or [] if str(a).strip()]
        if not listed:
            # This product only has the raw parsed text. Promoting it to the structured list is
            # the fix: that list is what the app reads, and the raw field stays as the datasheet
            # printed it, which is the record of what was actually parsed.
            listed = split_options((sheet.fields or {}).get("accessories"))
            if not any(fold(n) in keys for n in listed):
                continue
            options = dict(sheet.options or {})
            options["accessories"] = listed
            sheet.options = options
        fixed, seen = [], set()
        for name in listed:
            if fold(name) in keys and name != to_name:
                touched["was"][name] += 1
                name, changed = to_name, True
            # A product that listed both spellings must not end up listing the same one twice.
            if fold(name) in keys:
                if to_name in seen:
                    changed = True
                    continue
                seen.add(to_name)
            fixed.append(name)
        if changed:
            options = dict(sheet.options or {})
            options["accessories"] = fixed
            sheet.options = options
            touched["products"] += 1

        codes = (sheet.params or {}).get("Accessory codes (Exenia)") or {}
        moved = {k: v for k, v in codes.items() if fold(k) in keys and k != to_name}
        if moved:
            params = dict(sheet.params or {})
            table = dict(codes)
            for old, code in moved.items():
                table.pop(old, None)
                table.setdefault(to_name, code)
            params["Accessory codes (Exenia)"] = table
            sheet.params = params
            touched["codes"] += len(moved)

    for entry in db.query(models.PriceEntry).all():
        if entry.accessory_part_id or entry.specsheet_id in on_parts:
            continue
        config = entry.config or {}
        name = config.get("accessory")
        if name and fold(name) in keys and name != to_name:
            entry.config = {**config, "accessory": to_name}
            touched["prices"] += 1

    # Its parameters and picture are keyed by the name, so they go with it - and a merge merges them.
    touched["details"] = _carry_details(db, from_key, to_name)
    touched["was"] = dict(touched["was"])
    return touched


# --- what a person defines about an accessory: its parameters and picture ----------------------
#
# Kept in models.AccessoryDetail, keyed by fold(name). The catalogue above stays derived from the
# specsheets; this is the one thing about an accessory no datasheet records, so it needs a home.

MAX_PARAMS = 6
MAX_PARAM_NAME = 40
MAX_OPTIONS = 40
MAX_OPTION = 60

# The only file names an accessory picture can have: letters and digits from the key (and the
# hyphen of a part's key, "part-12"), then a number. An export opens whatever path a BOQ line
# names, so a name is only ever used when it is exactly this shape - no folder, no "..", nothing a
# crafted request could point elsewhere.
PICTURE_FILE = re.compile(r"accessory_[a-z0-9-]+_\d+\.png")


def picture_file(name) -> str:
    """`name` when it is an accessory picture's file name, else ""."""
    name = str(name or "")
    return name if PICTURE_FILE.fullmatch(name) else ""


def line_picture(item) -> str:
    """The accessory picture a BOQ line was added with ("" for none). See models.AccessoryDetail."""
    return picture_file((getattr(item, "config", None) or {}).get("accessory_image"))


def clean_params(raw) -> list:
    """Parameters as saved: [{"name", "options"}], tidied, or a ValueError a person can act on.

    Spaces are collapsed, empty options dropped, and an option repeated in another case kept once.
    A row with neither a name nor options is an empty line in the form, and is ignored."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("Parameters must be a list.")
    out, seen = [], set()
    for p in raw:
        if not isinstance(p, dict) or not isinstance(p.get("options", []), list):
            raise ValueError("Each parameter needs a name and a list of options.")
        name = " ".join(str(p.get("name") or "").split())
        options, have = [], set()
        for o in p.get("options") or []:
            o = " ".join(str(o or "").split())
            if not o or o.lower() in have:
                continue
            if len(o) > MAX_OPTION:
                raise ValueError('"%s..." is longer than %d characters.' % (o[:24], MAX_OPTION))
            have.add(o.lower())
            options.append(o)
        if not name:
            if options:
                raise ValueError("Give every parameter a name.")
            continue
        if len(name) > MAX_PARAM_NAME:
            raise ValueError('"%s..." is longer than %d characters.' % (name[:24], MAX_PARAM_NAME))
        if name.lower() in seen:
            raise ValueError('"%s" is there twice - one parameter, one name.' % name)
        if len(options) > MAX_OPTIONS:
            raise ValueError('"%s" has %d options; %d is the most.' % (name, len(options), MAX_OPTIONS))
        seen.add(name.lower())
        out.append({"name": name, "options": options})
    if len(out) > MAX_PARAMS:
        raise ValueError("%d parameters is the most one accessory can have." % MAX_PARAMS)
    return out


def merge_params(keep, extra) -> list:
    """`keep`'s parameters, then any of `extra`'s it lacks; a parameter both have gains the other's
    options. What two accessories become when they are merged - nothing either defined is lost."""
    out = [{"name": p["name"], "options": list(p.get("options") or [])} for p in keep or []]
    by_name = {p["name"].lower(): p for p in out}
    for p in extra or []:
        mine = by_name.get(p["name"].lower())
        if mine is None:
            mine = {"name": p["name"], "options": list(p.get("options") or [])}
            out.append(mine)
            by_name[mine["name"].lower()] = mine
            continue
        have = {o.lower() for o in mine["options"]}
        mine["options"] += [o for o in p.get("options") or [] if o.lower() not in have]
    return out


def _carry_details(db, from_key: str, to_name: str) -> str:
    """Moves an accessory's parameters and picture to the name it is being given. Returns what
    happened: "" (nothing defined), "kept", "moved" or "merged".

    Same key: only the name it was saved under changes. A new key with nothing defined: the row
    moves there. A new key that has its own: the two are merged (merge_params), the target's
    picture kept and the moving one's taken only if the target has none. The picture files
    themselves stay where they are - BOQ lines may name them."""
    to_key = fold(to_name)
    src = db.get(models.AccessoryDetail, from_key)
    if src is None:
        return ""
    if to_key == from_key:
        src.name = to_name
        return "kept"
    dst = db.get(models.AccessoryDetail, to_key)
    if dst is None:
        db.add(models.AccessoryDetail(key=to_key, name=to_name, params=list(src.params or []),
                                      image=src.image or ""))
        db.delete(src)
        return "moved"
    dst.params = merge_params(dst.params, src.params)
    dst.image = dst.image or src.image or ""
    dst.name = to_name
    db.delete(src)
    return "merged"


def near_duplicates(entries: list) -> list:
    """Pairs of accessories that read like the same part spelled with a different word.

    "Honeycomb Louver" and "Honeycomb louvre" fold differently - louver and louvre are not the
    same letters - so nothing here merges them, and nothing should: whether they are one part is
    a judgement about the products. What this can honestly do is put the pair in front of someone
    who knows, which is the whole difference between a report and a guess.
    """
    import difflib
    out = []
    ranked = sorted(entries, key=lambda e: -e["count"])
    for i, a in enumerate(ranked):
        for b in ranked[i + 1:]:
            if abs(len(a["key"]) - len(b["key"])) > 4 or a["key"][:4] != b["key"][:4]:
                continue
            ratio = difflib.SequenceMatcher(None, a["key"], b["key"]).ratio()
            if ratio >= 0.86:
                out.append({"a": {"key": a["key"], "name": a["name"], "count": a["count"]},
                            "b": {"key": b["key"], "name": b["name"], "count": b["count"]},
                            "likeness": round(ratio, 3)})
    out.sort(key=lambda p: -(p["a"]["count"] + p["b"]["count"]))
    return out[:20]


def issues(db) -> dict:
    """What is untidy in the accessory data, as a list a person can act on.

    Reported, never repaired on its own: every one of these is a judgement about what a
    manufacturer meant, and the app has no business making it quietly.
    """
    from app import accessory_parts
    sheets = [s for s in db.query(models.Specsheet).all() if not accessory_parts.is_part_brand(s.make)]
    by_key = defaultdict(Counter)
    listed = set()
    debris = defaultdict(int)
    for sheet in sheets:
        raw = [a for a in (sheet.options or {}).get("accessories") or [] if str(a).strip()]
        if not raw:
            raw = split_options((sheet.fields or {}).get("accessories"))
        for name in raw:
            if is_none(name):
                continue            # "NIL": the datasheet saying there is no accessory
            if not is_name(name):
                debris[str(name).strip()] += 1
                continue
            listed.add(fold(name))
            by_key[fold(name)][name] += 1

    spellings = [
        {"key": key, "shown": _display_name(counts),
         "variants": [{"name": n, "products": c} for n, c in counts.most_common()]}
        for key, counts in by_key.items() if len(counts) > 1
    ]
    spellings.sort(key=lambda s: -sum(v["products"] for v in s["variants"]))

    orphans = []
    shown = {s.id for s in sheets}
    for entry in db.query(models.PriceEntry).all():
        config = entry.config or {}
        name = config.get("accessory")
        if not name or entry.accessory_part_id:
            continue                # a library accessory's own price is attached - to it
        if entry.specsheet_id is not None and entry.specsheet_id not in shown:
            continue                # a InHouse product's: its accessories are the library's business
        if entry.specsheet_id is None:
            orphans.append({"id": entry.id, "accessory": name, "reason": "not attached to any product",
                            "price": float(entry.price) if entry.price is not None else None})
        elif fold(name) not in listed:
            orphans.append({"id": entry.id, "accessory": name, "specsheet_id": entry.specsheet_id,
                            "reason": "no product's datasheet lists this accessory",
                            "price": float(entry.price) if entry.price is not None else None})

    return {
        "near": near_duplicates([{"key": k, "name": _display_name(c), "count": sum(c.values())}
                                 for k, c in by_key.items()]),
        "spellings": spellings,
        "debris": [{"text": t, "products": n} for t, n in sorted(debris.items(), key=lambda kv: -kv[1])],
        "orphan_prices": orphans,
    }
