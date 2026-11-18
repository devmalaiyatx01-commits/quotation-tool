"""The InHouse accessory library: each physical accessory is a record, and a product picks the
ones it takes.

Why a library, and why picking
------------------------------
An accessory's name is a label, not an identity. Two products can both list "U Shape Profile" and
take different profiles - 35x35 mm on one, 16x16 mm on the other, each with its own price and
picture - and two strip lights can take the very same profile. Typing the name per product gets
both cases wrong: two spellings of one profile ("U Shape Profile", "U-shape profile") become two
accessories, and two different profiles with one name become one.

So each physical accessory is one row (models.AccessoryPart), and a product points at the ones it
takes (models.AccessoryLink). The product form never asks for a name to be typed against a product:
it offers the library to pick from, and a new accessory is created in the library once, then
picked by every product that takes it.

  * Same thing, any spelling: identity is the record, not the text. Creating an accessory whose
    name folds to an existing one ("u-shape PROFILE") takes the existing spelling, so a family of
    variants is always written the same way.
  * Same name, different thing: a variant. Variants share a name and differ in a specification
    (Dimension 35x35 mm / 16x16 mm). Two accessories with the same name AND the same
    specifications are the same accessory, and creating the second is refused with the first one
    to use instead.

Everything that belongs to the physical thing is on the record: its specifications (`params`),
picture, own code, and its price (PriceEntry rows with accessory_part_id set). A product that
takes it gets all of them; a BOQ line records which accessory it is (config["accessory_part"]),
so lines of one accessory under different products can be added up.

InHouse only, per instruction (PART_BRANDS). Other brands keep their accessories as the names
their datasheets print, priced per product - app/accessories.py.
"""
import re
from collections import Counter, defaultdict

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app import accessories, models, product_codes

# The brands whose accessories are kept as a library. The single place that scoping lives, so
# moving another brand over is a one-word change and a data decision, not a hunt through code.
PART_BRANDS = ("InHouse",)

MAX_NAME = 80
MAX_CODE = 60
PART_PRICE_SOURCE = "Accessory library"


class PartError(ValueError):
    """Something that would make the library wrong, said in words a person can act on. `twin` is
    the existing accessory to use instead, when that is what is wrong."""

    def __init__(self, message, twin=None):
        super().__init__(message)
        self.twin = twin


def is_part_brand(make) -> bool:
    return (make or "").strip().lower() in {b.lower() for b in PART_BRANDS}


# --- identity --------------------------------------------------------------------------------


def norm_value(value) -> str:
    """How two spellings of one specification value are recognised as the same: case, spacing,
    the multiplication sign and a millimetre unit after a number are not differences.
    "35mm X 35mm", "35 x 35" and "35*35" are all "35x35"."""
    text = str(value or "").lower().replace("×", "x").replace("*", "x")
    text = re.sub(r"(?<=\d)\s*mm\b", "", text)
    return re.sub(r"[^a-z0-9.]+", "", text)


def specs_of(part) -> list:
    """[{name, value}] - a part's specifications. Stored as params ([{name, options}]) because Add
    Product already reads that shape: a parameter with one option is a fact it prints, not a
    question it asks."""
    out = []
    for p in part.params or []:
        options = [o for o in p.get("options") or [] if str(o).strip()]
        out.append({"name": p.get("name") or "", "value": " / ".join(options)})
    return out


def identity(name, params) -> tuple:
    return (accessories.fold(name),
            frozenset((accessories.fold(p.get("name")), norm_value(" ".join(p.get("options") or [])))
                      for p in params or []))


def params_from_specs(specs) -> list:
    """Specifications as typed ([{name, value}]) into stored params, or a PartError. A row with
    neither a name nor a value is an empty line in the form and is dropped."""
    if specs is None:
        return []
    if not isinstance(specs, list):
        raise PartError("Specifications must be a list.")
    raw = []
    for s in specs:
        if not isinstance(s, dict):
            raise PartError("Each specification needs a name and a value.")
        name = " ".join(str(s.get("name") or "").split())
        value = " ".join(str(s.get("value") or "").split())
        if not name and not value:
            continue
        if not name:
            raise PartError('Give "%s" a name - Dimension, Length, Finish...' % value)
        if not value:
            raise PartError('Give "%s" a value.' % name)
        raw.append({"name": name, "options": [value]})
    try:
        return accessories.clean_params(raw)
    except ValueError as exc:
        raise PartError(str(exc))


def _brand_parts(db, make, discipline):
    return db.query(models.AccessoryPart).filter(models.AccessoryPart.make == make,
                                                 models.AccessoryPart.discipline == discipline).all()


def _describe(part) -> str:
    specs = ", ".join("%s %s" % (s["name"], s["value"]) for s in specs_of(part))
    return part.name + (" (%s)" % specs if specs else "")


def _check(db, make, discipline, name, params, code, exclude_id=None, respell=False):
    """Settles a name, specifications and code for a new or edited accessory: the name takes the
    spelling of an existing accessory it is a variant of (unless `respell`, which is the family's
    spelling being changed), and a twin or a code already in use is refused. Returns
    (name, code) to store."""
    name = " ".join(str(name or "").split())
    if not name:
        raise PartError("Give the accessory a name.")
    if len(name) > MAX_NAME:
        raise PartError("A name can be up to %d characters." % MAX_NAME)
    others = [p for p in _brand_parts(db, make, discipline) if p.id != exclude_id]
    family = [p for p in others if accessories.fold(p.name) == accessories.fold(name)]
    if family and not respell:
        # One accessory family, one spelling - the reason a capital letter can never split one
        # accessory into two.
        name = Counter(p.name for p in family).most_common(1)[0][0]
    me = identity(name, params)
    twin = next((p for p in family if identity(p.name, p.params) == me), None)
    if twin is not None:
        raise PartError(
            "%s is already in the library%s. Use it, or give this one a specification that tells "
            "them apart - a different Dimension, say." % (
                _describe(twin), "" if not params else " with the same specifications"), twin=twin)
    if code is not None:
        # Written the way every product code is (product_codes.clean_code): capitals, no spaces.
        # It is what this accessory adds to a product's code, so it is required, and it must say
        # what it is - a code of digits alone could be a size or a wattage.
        code = product_codes.clean_code(code)
        if not code:
            raise PartError("Give it a product code of its own - a U Shape Profile in 16x16 and one "
                            "in 35x35 put different codes into a product's code.")
        if len(code) > MAX_CODE:
            raise PartError("A code can be up to %d characters." % MAX_CODE)
        if not product_codes.explicit(code):
            raise PartError("A code needs at least one letter - \"%s\" could be a size or a wattage." % code)
        clash = next((p for p in others if (p.code or "").upper() == code), None)
        if clash is not None:
            raise PartError('"%s" is already the code of %s.' % (code, _describe(clash)), twin=clash)
    return name, code


def create(db, make, discipline, name, specs, code="") -> "models.AccessoryPart":
    if not is_part_brand(make):
        raise PartError("The accessory library is for %s products." % " and ".join(PART_BRANDS))
    params = params_from_specs(specs)
    name, code = _check(db, make, discipline, name, params, code or "")
    part = models.AccessoryPart(discipline=discipline, make=make, name=name, code=code or "",
                                params=params, image="")
    db.add(part)
    db.flush()
    return part


def update(db, part, name=None, specs=None, code=None) -> dict:
    """Edits an accessory's name, specifications or code (any of them), and brings the accessory
    list of every product that takes it up to date. Returns {before, after, respelled}.

    A new spelling of the same name ("U-SHAPE PROFILE" -> "U Shape Profile") is the family's new
    spelling, so every variant takes it; a different name moves this one into that family (or a
    new one)."""
    before = {"name": part.name, "specs": specs_of(part), "code": part.code or ""}
    params = params_from_specs(specs) if specs is not None else list(part.params or [])
    wanted = part.name if name is None else name
    respell = accessories.fold(wanted) == accessories.fold(part.name)
    # The code is only checked when one is sent: renaming an accessory that has none yet is fine.
    new_name, new_code = _check(db, part.make, part.discipline, wanted, params, code,
                                exclude_id=part.id, respell=respell)
    family = [p for p in _brand_parts(db, part.make, part.discipline)
              if p.id != part.id and accessories.fold(p.name) == accessories.fold(part.name)] if respell else []
    touched = {part.id} | {p.id for p in family if p.name != new_name}
    for sibling in family:
        sibling.name = new_name
    part.name, part.params = new_name, params
    if code is not None:
        part.code = new_code or ""
    db.flush()
    after = {"name": part.name, "specs": specs_of(part), "code": part.code or ""}
    if (before["name"], before["specs"]) != (after["name"], after["specs"]):
        sheet_ids = {l.specsheet_id for l in db.query(models.AccessoryLink).filter(
            models.AccessoryLink.part_id.in_(touched)).all()}
        for sheet_id in sheet_ids:
            write_accessory_list(db, sheet_id)
    return {"before": before, "after": after, "respelled": len(touched) - 1}


def delete(db, part, everywhere=False) -> dict:
    """Deletes an accessory from the library. Its price goes back to List Pricing's waiting list
    rather than vanishing with it.

    Without `everywhere` it refuses an accessory a product takes or a BOQ line was added as. With it
    (owner, 18 Nov 2026: "once removed, it must be removed from the accessory section of every
    product") the accessory is first taken off every product that takes it - its library link, its
    name in the product's accessory list and on its printed ACCESSORIES line - and what each product
    listed before is returned, for the activity log and for putting it back. BOQ lines already made
    are left exactly as they are: they are a record of what was quoted."""
    products = []
    if everywhere:
        products = take_off_every_product(db, part)
    elif part.links:
        raise PartError("%d product(s) take this accessory - remove it from them first." % len(part.links))
    lines = lines_naming(db, part.id)
    if lines and not everywhere:
        raise PartError("BOQ lines were added as this accessory, so it stays as the record of what they are.")
    released = 0
    for row in price_rows(db, [part.id]).get(part.id, []):
        row.accessory_part_id = None
        row.status = "open"
        released += 1
    db.flush()
    db.delete(part)
    db.flush()
    return {"prices_released": released, "products": products, "boq_lines_kept": lines}


def take_off_every_product(db, part) -> list:
    """Takes one library accessory off every product that takes it: the link goes, and each
    product's accessory list and printed line are rewritten from what it still takes (plus any
    datasheet names never in the library, which are kept). Returns, per product, what it listed
    before and after - nothing else about the product changes."""
    out = []
    for sheet_id in sorted({l.specsheet_id for l in part.links}):
        sheet = db.get(models.Specsheet, sheet_id)
        if sheet is None:
            continue
        links = links_of(db, [sheet_id]).get(sheet_id, [])
        # names this product lists that no library accessory backs: kept as they are
        backed = {accessories.fold(label) for _l, label in labelled(links)} | {accessories.fold(l.part.name) for l in links}
        before_list = list(accessories._names_of(sheet))
        before_printed = str((sheet.fields or {}).get("accessories") or "")
        legacy = [n for n in before_list if accessories.fold(n) not in backed]
        for link in links:
            if link.part_id == part.id:
                db.delete(link)
        db.flush()
        write_accessory_list(db, sheet_id, legacy=legacy, previous=before_list)
        db.refresh(sheet)
        out.append({"product_id": sheet_id, "product": sheet.product_name or "",
                    "accessories_before": before_list, "printed_before": before_printed,
                    "accessories_after": list((sheet.options or {}).get("accessories") or []),
                    "printed_after": str((sheet.fields or {}).get("accessories") or "")})
    db.expire(part, ["links"])
    return out


def lines_naming(db, part_id) -> int:
    return sum(1 for line in db.query(models.BOQItem).filter(models.BOQItem.is_accessory.is_(True)).all()
               if (line.config or {}).get("accessory_part") == part_id)


# --- prices ----------------------------------------------------------------------------------


def price_rows(db, part_ids) -> dict:
    """{part id: [PriceEntry]} - each accessory's own price rows."""
    ids = list({i for i in part_ids if i})
    out = defaultdict(list)
    if ids:
        for row in db.query(models.PriceEntry).filter(
                models.PriceEntry.accessory_part_id.in_(ids)).order_by(models.PriceEntry.id).all():
            out[row.accessory_part_id].append(row)
    return out


def price_of(rows):
    """An accessory's price: the one figure its rows agree on, or None when it has none - or when
    they disagree, which only an edit made row by row in List Pricing can cause, and which is then
    shown as unpriced rather than quoted at either figure."""
    values = {round(float(r.price), 2) for r in rows or [] if r.price is not None}
    return values.pop() if len(values) == 1 else None


def _money(value):
    return "Rs " + format(round(float(value), 2), ",g")


def set_price(db, part, price) -> dict:
    """Gives an accessory its price. Every row it has takes the new figure (they agree, by
    construction), and one with none gets a row. There is no clearing a price here: a price
    somebody entered never quietly disappears - List Pricing can delete a row on purpose."""
    try:
        price = round(float(price), 2)
    except (TypeError, ValueError):
        raise PartError("A price is a number.")
    if price < 0:
        raise PartError("A price cannot be negative.")
    rows = price_rows(db, [part.id]).get(part.id, [])
    before = price_of(rows)
    if rows:
        for row in rows:
            row.price = price
            row.status = "locked"
    else:
        db.add(models.PriceEntry(
            discipline=part.discipline, specsheet_id=None, accessory_part_id=part.id,
            source_file=PART_PRICE_SOURCE, source_sheet="",
            source_label="Price set in the accessory library",
            config={"accessory": part.name}, price=price, status="locked"))
    db.flush()
    return {"before": before, "after": price}


def attach_price(db, row, part):
    """A price-sheet row mapped (in List Pricing) to a product's library accessory becomes that
    accessory's price - refused when it already has a different one, which means the row is for a
    different variant, or when the row only applies to some configurations of one product."""
    limits = {k: v for k, v in (row.config or {}).items() if k != "accessory"}
    if limits:
        raise PartError("This price applies only when %s - a library accessory has one price for "
                        "every product that takes it." % ", ".join(
                            "%s is %s" % (k, v) for k, v in sorted(limits.items())))
    others = [r for r in price_rows(db, [part.id]).get(part.id, []) if r.id != row.id]
    if others and price_of(others) != round(float(row.price), 2):
        raise PartError("%s already costs %s. A different price means a different variant - "
                        "create it in the accessory library and pick it for this product." % (
                            _describe(part), "several figures" if price_of(others) is None
                            else _money(price_of(others))))
    row.specsheet_id = None
    row.accessory_part_id = part.id
    db.flush()


def _own_rows(db, sheet_id, name) -> list:
    """A product's own (per-product) price rows for an accessory of this name."""
    key = accessories.fold(name)
    return [r for r in db.query(models.PriceEntry).filter(
                models.PriceEntry.specsheet_id == sheet_id,
                models.PriceEntry.accessory_part_id.is_(None)).all()
            if accessories.fold((r.config or {}).get("accessory") or "") == key]


def take_over_price(db, sheet, legacy_name, part, may_price) -> str:
    """A product's datasheet accessory replaced by a library accessory: the product's own price
    for it moves onto the accessory when that has none or the same figure, so a price somebody
    entered is neither lost nor duplicated. Otherwise it stays on the product, and the note says
    so. Returns the note ("" when there was nothing to move)."""
    rows = _own_rows(db, sheet.id, legacy_name)
    if not rows:
        return ""
    if any({k for k in (r.config or {}) if k != "accessory"} for r in rows):
        return ("%s's own price for %s applies only to some configurations, so it was left on the "
                "product." % (sheet.product_name, legacy_name))
    current = price_of(price_rows(db, [part.id]).get(part.id, []))
    theirs = price_of(rows)
    if theirs is None:
        return "%s had several prices for %s; they were left on the product." % (sheet.product_name, legacy_name)
    if current is not None and current != theirs:
        return ("%s's own price for %s (%s) differs from %s's (%s), so it was left on the product - "
                "if it is right, this is probably a different variant." % (
                    sheet.product_name, legacy_name, _money(theirs), _describe(part), _money(current)))
    if current is None and not may_price:
        return ("%s's own price for %s (%s) was left on the product: pricing the library accessory "
                "needs “Change prices”." % (sheet.product_name, legacy_name, _money(theirs)))
    for row in rows:
        row.specsheet_id = None
        row.accessory_part_id = part.id
    db.flush()
    return "%s's price for %s (%s) moved onto %s." % (sheet.product_name, legacy_name,
                                                     _money(theirs), _describe(part))


# --- a product's accessories -----------------------------------------------------------------


def links_of(db, sheet_ids) -> dict:
    """{specsheet id: [AccessoryLink]}, in the product's own order.

    Each link arrives with its part already loaded. Every caller reads `link.part`, and left to
    load lazily that is one query per link - and again in the next caller, because the session
    keeps objects nobody holds only weakly. One product's accessories cost 40 queries this way
    (four helpers, each 1 + 6), 67 ms of a 2 KB answer; loaded here, two per call."""
    ids = list({i for i in sheet_ids if i})
    out = defaultdict(list)
    if ids:
        for link in db.query(models.AccessoryLink).options(selectinload(models.AccessoryLink.part)).filter(
                models.AccessoryLink.specsheet_id.in_(ids)).order_by(
                models.AccessoryLink.sort_order, models.AccessoryLink.id).all():
            out[link.specsheet_id].append(link)
    return out


def link_counts(db, part_ids) -> dict:
    """{part id: how many products take it}, for these parts. A part no product takes may be
    left out - callers read it with .get(id, 0)."""
    # TODO(human): answer this with ONE query that counts in the database, instead of the
    # placeholder below, which loads every link of every part (one query per part) to count them.
    parts = db.query(models.AccessoryPart).filter(models.AccessoryPart.id.in_(list(part_ids))).all()
    return {p.id: len(p.links) for p in parts}


def labels_for_parts(parts) -> list:
    """What each of a product's accessories is called on it, in order: its name, unless the
    product takes two variants of it, which are then told apart by their specifications."""
    names = Counter(accessories.fold(p.name) for p in parts)
    out = []
    for part in parts:
        label = part.name
        if names[accessories.fold(label)] > 1:
            specs = ", ".join(s["value"] for s in specs_of(part)) or "#%d" % part.id
            label = "%s (%s)" % (label, specs)
        out.append(label)
    return out


def labelled(links) -> list:
    """[(link, label)] for a product's links - see labels_for_parts."""
    return list(zip(links, labels_for_parts([l.part for l in links])))


def set_product_accessories(db, sheet, part_ids) -> dict:
    """Makes the product take exactly these accessories, in this order. Returns what changed."""
    wanted = []
    for pid in part_ids:
        if pid not in wanted:
            wanted.append(pid)
    parts = {p.id: p for p in db.query(models.AccessoryPart).filter(
        models.AccessoryPart.id.in_(wanted)).all()} if wanted else {}
    for pid in wanted:
        part = parts.get(pid)
        if part is None:
            raise PartError("Accessory #%s is no longer in the library - reload the page." % pid)
        if part.make != sheet.make or part.discipline != (sheet.discipline or models.DEFAULT_DISCIPLINE):
            raise PartError("%s is a %s accessory; this product is %s." % (part.name, part.make, sheet.make))
    have = {l.part_id: l for l in links_of(db, [sheet.id]).get(sheet.id, [])}
    changes = {"added": [], "removed": []}
    for pid, link in have.items():
        if pid not in wanted:
            changes["removed"].append({"id": pid, "name": link.part.name})
            db.delete(link)
    db.flush()
    for order, pid in enumerate(wanted):
        link = have.get(pid)
        if link is None:
            db.add(models.AccessoryLink(specsheet_id=sheet.id, part=parts[pid], sort_order=order))
            changes["added"].append({"id": pid, "name": parts[pid].name})
        elif link.sort_order != order:
            link.sort_order = order
    db.flush()
    return changes


def write_accessory_list(db, sheet_id, legacy=None, previous=None):
    """Writes a product's accessory list - `options["accessories"]`, and the printed ACCESSORIES
    line - from its library accessories, followed by any datasheet names not yet in the library
    (`legacy`; when None, the ones it lists now are kept).

    The printed line is only rewritten when the list changed from `previous` (by default, what the
    product lists now), so saving a product untouched never rewrites its sheet. A printed line that
    began with "NIL" - the datasheet saying a plain fixture is also an option - keeps it."""
    sheet = db.get(models.Specsheet, sheet_id)
    if sheet is None:
        return
    links = links_of(db, [sheet.id]).get(sheet.id, [])
    labels = [label for _l, label in labelled(links)]
    current = accessories._names_of(sheet)
    if legacy is None:
        known = {accessories.fold(l.part.name) for l in links} | {accessories.fold(x) for x in labels}
        legacy = [n for n in current if accessories.fold(n) not in known]
    names = labels + [n for n in legacy if n not in labels]
    old_printed = str((sheet.fields or {}).get("accessories") or "")
    same = [accessories.fold(n) for n in names] == \
        [accessories.fold(n) for n in (current if previous is None else previous)]
    options = dict(sheet.options or {})
    if names:
        options["accessories"] = names
    else:
        options.pop("accessories", None)
    sheet.options = options
    if same and old_printed:
        db.flush()
        return
    nil = old_printed.strip().upper().startswith("NIL")
    printed = " / ".join((["NIL"] if nil else []) + names)
    fields = dict(sheet.fields or {})
    if printed:
        fields["accessories"] = printed
    else:
        fields.pop("accessories", None)
    sheet.fields = fields
    params = {g: dict(v) for g, v in (sheet.params or {}).items()} if sheet.params else {}
    for group, values in params.items():
        for label in list(values):
            if str(label).strip().upper() == "ACCESSORIES":
                values[label] = printed
    if params:
        sheet.params = params
    db.flush()


def details_for_sheet(db, sheet) -> dict:
    """{label: what Add Product needs} for this product's library accessories: the accessory's id,
    specifications (as parameters with one option, printed on the line without being asked), its
    picture and code."""
    out = {}
    for link, label in labelled(links_of(db, [sheet.id]).get(sheet.id, [])):
        part = link.part
        out[label] = {"key": "part-%d" % part.id, "part_id": part.id, "part_name": part.name,
                      "params": list(part.params or []), "image": part.image or "",
                      "code": part.code or ""}
    return out


def prices_for_sheet(db, sheet) -> list:
    """(PriceEntry, label) for this product's library accessories."""
    pairs = labelled(links_of(db, [sheet.id]).get(sheet.id, []))
    rows = price_rows(db, [l.part_id for l, _label in pairs])
    return [(row, label) for link, label in pairs for row in rows.get(link.part_id, [])]


def link_for(db, sheet_id, name):
    """The link whose label on this product is `name` (case and spacing aside), or None."""
    if not sheet_id or not name:
        return None
    key = accessories.fold(name)
    pairs = labelled(links_of(db, [sheet_id]).get(sheet_id, []))
    return next((l for l, label in pairs if accessories.fold(label) == key), None)


def for_specsheet(db, sheet) -> list:
    """The Specsheet tab's view of a InHouse product's accessories: each library accessory with its
    specifications, price, code and how many other products take it, then each name the datasheet
    lists that is not in the library yet (priced on the product, as before)."""
    pairs = labelled(links_of(db, [sheet.id]).get(sheet.id, []))
    prices = price_rows(db, [l.part_id for l, _label in pairs])
    taking = link_counts(db, [l.part_id for l, _label in pairs])
    out = []
    for link, label in pairs:
        part = link.part
        out.append({"key": "part-%d" % part.id, "name": label, "catalogue_name": part.name,
                    "library": True, "specs": specs_of(part), "price": price_of(prices.get(part.id)),
                    "code": part.code or None, "also_on": taking.get(part.id, 0) - 1, "makes": [part.make]})
    known = {accessories.fold(label) for _l, label in pairs}
    own = {}
    for row in sheet.price_entries or []:
        name = (row.config or {}).get("accessory")
        if name and len(row.config or {}) == 1:
            own.setdefault(accessories.fold(name), float(row.price))
    for name in accessories._names_of(sheet):
        if accessories.fold(name) in known:
            continue
        out.append({"key": accessories.fold(name), "name": name, "catalogue_name": name,
                    "library": False, "specs": [], "price": own.get(accessories.fold(name)),
                    "code": None, "also_on": 0, "makes": [sheet.make]})
    return out


def code_overrides(db, sheet=None, part_ids=None) -> dict:
    """{name a product lists it under: its own code, "" for none} for a InHouse product's library
    accessories - a saved product's (`sheet`), or the ids a draft has picked. The product code puts
    these in place of the name's abbreviation, which variants share (product_codes.Context)."""
    if sheet is not None and getattr(sheet, "id", None) and is_part_brand(getattr(sheet, "make", "")):
        parts = [l.part for l in links_of(db, [sheet.id]).get(sheet.id, [])]
    elif part_ids:
        ids = []
        for raw in part_ids:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        found = {p.id: p for p in db.query(models.AccessoryPart).filter(models.AccessoryPart.id.in_(ids)).all()}
        parts = [found[i] for i in ids if i in found]
    else:
        return {}
    return {label: part.code or "" for part, label in zip(parts, labels_for_parts(parts))}


def library_codes_by_label(db, sheet_ids) -> dict:
    """{name a product lists an accessory under: [its library code(s)]} across these products, in
    one pass - what Nomenclature's value table shows, so it says what NPD and the product codes say
    (code_overrides is the same thing for one product). A name two products give to different
    library accessories ("'U'-SHAPE PROFILE": 08x09 on one, 17x14 on another) lists each code; ""
    stands for one with no code yet."""
    out = {}
    for links in links_of(db, sheet_ids).values():
        parts = [l.part for l in links]
        for part, label in zip(parts, labels_for_parts(parts)):
            codes = out.setdefault(label, [])
            code = part.code or ""
            if code not in codes:
                codes.append(code)
    return out


def labels_of(db, sheet_id) -> set:
    """The folded labels of a product's library accessories."""
    return {accessories.fold(label) for _l, label in labelled(links_of(db, [sheet_id]).get(sheet_id, []))}


# --- the library -----------------------------------------------------------------------------


def library(db, discipline=None) -> list:
    """Every accessory in the library, with what it is, what it costs and the products that take
    it - the one place to see all of it."""
    query = db.query(models.AccessoryPart)
    if discipline:
        query = query.filter(models.AccessoryPart.discipline == discipline)
    parts = [p for p in query.all() if is_part_brand(p.make)]
    if not parts:
        return []
    prices = price_rows(db, [p.id for p in parts])
    links = db.query(models.AccessoryLink).filter(
        models.AccessoryLink.part_id.in_([p.id for p in parts])).all()
    sheets = {s.id: s for s in db.query(models.Specsheet).filter(
        models.Specsheet.id.in_({l.specsheet_id for l in links})).all()} if links else {}
    used = defaultdict(list)
    for link in links:
        s = sheets.get(link.specsheet_id)
        if s is not None:
            used[link.part_id].append({"id": s.id, "name": s.product_name or s.filename or "",
                                       "series": s.series or "", "family": s.family or "",
                                       "typology": s.typology or "",
                                       "approval_status": s.approval_status or ""})
    out = []
    for part in parts:
        rows = prices.get(part.id, [])
        products = sorted(used.get(part.id, []), key=lambda p: (p["series"], p["family"], p["name"]))
        out.append({"id": part.id, "name": part.name, "group": accessories.fold(part.name),
                    "make": part.make, "specs": specs_of(part), "code": part.code or "",
                    "price": price_of(rows), "price_conflict": len(rows) > 1 and price_of(rows) is None,
                    "image": part.image or "", "products": products,
                    "updated_at": part.updated_at.isoformat() if part.updated_at else None})
    out.sort(key=lambda p: (p["group"], [s["value"] for s in p["specs"]], p["id"]))
    return out
