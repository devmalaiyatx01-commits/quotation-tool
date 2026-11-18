"""The studio's own product code: what it is made of, and how one is assembled.

A product code is an address. One configured product - one SKU - one code:
"DL-RC-CRV-AD-MC-WT-LR-7W-27K-CRI95-15D-WH-MW-IP20" reads Downlight, Recessed, CURVE,
Adjustable, Micro, With Trim, Lens + Reflector - which product it is - and then 7W, 2700K,
CRI 95, 15 degrees, white body, matte white reflector, IP20 - how it was configured. A code
says only what the product states: a segment it has no value for is left out, never filled
with what is usual, and every choice it offers when quoted is a segment of its own.

Everything that decides the code is data, edited in NPD, so a new axis or a changed
abbreviation never waits on a developer:

  * CodeAttribute  - one thing a code can say (trim, wattage) and the rule that turns a value
                     of it into letters when nobody has typed an abbreviation.
  * FieldCode      - the abbreviation for one value ("WITH TRIM" -> "WT"). The same table the
                     Config Rules panel and NPD's identity boxes already write.
  * CodeScheme     - which attributes a typology's code carries, in which order.

This module is the only place a code is assembled. The NPD form's preview, the Configurator's
code panel, the BOQ description and the Match tab all ask it, because two copies of this logic
disagreeing is exactly how a wrong code reaches a purchase order.
"""
import copy
import hashlib
import itertools
import re
import threading
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.orm import load_only

from . import models
from .npd import split_options as _split_printed

# The ways a value can become a code without anyone typing one. An abbreviation typed for a
# value always wins over its attribute's rule, so a rule only has to be right for the common
# case - "Wall Wash" is WW by abbreviation while every numbered beam is coded by the rule.
RULES = {
    "list": ("Abbreviations only",
             "Only values given an abbreviation below get a code.",
             "WITH TRIM → WT (typed)"),
    "number": ("The number",
               "The first number in the value, between the letters set under Written as. A "
               "decimal point becomes P, so the code never carries a full stop.",
               "7W \u2192 7W \u00b7 14.4W \u2192 14P4W \u00b7 > 95 (R9 > 80) \u2192 CRI95"),
    "numbers": ("Every number, joined with X",
                "Each number in the value, in order, joined with X and set between the letters "
                "under Written as - for beams, elliptical beams and cross-sections.",
                "24\u00b0 \u2192 24D \u00b7 18\u00b0X20\u00b0 \u2192 18X20D \u00b7 10 x 10 mm \u2192 10X10"),
    "kelvin": ("First two digits of the Kelvin",
               "The first two digits of the colour temperature, between the letters set under "
               "Written as.",
               "3000K \u2192 30K \u00b7 2700K \u2192 27K"),
    "digits": ("All the digits",
               "Every digit in the value, run together.",
               "10mm x 10mm → 1010"),
    "text": ("The value as written",
             "The value itself in capitals, with spaces and punctuation taken out.",
             "S 10 → S10 · LL 103002 → LL103002"),
}

# A code must say what it is. "7" could be a wattage, a size or a CCT; "7W" can only be one of
# them. So every segment of a code carries at least one letter: a rule's number gets the
# attribute's prefix or suffix (CRI95, 27K, 15D), a typed abbreviation must have one of its own,
# and a code of digits alone counts as missing rather than being printed.
REASON_NONE, REASON_DIGITS = "none", "digits"
# A value that lists several options - a driver "IP20 / IP67" - on a product that does not offer
# them as a choice. The product does not say which one it is, so no code can say it either.
REASON_SEVERAL = "several"
# A library accessory with no code of its own yet (app/accessory_parts.py) - given one in the
# accessory library, never borrowed from its name, which its variants share.
REASON_LIBRARY = "library"


def explicit(code):
    """Whether a code says what it is - it has at least one letter."""
    return bool(re.search(r"[A-Z]", str(code or "").upper()))


PARTS = ("identity", "config")
SEPARATORS = ["-", ".", "/", "_", " ", ""]

# Attributes whose value is a column of its own on the specsheet rather than a field.
COLUMN_KEYS = ("typology", "series", "family", "variant", "make")

# What a datasheet writes when a parameter does not apply - "ADJUSTABILITY: NA" on a suspended
# track light. It is not a value to abbreviate, it is the absence of one. NIL is InHouse's way of
# saying it in an accessory list ("NIL / HONEYCOMB / SPREAD LENS"): no accessory, which the rest of
# the tool already drops from its accessory pickers - so a code never carries a segment for it.
NOT_APPLICABLE = {"NA", "N/A", "N.A.", "N.A", "-", "–", "NONE", "NOT APPLICABLE", "NIL"}

# Statuses a segment can end in. Only the first two keep a code complete.
OK, SKIPPED = "ok", "skipped"
CHOOSE, NO_VALUE, NO_CODE, OFF = "choose", "no_value", "no_code", "off"

# An accessory is not part of what the product is - it is ordered with it, after it. So each
# accessory's code is added with a plus rather than the structure's separator:
# "DL-RC-NYX-...-IP44+HNYXMD+SFLNYXMD" reads as the product, then what goes with it.
# static/app.js (npdCodeSpans) writes a code the same way.
ACCESSORY_JOIN = "+"


def norm(value):
    """How two spellings of one value are recognised as the same value: case, spacing and the
    en dash InHouse types for a hyphen are not differences. "ON –OFF" is "ON-OFF", and
    "LENS+REFLECTOR" is "LENS + REFLECTOR"."""
    text = str(value or "").upper().replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", text)


def clean_code(code):
    """An abbreviation as stored: capitals, no spaces."""
    return re.sub(r"\s+", "", str(code or "")).upper()


def _fmt_number(text):
    """"14.4" -> "14P4", "10" -> "10", "9,60" -> "9P6"."""
    text = text.replace(",", ".")
    if "." in text:
        whole, frac = text.split(".", 1)
        frac = frac.rstrip("0")
        return whole + ("P" + frac if frac else "")
    return text


def rule_code(rule, value):
    """The code a rule gives a value, or "" when the rule has nothing to say about it."""
    text = str(value or "").strip()
    if not text:
        return ""
    numbers = re.findall(r"\d+(?:[.,]\d+)?", text)
    if rule == "number":
        return _fmt_number(numbers[0]) if numbers else ""
    if rule == "numbers":
        return "X".join(_fmt_number(n) for n in numbers)
    if rule == "kelvin":
        for n in numbers:
            whole = n.replace(",", ".").split(".")[0]
            if len(whole) >= 4:
                return whole[:2]
        return ""
    if rule == "digits":
        return "".join(re.findall(r"\d", text))
    if rule == "text":
        return re.sub(r"[^A-Z0-9]", "", text.upper())
    return ""


def split_options(value):
    """The options a printed value offers - "WHITE / BLACK" is two, and "7.2W/M / 9.6W/M" is two
    as well: the one rule, npd.split_options, which knows W/M is a unit."""
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return _split_printed(value)


def suggest_code(value, taken=()):
    """A starting abbreviation for a value nobody has coded - offered, never stored unasked.

    Initials for several words (MATTE WHITE -> MW, IP68 CONNECTOR -> IC), the first three letters
    of one word (MEDIUM -> MED), and a digit added only where that code is already taken within
    the attribute (END CAPS -> EC2 beside END CAP -> EC)."""
    words = re.findall(r"[A-Z]+|\d+", str(value or "").upper())
    letters = [w for w in words if w.isalpha()]
    if len(letters) >= 2:
        base = "".join(w[0] for w in letters[:4])
    elif letters:
        base = letters[0][:3]
    else:
        return ""
    taken = {clean_code(t) for t in taken}
    if base not in taken:
        return base
    n = 2
    while "%s%d" % (base, n) in taken:
        n += 1
    return "%s%d" % (base, n)


# ---------------------------------------------------------------- context


class Context:
    """Everything a code is assembled from, read once per request: the attributes by key and
    every abbreviation, indexed by attribute and by the normalised value."""

    def __init__(self, attributes, field_codes):
        self.attributes = {a.key: a for a in attributes}
        self.codes = {}
        for row in field_codes:
            if (row.code or "").strip():
                self.codes.setdefault(row.field, {})[norm(row.option_value)] = clean_code(row.code)
        # {(attribute key, normalised value): code} that win over the abbreviations above for one
        # product - a library accessory's own code (with_overrides). "" is a value that has no code
        # and must not borrow one.
        self.overrides = {}

    def with_overrides(self, key, codes):
        """A copy of this context in which these values of `key` are coded by `codes` first.

        For a InHouse product's accessories (app/accessory_parts.py): each library accessory has a
        code of its own, so a U Shape Profile in 35x35 and one in 16x16 put different letters into
        the product's code - which an abbreviation keyed by the shared name never could. One without
        a code is reported missing rather than given the name's abbreviation."""
        other = copy.copy(self)
        other.overrides = dict(self.overrides)
        for value, code in (codes or {}).items():
            other.overrides[(key, norm(value))] = clean_code(code)
        return other

    def attribute(self, key):
        a = self.attributes.get(key)
        if a is not None:
            return a
        # An attribute removed while a scheme still names it: still shown, coded by
        # abbreviation only, so the gap is visible rather than silently dropped.
        return SimpleNamespace(key=key, label=key.replace("_", " ").title(), part="identity",
                               rule="list", multi=False, note="", prefix="", suffix="")

    def typed(self, key, value):
        """The abbreviation somebody typed for exactly this value, or ""."""
        return self.codes.get(key, {}).get(norm(value), "")

    def code_for(self, key, value):
        """(code, where it came from): "set" for a typed abbreviation, "rule" for one derived,
        ("", "digits") for one that would be digits alone, ("", "") for a value nothing codes,
        "library" / ("", "library") for a library accessory's own code, or its lack of one."""
        if (key, norm(value)) in self.overrides:
            code = self.overrides[(key, norm(value))]
            return (code, "library") if code and explicit(code) else ("", REASON_LIBRARY)
        typed = self.typed(key, value)
        if typed:
            return (typed, "set") if explicit(typed) else ("", REASON_DIGITS)
        attr = self.attribute(key)
        derived = rule_code(attr.rule, value)
        if not derived:
            return "", ""
        derived = (clean_code(getattr(attr, "prefix", "") or "") + derived +
                   clean_code(getattr(attr, "suffix", "") or ""))
        return (derived, "rule") if explicit(derived) else ("", REASON_DIGITS)

    def suggestion(self, key, value):
        """What the panel offers for a value with no code - stored only once someone accepts it.

        A value that is digits alone ("1") borrows the initials of what it is a value of, so the
        suggestion still says what it is: series variant 1 is SV1, never a bare 1."""
        taken = self.codes.get(key, {}).values()
        found = suggest_code(value, taken)
        digits = "".join(re.findall(r"\d+", str(value or "")))
        if found or not digits:
            return found
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", self.attribute(key).label))[:2].upper()
        candidate = initials + digits
        # Only offered when free - a suggestion that clashed would be worse than none.
        return candidate if initials and candidate not in {clean_code(t) for t in taken} else ""


def load_context(db, discipline):
    attrs = (db.query(models.CodeAttribute)
             .filter(models.CodeAttribute.discipline == discipline)
             .order_by(models.CodeAttribute.sort_order, models.CodeAttribute.id).all())
    keys = [a.key for a in attrs]
    rows = (db.query(models.FieldCode).filter(models.FieldCode.field.in_(keys)).all()
            if keys else [])
    return Context(attrs, rows)


def scheme_for(db, discipline, make, typology):
    """The scheme a product is coded by: its typology's own, else its brand's default, else
    None - a brand nobody has given a scheme keeps whatever code it had before. A retired
    scheme (put back on the brand default, kept only so it can be restored) codes nothing."""
    make = (make or "").strip()
    if not make:
        return None
    rows = (db.query(models.CodeScheme)
            .filter(models.CodeScheme.discipline == discipline,
                    models.CodeScheme.make == make,
                    models.CodeScheme.retired_at.is_(None),
                    models.CodeScheme.typology.in_([(typology or "").strip(), ""])).all())
    exact = [r for r in rows if (r.typology or "") == (typology or "").strip() and r.typology]
    default = [r for r in rows if not (r.typology or "")]
    return (exact or default or [None])[0]


# ---------------------------------------------------------------- one product's values


def raw_value(sheet, key):
    if key in COLUMN_KEYS:
        return getattr(sheet, key, "") or ""
    value = (getattr(sheet, "fields", None) or {}).get(key)
    return "" if value is None else value


def offered(sheet, key):
    """The options a product offers for one attribute when it is quoted, or None when it offers
    no choice there. NPD keeps an option list only for a parameter the product marks as a choice
    (npd.assemble), and the Configurator asks exactly those - so the list is the product's own
    statement that this is chosen. A value that merely reads "IP20 / IP67" without one is not
    offered as a choice, and is not coded as if it were."""
    opts = (getattr(sheet, "options", None) or {}).get(key)
    if not isinstance(opts, list):
        return None
    clean = [str(o).strip() for o in opts if str(o).strip()]
    return clean or None


def product_options(sheet, key):
    """Every option a product lists for one attribute: its option list where it has one,
    otherwise its printed value split on "/". For a many-valued attribute (accessories). "NIL"
    and the like are the absence of an option, not one of them."""
    return [o for o in (offered(sheet, key) or split_options(raw_value(sheet, key)))
            if o.upper() not in NOT_APPLICABLE]


def _chosen_list(value):
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return split_options(value)


def segment(ctx, seg, sheet, chosen):
    """One segment of one product's code: its value, its code and whether it is settled.

    The value is only ever what the product states: its own value, or the option chosen from the
    ones it offers. Nothing is filled in for a product that says nothing - a segment it has no
    value for is left out where the structure allows that, and reported missing where not."""
    key = seg.get("key") or ""
    attr = ctx.attribute(key)
    out = {"key": key, "label": attr.label, "part": attr.part or "identity",
           "include": seg.get("include", True) is not False,
           "optional": bool(seg.get("optional")),
           "value": "", "code": "", "source": "", "status": OK, "choices": []}

    if attr.multi:
        # Zero or many - accessories. Nothing chosen is a complete answer ("none"), not a gap,
        # and a "NIL" that arrives as a choice says the same thing.
        picked = [p for p in _chosen_list((chosen or {}).get(key)) if p.upper() not in NOT_APPLICABLE]
        if not picked:
            out["status"] = SKIPPED
            out["choices"] = [_choice(ctx, key, o) for o in product_options(sheet, key)]
        else:
            codes, missing = [], []
            for p in picked:
                code, _src = ctx.code_for(key, p)
                if code:
                    codes.append(code)
                else:
                    missing.append(p)
            out["value"] = " / ".join(picked)
            out["code"] = "" if missing else "+".join(codes)
            out["codes"] = [] if missing else codes
            out["source"] = "set"
            out["status"] = NO_CODE if missing else OK
            out["missing"] = missing
    else:
        options = offered(sheet, key)
        value = ""
        if options and len(options) > 1:
            # A choice the product offers - a finish, a CCT, a grazer's fixed or adjustable
            # bracket. Each option is a SKU of its own, so each is coded, whether the attribute
            # names the product or configures it.
            picked = str((chosen or {}).get(key) or "").strip()
            if picked:
                value = picked
            else:
                out["status"] = CHOOSE
                out["choices"] = [_choice(ctx, key, o) for o in options]
        elif options:
            value = options[0]
        else:
            value = str(raw_value(sheet, key) or "").strip()
            if len(split_options(value)) > 1 and not ctx.typed(key, value):
                # Several options written down, none offered as a choice. Coding the first
                # would claim something the product does not say; an abbreviation typed for the
                # whole value is somebody's decision that it is one value, and is used.
                out["value"] = value
                out["status"] = SKIPPED if out["optional"] else NO_VALUE
                out["reason"] = REASON_SEVERAL
                value = ""
        if value.upper() in NOT_APPLICABLE:
            value = ""
        if out["status"] == OK:
            if not value:
                out["status"] = SKIPPED if out["optional"] else NO_VALUE
            else:
                code, source = ctx.code_for(key, value)
                out.update(value=value, code=code, source=source if code else "")
                if not code:
                    out["status"] = NO_CODE
                    out["reason"] = source or REASON_NONE
                    out["suggestion"] = ctx.suggestion(key, value)
    if not out["include"]:
        out["status_if_included"] = out["status"]
        out["status"] = OFF
    return out


def _choice(ctx, key, option):
    code, source = ctx.code_for(key, option)
    out = {"value": option, "code": code, "source": source if code else ""}
    if not code:
        out["reason"] = source or REASON_NONE
        out["suggestion"] = ctx.suggestion(key, option)
    return out


def compose(ctx, scheme, sheet, chosen=None):
    """The code for one product, configured by `chosen` ({attribute key: value}).

    `code` is only ever a complete code - "" while any segment is unsettled, so a partial one
    cannot be copied onto an order by accident. `partial` shows the same code with a "?" for
    each gap and a "…" for each choice still to make, for the screens that explain what is
    missing. The identity part - the segments
    that name the product - is returned on its own too: it must be unique across the brand."""
    sep = "" if scheme is None else (scheme.separator if scheme.separator is not None else "-")
    prefix = clean_code(getattr(scheme, "prefix", "") or "")
    segs = [segment(ctx, s, sheet, chosen) for s in (getattr(scheme, "segments", None) or [])]

    def assemble(which):
        bits, added, whole = ([prefix] if prefix else []), [], True
        for s in which:
            if s["status"] == OK and s.get("codes"):
                added.extend(s["codes"])        # accessories: after the product, each with a +
            elif s["status"] == OK:
                bits.append(s["code"])
            elif s["status"] in (CHOOSE, NO_VALUE, NO_CODE):
                # "…" is a choice still to be made when quoting; "?" is something missing.
                # The screens colour them the same way, so the text should say the same thing.
                bits.append("…" if s["status"] == CHOOSE else "?")
                whole = False
        return sep.join(bits) + "".join(ACCESSORY_JOIN + c for c in added), whole

    partial, complete = assemble(segs)
    ident_segs = [s for s in segs if s["part"] == "identity"]
    identity_partial, identity_complete = assemble(ident_segs)
    return {
        "scheme_id": getattr(scheme, "id", None),
        "typology": getattr(scheme, "typology", "") if scheme else "",
        "complete": complete and bool(segs),
        "code": partial if complete and segs else "",
        "partial": partial,
        "identity_complete": identity_complete and bool(ident_segs),
        "identity_code": identity_partial if identity_complete and ident_segs else "",
        "identity_partial": identity_partial,
        "segments": segs,
        "missing": [s["label"] for s in segs if s["status"] in (NO_VALUE, NO_CODE)],
        "to_choose": [s["label"] for s in segs if s["status"] == CHOOSE],
    }


def configurations(result, separator="-", prefix=""):
    """Every SKU of one product: each combination of the options it offers, with its code.

    `result` is compose() with nothing chosen. Yields (code, chosen) - code is "" for a
    combination some segment of which cannot be coded (a gap, or an option with no code), so
    those are counted rather than silently dropped. Accessories are not combined here: each is
    ordered as a line of its own, and what they add to a code is checked separately."""
    slots = []                                     # per segment: [(code, chosen-or-None)]
    added = ""                                     # accessories, after the product as compose() puts them
    for s in result["segments"]:
        if s["status"] == OK and s.get("codes"):
            added += "".join(ACCESSORY_JOIN + c for c in s["codes"])
        elif s["status"] == OK:
            slots.append([(s["code"], None)])
        elif s["status"] == CHOOSE:
            slots.append([(c["code"], (s["key"], c["value"])) for c in s["choices"]])
        elif s["status"] in (NO_VALUE, NO_CODE):
            slots.append([("", None)])
        # SKIPPED and OFF say nothing, and take no place in the code
    head = [prefix] if prefix else []
    for combo in itertools.product(*slots):
        chosen = dict(c for _code, c in combo if c)
        codes = [code for code, _c in combo]
        yield ("" if any(not c for c in codes) else separator.join(head + codes) + added), chosen


def sku_audit(entries):
    """Whether every SKU of a set of products has a code of its own.

    `entries` is [(product id, compose() result, separator, prefix)]. Returns per-product counts
    - SKUs, and how many cannot be coded - and every code two SKUs share, with the products and
    the choices behind each. Two SKUs with one code is the thing the scheme exists to prevent,
    within one product (two options coded alike) as much as between two."""
    seen, clashes, per = {}, {}, {}
    for pid, result, sep, prefix in entries:
        total = uncoded = 0
        for code, chosen in configurations(result, sep, prefix):
            total += 1
            if not code:
                uncoded += 1
                continue
            first = seen.setdefault(code, (pid, chosen))
            if first != (pid, chosen):
                clashes.setdefault(code, [first]).append((pid, chosen))
        per[pid] = {"skus": total, "uncoded": uncoded}
    return per, clashes


def code_for_sheet(db, sheet, chosen=None):
    """compose() for a saved product, or None when no scheme covers its brand."""
    discipline = getattr(sheet, "discipline", None) or models.DEFAULT_DISCIPLINE
    scheme = scheme_for(db, discipline, sheet.make, sheet.typology)
    if scheme is None:
        return None
    return compose(with_library_accessories(db, load_context(db, discipline), sheet=sheet),
                   scheme, sheet, chosen)


def with_library_accessories(db, ctx, sheet=None, part_ids=None):
    """`ctx` with a InHouse product's library accessories coded by their own codes - the saved
    product's (`sheet`), or a draft's picks (`part_ids`). See Context.with_overrides."""
    from . import accessory_parts          # imported here: it imports this module, through accessories
    codes = accessory_parts.code_overrides(db, sheet=sheet, part_ids=part_ids)
    return ctx.with_overrides("accessories", codes) if codes else ctx


def draft_sheet(body):
    """A product as the NPD form currently holds it, shaped like a Specsheet for compose()."""
    values = dict(body.get("values") or {})
    for key in ("sub_typology", "model_no"):
        if str(body.get(key) or "").strip():
            values[key] = str(body[key]).strip()
    # Only what the form marks as a choice becomes one, as npd.assemble() does on save - so the
    # code shown while typing is the code the saved product gets. A form that says nothing
    # (an older caller) has every multi-option value taken as a choice, as before.
    affects = body.get("affects")
    options = {}
    for key, value in values.items():
        if isinstance(affects, dict) and not affects.get(key):
            continue
        opts = split_options(value)
        if len(opts) > 1:
            options[key] = opts
    for key, opts in (body.get("options") or {}).items():
        if isinstance(opts, list) and opts:
            options[key] = [str(o) for o in opts]
    return SimpleNamespace(
        typology=(body.get("typology") or "").strip(), series=(body.get("series") or "").strip(),
        family=(body.get("family") or "").strip(), variant=(body.get("variant") or "").strip(),
        make=(body.get("make") or "").strip(), fields=values, options=options)


def values_in_use(sheets, key, part, multi, typed=None):
    """{value: number of products} for one attribute across a set of products - what the
    abbreviation list is built from, so it lists what the catalogue actually says.

    `typed` is Context.typed: pass it so a value is counted exactly when segment() would code it."""
    counts, spelled = {}, {}
    for s in sheets:
        if multi:
            vals = product_options(s, key)
        else:
            # What a code can carry: each option offered, or the product's one value. A value
            # listing several options that are not offered is not coded, so it is not listed
            # for an abbreviation either - unless an abbreviation was typed for the whole of it
            # ("Flexible Neon / LED Band" is NB), which segment() then codes as one value. Left
            # out, those showed as used by no product at all, and looked safe to delete.
            vals = offered(s, key)
            if vals is None:
                v = str(raw_value(s, key) or "").strip()
                one = len(split_options(v)) < 2 or bool(typed and typed(key, v))
                vals = [v] if v and one else []
            # "NA" is the absence of a value, never coded - so never asked for an abbreviation
            vals = [v for v in vals if v.upper() not in NOT_APPLICABLE]
        for v in dict.fromkeys(vals):
            n = norm(v)
            spelled.setdefault(n, v)
            counts[n] = counts.get(n, 0) + 1
    return [(spelled[n], counts[n]) for n in counts]


# ---------------------------------------------------------------- a brand, coded once
#
# Three screens code every product of a brand: Nomenclature (which products are complete, and
# which share a code), the NPD list (each card's code) and the code preview, which checks that
# none of a product's codes is already another product's. Each used to compose every product and
# expand every configuration on every request - 53,243 SKUs for InHouse, about 0.8 s on a laptop
# per preview and several times that on the live server, for 2,109 previews in the week of 3-9 Oct
# 2026. None of it changes while somebody types into a draft, so it is built once per process
# and kept until something it is built from changes.
#
# That is checked on every use, by one query fingerprinting all of it: the brand's products
# (everything compose() reads, and the names shown beside the codes), its code structures, the
# attributes and every abbreviation. A change made through either worker, by a script or straight
# in the database changes the fingerprint, so a stale answer is never served - there is no expiry
# to tune and nothing that has to remember to clear it.
#
# Only what is the same for every caller is kept: each product coded with nothing chosen, from
# the shared abbreviations. That is exactly what the three screens use. A library accessory's own
# code (with_library_accessories) never reaches it: accessories are skipped when nothing is
# chosen (segment()) and are never part of a configuration (configurations()).

_FINGERPRINT_SQL = text("""
SELECT md5(concat_ws(chr(29),
  (SELECT string_agg(ROW(s.id, s.product_name, s.origin, s.approval_status, s.typology, s.series,
                         s.family, s.variant, s.make, s.fields::text, s.options::text)::text,
                     chr(30) ORDER BY s.id)
     FROM specsheets s WHERE s.discipline = :discipline AND s.make = :make),
  (SELECT string_agg(ROW(c.id, c.typology, c.prefix, c.separator, c.segments::text, c.retired_at)::text,
                     chr(30) ORDER BY c.id)
     FROM code_schemes c WHERE c.discipline = :discipline AND c.make = :scheme_make),
  (SELECT string_agg(row_to_json(a)::text, chr(30) ORDER BY a.id)
     FROM code_attributes a WHERE a.discipline = :discipline),
  (SELECT string_agg(ROW(f.id, f.field, f.option_value, f.code)::text, chr(30) ORDER BY f.id)
     FROM field_codes f),
  (SELECT string_agg(ROW(p.id, p.name, p.code, p.params::text)::text, chr(30) ORDER BY p.id)
     FROM accessory_parts p WHERE p.discipline = :discipline),
  (SELECT string_agg(ROW(l.id, l.specsheet_id, l.part_id, l.sort_order)::text, chr(30) ORDER BY l.id)
     FROM accessory_links l)))
""")


def _fingerprint(db, discipline, make):
    """One value that changes whenever anything a brand's codes are built from changes."""
    params = {"discipline": discipline, "make": make, "scheme_make": (make or "").strip()}
    if db.bind.dialect.name == "postgresql":
        return db.execute(_FINGERPRINT_SQL, params).scalar()
    # Any other database (a SQLite development copy): the same inputs, hashed here.
    h = hashlib.md5()
    for sql in ("SELECT id, product_name, origin, approval_status, typology, series, family, variant, make, "
                "fields, options FROM specsheets WHERE discipline = :discipline AND make = :make ORDER BY id",
                "SELECT id, typology, prefix, separator, segments, retired_at FROM code_schemes "
                "WHERE discipline = :discipline AND make = :scheme_make ORDER BY id",
                "SELECT * FROM code_attributes WHERE discipline = :discipline ORDER BY id",
                "SELECT id, field, option_value, code FROM field_codes ORDER BY id",
                # library accessories' own codes: Nomenclature's value table shows them (18 Nov 2026)
                "SELECT id, name, code, params FROM accessory_parts WHERE discipline = :discipline ORDER BY id",
                "SELECT id, specsheet_id, part_id, sort_order FROM accessory_links ORDER BY id"):
        for row in db.execute(text(sql), params):
            h.update(repr(tuple(row)).encode("utf-8", "replace"))
        h.update(b"\x1d")
    return h.hexdigest()


class BrandCodes:
    """Every product of one brand, coded once - see brand_codes()."""

    def __init__(self, fingerprint):
        self.fingerprint = fingerprint
        self.names = {}       # id -> product name, for every product of the brand
        self.results = {}     # id -> (compose() with nothing chosen, separator, prefix), coded products
        self.per = {}         # id -> {"skus", "uncoded"}, products that are not held
        self.clashes = {}     # code -> [(id, chosen), ...] for a code two SKUs share, as sku_audit()
        self.owner = {}       # code -> the first product (not held) to have it
        self._memo = {}

    def owners(self, code):
        """Every product (not held) that has this code."""
        first = self.owner.get(code)
        if first is None:
            return set()
        return {first} | {pid for pid, _c in self.clashes.get(code, ())}

    def first_shared(self, pid, codes):
        """The first of product `pid`'s codes, in configuration order, that is in `codes`."""
        result, sep, prefix = self.results[pid]
        return next((code for code, _c in configurations(result, sep, prefix) if code in codes), None)

    def memo(self, name, build):
        """Something else derived only from the same inputs, built once and kept as long as this.
        `build` must read nothing the fingerprint does not cover."""
        if name not in self._memo:
            self._memo[name] = build()
        return self._memo[name]


_BRANDS = {}
_BRANDS_LOCK = threading.Lock()


def brand_codes(db, discipline, make):
    """The brand's products coded with nothing chosen, built once per process and rebuilt only
    when the fingerprint of what it is built from changes. The fingerprint is read before
    anything else, so what is built is never older than the fingerprint it is kept under."""
    make = make or ""
    fingerprint = _fingerprint(db, discipline, make)
    key = (discipline, make)
    entry = _BRANDS.get(key)
    if entry is not None and entry.fingerprint == fingerprint:
        return entry
    with _BRANDS_LOCK:                 # one build at a time: on the live server it is seconds of CPU
        entry = _BRANDS.get(key)
        if entry is None or entry.fingerprint != fingerprint:
            entry = _build_brand(db, discipline, make, fingerprint)
            _BRANDS[key] = entry
    return entry


def _build_brand(db, discipline, make, fingerprint):
    entry = BrandCodes(fingerprint)
    ctx = load_context(db, discipline)
    # The structures as scheme_for() picks them: the typology's own, else the brand's default.
    schemes = (db.query(models.CodeScheme)
               .filter(models.CodeScheme.discipline == discipline,
                       models.CodeScheme.make == make.strip(),
                       models.CodeScheme.retired_at.is_(None)).all()) if make.strip() else []
    own = {}
    for s in schemes:
        if s.typology:
            own.setdefault(s.typology, s)
    default = next((s for s in schemes if not (s.typology or "")), None)
    sheets = (db.query(models.Specsheet)
              .options(load_only(models.Specsheet.id, models.Specsheet.product_name,
                                 models.Specsheet.approval_status, models.Specsheet.typology,
                                 models.Specsheet.series, models.Specsheet.family,
                                 models.Specsheet.variant, models.Specsheet.make,
                                 models.Specsheet.fields, models.Specsheet.options))
              .filter(models.Specsheet.discipline == discipline, models.Specsheet.make == make)
              # Nomenclature's order, so the first two products sharing a code - the example it
              # shows - are the ones it always showed.
              .order_by(models.Specsheet.typology, models.Specsheet.product_name, models.Specsheet.id)
              .all())
    entries = []
    for s in sheets:
        entry.names[s.id] = s.product_name
        scheme = own.get((s.typology or "").strip()) or default
        if scheme is None:
            continue
        sep = scheme.separator if scheme.separator is not None else "-"
        prefix = clean_code(scheme.prefix or "")
        result = compose(ctx, scheme, s, {})
        entry.results[s.id] = (result, sep, prefix)
        # A held product is never ordered, so it can neither clash nor be missing a code - nor can
        # one deleted from NPD that BOQs still keep (main.NPD_DELETED).
        if s.approval_status not in ("held", "deleted"):
            entries.append((s.id, result, sep, prefix))
    # sku_audit(), and who has each code, in the one pass over every configuration.
    seen = {}
    for pid, result, sep, prefix in entries:
        total = uncoded = 0
        for code, chosen in configurations(result, sep, prefix):
            total += 1
            if not code:
                uncoded += 1
                continue
            first = seen.setdefault(code, (pid, chosen))
            if first != (pid, chosen):
                entry.clashes.setdefault(code, [first]).append((pid, chosen))
            entry.owner.setdefault(code, pid)
        entry.per[pid] = {"skus": total, "uncoded": uncoded}
    return entry
