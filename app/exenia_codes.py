"""Composes Exenia's ordering codes from the options a specifier has actually chosen.

Exenia does not publish one code per product. Their configurator builds it by concatenating
the SKU of every step in order - housing body `134`, version `80`, control `R`, finish `W`,
colour `22`, optic `V` gives `13480RW22V` - so a complete code only exists once a fixture is
fully specified. `import_exenia_website.py` captures each step's {option title: SKU} map into
`params["Option codes (Exenia)"]`, and this reads it back.

One page, several codes
-----------------------
A product page is not one orderable product. M2's page configures three: the luminaire, a
decorative ring, and a mounting adapter, and Exenia's own page prints them as separate codes
joined by a "+":

    610 E0 51 52 /9530  +  6109050 17 /M

What separates them is a step whose only option is "+" - "Order separately", "Select to
activate adapter". Those steps are not decoration to be dropped; they are the boundary. Read
as decoration, M2 came out as the single string `610E05052/953017/M`: the ring's own base
part number missing, and the ring's finish and optic run onto the end of the luminaire's
code. That is a part number Exenia does not sell, which is the worst possible thing to put on
a purchase order, and it is what this module exists to get right.

So the steps are split into groups at those markers. Group 0 is the luminaire and is always
priced; every later group is an extra, and is coded only when the specifier actually chose
something from it - nothing reaches a purchase order because a dropdown had a first entry.

Within a group, nothing is skipped for what it is called
--------------------------------------------------------
An earlier version also dropped any step whose NAME looked like an add-on - matching
"accessor", "adapter", "driver" and so on. That guess was wrong in both directions on a
single page. SPOT 230 REVO Compact EASYMETRIK is ordered as

    387 E0 Y B 5 2T MS N

where `5` is the "Adapter finish" and `N` is "Accessories: No accessories" - both dropped by
that rule, leaving `387E0YB2TMS`. A code that is shorter than the real one but looks just as
plausible is the worst thing this module can produce, and no amount of care in the rest of
it makes up for guessing here.

The page already says where a product's code ends: the "+" markers. That is the only
boundary used. Within a group every step contributes its SKU, and the sole reason a step
contributes nothing is that Exenia publishes no SKU for it anywhere - a fact read off the
data, not inferred from a word in a label.

The refusal rule
----------------
A segment is used only when the chosen values single out EXACTLY ONE option of that step.
Where they do not - the pick matches no option, or several - no code is returned at all and
the caller falls back to the body+version stem. That is deliberate and is the whole point of
this module: a code that is complete but wrong orders the wrong fixture, and is far worse
than a stem the buyer can see is partial. Silence beats a plausible guess.

Two steps carry two of our fields at once, because Exenia sells them as one choice:

  CRI-CCT   "CRI92 3000K" is a die and the temperature it is binned in.
  Optic     "Narrow Spot 9° - 18,2W" is a lens and the power drawn through it.

Both halves must match - matching "3000K" alone would pick a CRI92 or CRI98 part at random.
But the coupling is a property of the page, not of the field: Tripo Bollard states its power
in a separate `Power` step and its optics carry none, so the second half is required only
when the step's own titles actually carry that half. That is read off the options, not
assumed.
"""
import json
import re
from collections import OrderedDict

# Exenia's configurator step names -> our field keys. A step this does not name keeps its
# own name as a slug and renders as its own dropdown (see extra_fields in app/main.py), so
# nothing is dropped and nothing is forced into a field that means something else.
#
# "CRI-CCT" is one step on Exenia's configurator ("CRI92 3000K") because the grade and the
# temperature are coupled - a 92 CRI part and a 98 CRI part are different LED dies, binned
# into different temperatures. It is still two properties, and storing the pair under `cct`
# labelled a colour-rendering grade a colour temperature everywhere the field is read. The
# pair is split on import and the coupling written as a ConfigRule instead, which is how this
# app models couplings - see restructure_coupled_field_values.py, which owns those rules.
#
# This lives here rather than in import_exenia_website.py (which imports it) because the
# running app needs it too: composing a code means going from a step name back to the field
# the specifier chose it under, and a second copy of this map would drift from the one the
# rows were imported with, quietly mis-composing codes.
STEP_FIELDS = {
    "Optic": "beam_angle",
    "Optics": "beam_angle",
    "Finishing": "finish",
    "Ring finishing": "ring_finish",
    "Primary optics accessories": "primary_optical_accessories",
    "CRI-CCT": "cct",
    "CRI and CCT": "cct",
    "Control": "control",
    "Finish": "finish",
    "Body finish": "finish",
    "Power": "wattage",
    "Output": "lumen_output",
    "Dimension": "length",
    "Accessories": "accessories",
}

_STEP_FIELDS_CI = {k.lower(): v for k, v in STEP_FIELDS.items()}

# An option that is only punctuation - Exenia's "+" - is their shop's UI, and a step made
# entirely of them separates one orderable product from the next. See is_separator().
_PUNCTUATION_ONLY = re.compile(r"^[\s+\-‐-―*/|.,;:]*$")

# The step naming the luminaire itself, whose SKU opens the code. Exenia calls it three
# things across the catalogue; see BODY_STEPS in backfill_product_codes.py.
BODY_STEPS = ("housing body", "apparel body", "system body")

VERSION_STEP = "version"

# Our field key -> the halves that must both be matched, when the step encodes both.
SPLIT_STEPS = {"cct": ("cri", "cct"), "beam_angle": ("beam_angle", "wattage")}

_CRI = re.compile(r"CRI\s*\d{2}", re.I)
_WATTS = re.compile(r"\d+([.,]\d+)?\s*W\b", re.I)
_HALF_MARKER = {"cri": _CRI, "wattage": _WATTS}


def slug(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_")


def field_key_for(step: str) -> str:
    """The field key a configurator step's value is stored under."""
    return _STEP_FIELDS_CI.get((step or "").lower(), slug(step or ""))


def _norm(s: str) -> str:
    """Compare without the incidental differences between a dropdown value and a site title:
    spacing, the four dash characters Exenia mixes, and the decimal comma ("8,2W" / "8.2W")."""
    return re.sub(r"[\s‐-―-]", "", (s or "").lower()).replace(",", ".")


def option_skus(specsheet) -> dict:
    """-> {step: {option title: sku}}, or {} for a row with none captured."""
    raw = (specsheet.params or {}).get("Option codes (Exenia)") or {}
    out = {}
    for step, codes in raw.items():
        if isinstance(codes, str):
            try:
                codes = json.loads(codes)
            except ValueError:
                return {}
        if isinstance(codes, dict):
            out[step] = codes
    return out


def _required_halves(field_key: str, titles) -> tuple:
    """Which halves of a coupled step this page's own options actually encode."""
    halves = SPLIT_STEPS.get(field_key)
    if not halves:
        return (field_key,)
    return tuple(h for h in halves
                 if h not in _HALF_MARKER or any(_HALF_MARKER[h].search(t) for t in titles))


def _resolve(options: dict, wanted: list):
    """The one SKU whose option title carries every wanted value, or None if not exactly one.

    An exact match wins over a containing one, because containment alone is ambiguous on
    their tunable-white options: a chosen 4000K is inside both "CRI98 4000K" and "CRI98 TW
    (2700K-4000K)", and those are different parts. Once whitespace and dashes are normalised
    away, a title that is precisely the chosen halves end to end ("CRI98 4000K" -> CRI98 +
    4000K) is that option and no other, so it is taken before containment is considered.
    """
    if not wanted:
        return None
    joined = "".join(_norm(w) for w in wanted)
    exact = {sku for title, sku in options.items() if _norm(title) == joined}
    if len(exact) == 1:
        return exact.pop()
    hits = {sku for title, sku in options.items()
            if all(_norm(w) in _norm(title) for w in wanted)}
    return hits.pop() if len(hits) == 1 else None


_CODE_BEARING = None


def code_bearing_steps(refresh: bool = False) -> frozenset:
    """Every configurator step name that Exenia codes on at least one page.

    Read from the catalogue rather than hard-coded, because it is a fact about their data
    and a hand-written list would drift the moment a family is re-imported. Computed once
    per process: one query, and the answer only changes when the Exenia rows are re-imported
    (pass refresh=True after doing that in the same process).
    """
    global _CODE_BEARING
    if _CODE_BEARING is not None and not refresh:
        return _CODE_BEARING
    # Imported here, not at module scope: this module is imported by the parser, which has
    # no business opening a database connection just to be loaded.
    from app.database import SessionLocal
    from app import models
    names = set()
    db = SessionLocal()
    try:
        for (params,) in db.query(models.Specsheet.params).filter(
                models.Specsheet.make == "Exenia"):
            for step, codes in ((params or {}).get("Option codes (Exenia)") or {}).items():
                if isinstance(codes, str):
                    try:
                        codes = json.loads(codes)
                    except ValueError:
                        continue
                if isinstance(codes, dict) and any(v for v in codes.values()):
                    names.add(step)
    finally:
        db.close()
    _CODE_BEARING = frozenset(names)
    return _CODE_BEARING


def is_separator(options: dict) -> bool:
    """A step whose every option is punctuation is Exenia's "+" affordance, and it marks the
    boundary between two separately-ordered products - not a choice and not part of a code."""
    return bool(options) and all(_PUNCTUATION_ONLY.match(t or "") for t in options)


def code_groups(specsheet) -> list:
    """The page's steps split into one group per separately-ordered product, in page order.

    Group 0 is the luminaire itself. Any later group is something bought alongside it - M2's
    decorative ring, then its adapter - each with a code of its own.
    """
    groups, current = [], OrderedDict()
    for step, options in option_skus(specsheet).items():
        if is_separator(options):
            if current:
                groups.append(current)
            current = OrderedDict()
            continue
        current[step] = options
    if current:
        groups.append(current)
    return groups


def _compose_group(specsheet, group: dict, chosen: dict, is_first: bool) -> str:
    fields = specsheet.fields or {}
    segments = []
    for index, (step, options) in enumerate(group.items()):
        low = step.lower()
        if low in BODY_STEPS:
            if len(set(options.values())) != 1:
                return ""
            segments.append(next(iter(options.values())))
            continue
        if low == VERSION_STEP:
            sku = options.get(specsheet.variant or "")
            if not sku:
                return ""
            segments.append(sku)
            continue
        # NOTHING is skipped by what a step is called. An earlier version dropped any step
        # whose name looked like an add-on ("accessor", "adapter", ...), and that guess was
        # wrong in both directions on one page: SPOT 230 REVO Compact EASYMETRIK's code is
        # 387 E0 Y B 5 2T MS N, where 5 is the "Adapter finish" and N is "Accessories: No
        # accessories". Both were dropped, and the result - 387E0YB2TMS - was a code that
        # looked complete and was not, which is the exact failure this module exists to make
        # impossible. Names lie; the page's own "+" markers do not, and grouping on them
        # already says where one product's code ends. Trust that and nothing else.
        # A step with no usable SKU is one of two very different things, and collapsing them
        # is exactly how a code comes out short but plausible. The page itself distinguishes
        # them, so this asks the page before it asks anything else:
        #
        #   DECLARED UNCODED - every option carries product data, and none of it has a SKU.
        #   That is Exenia stating this step is not in the code. M2's "Secondary optical
        #   accessories", EASYMETRIK's "Independent accessories", Superlinea's "UGR". Skipping
        #   is then a fact about their data, not an assumption about ours.
        #
        #   NO DATA AT ALL - the page sent nothing for these options, so we know nothing.
        #   SPOT REVO Small Semi-Recessed's "Factory-fitted accessories" looks like this, and
        #   it DOES code: the trailing N of 385 _ R W 23 S N. Superlinea's CRI-CCT and Optic
        #   look like this too. Treated as "no SKU" they quietly shortened the code.
        #
        # Catalogue-wide evidence is then used in one direction only - to rescue a step we
        # have no data for but that Exenia codes on NO page anywhere (`Lifetime`, `UGR`,
        # `Power supply`: derived specifications and separately-bought drivers). It is never
        # used to force a refusal, because one page that happens to code a step name says
        # nothing about this page, and using it that way refused hundreds of good codes.
        coded = [v for v in options.values() if v]
        if not coded:
            unknown = any(v is None for v in options.values())
            if not unknown:
                continue                       # the page says: not part of the code
            if index == 0 or step in code_bearing_steps():
                return ""                      # we have no data and it may well code
            continue                           # never coded anywhere: safe to leave out
        # A step with one option is not a question, it is a fixed part of this product -
        # M2's Control is only ever On-off (E0), and a group's base part is always single.
        # Looked up as though it were a choice, it finds nothing chosen and refuses a code
        # that was never in doubt.
        if len(options) == 1:
            segments.append(next(iter(options.values())))
            continue
        key = field_key_for(step)
        wanted = []
        for half in _required_halves(key, list(options)):
            value = (chosen.get(half) or "").strip()
            if not value:
                # Not chosen: usable only if the row offers a single value for that field,
                # which makes it decided rather than defaulted.
                only = [v.strip() for v in (fields.get(half) or "").split("/") if v.strip()]
                if len(only) != 1:
                    return ""
                value = only[0]
            wanted.append(value)
        sku = _resolve(options, wanted)
        if not sku:
            return ""
        segments.append(sku)
    return "".join(segments)


def _group_requested(group: dict, chosen: dict) -> bool:
    """Whether the specifier actually asked for this separately-ordered extra. Nothing is
    added to a purchase order because a dropdown happened to have a first entry."""
    return any((chosen.get(field_key_for(step)) or "").strip() for step in group)


def _group_title(group: dict, index: int) -> str:
    """What to call a group in a sentence - its base part's name where it has one."""
    for position, (step, options) in enumerate(group.items()):
        if index and position == 0 and len(options) == 1:
            return next(iter(options), step)
    return "Luminaire" if not index else next(iter(group), "extra")


def exenia_order_codes(specsheet, chosen: dict):
    """-> (codes, uncoded), the codes this configuration orders and the names of any extras
    that were asked for but that Exenia publishes no code for.

    The luminaire is all-or-nothing: if its own code is not fully determined, nothing is
    returned, because a partially right part number is the one outcome worth failing to
    avoid. An extra is different. Refusing the whole line because a flush-mounting adapter
    has no published code would throw away a luminaire code that is completely correct, and
    the buyer needs that far more than they need the line to be all-or-nothing. So the codes
    that exist are returned, and the extra that has none is named so the omission is on the
    page rather than silent.
    """
    groups = code_groups(specsheet)
    if not groups:
        return [], []
    first = _compose_group(specsheet, groups[0], chosen, True)
    if not first:
        return [], []
    codes, uncoded = [first], []
    for index, group in enumerate(groups[1:], 1):
        if not _group_requested(group, chosen):
            continue
        extra = _compose_group(specsheet, group, chosen, False)
        if extra:
            codes.append(extra)
        else:
            uncoded.append(_group_title(group, index))
    return codes, uncoded


def compose_exenia_codes(specsheet, chosen: dict) -> list:
    """Just the codes - see exenia_order_codes for the full answer."""
    return exenia_order_codes(specsheet, chosen)[0]


def compose_exenia_code(specsheet, chosen: dict) -> str:
    """The codes this configuration orders, written the way Exenia's own configurator writes
    them: the luminaire, then each separately-ordered extra, joined by "+". Any extra Exenia
    publishes no code for is named in a parenthetical - prose, so it can never be mistaken
    for part of the part number, but visible, so nobody orders from a code that silently
    leaves something out."""
    codes, uncoded = exenia_order_codes(specsheet, chosen)
    if not codes:
        return ""
    text = " + ".join(codes)
    if uncoded:
        text += f"  (no Exenia code published for: {', '.join(uncoded)})"
    return text


def config_layout(specsheet) -> list:
    """How Exenia's own configurator lays this product out, for the Configurator to mirror.

    -> [{"title", "optional", "fields": [field keys, in the order Exenia asks them]}]

    The point is not decoration. A specifier reads a code off Exenia's page by working down
    it, and if our form asks the same questions in a different order, split across a grid,
    with an unrelated `IP Rating` box among them, the two cannot be checked against each
    other - which is exactly how a mismatched code goes unnoticed. Ordering ours the way
    theirs runs makes the two comparable line for line.
    """
    groups = code_groups(specsheet)
    if len(groups) < 2 and not any(
            len(o) > 1 for g in groups for o in g.values()):
        return []
    out = []
    for index, group in enumerate(groups):
        fields, title = [], ""
        for position, (step, options) in enumerate(group.items()):
            low = step.lower()
            if low in BODY_STEPS or low == VERSION_STEP:
                continue
            # A group's opening accessory step names the extra rather than asking anything -
            # "Primary optical accessories: Decorative ring" IS the decorative ring.
            if index and position == 0 and len(options) == 1:
                title = next(iter(options), step)
                continue
            key = field_key_for(step)
            for half in _required_halves(key, list(options)):
                if half not in fields:
                    fields.append(half)
        if not fields and not title:
            continue
        out.append({
            "title": title or ("Luminaire" if not index else step),
            "optional": bool(index),
            "fields": fields,
        })
    return out if len(out) > 1 or (out and out[0]["fields"]) else []
