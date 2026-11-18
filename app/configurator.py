"""Which details the Configurator asks, for one brand's typology - decided in NPD.

When a product is added to a BOQ, the Configurator asks for its details: the Configure tab's
filters, and the product's own form after it is picked. Until 21 Oct 2026 which details those
were was fixed in code (config_rules.FIELD_ORDER) and guessed from whatever each product held.
Now whoever looks after a brand in NPD decides it per typology, and this module is the one place
that turns that decision into the list both screens ask - so the two can never disagree.

The rule that bounds the decision: a detail some product of the typology offers a CHOICE of (two
CCTs, five beams) is always asked. A BOQ line has to say which one, or it names no orderable
product - its code cannot complete, and the price adder of the option chosen is missed. So the
admin decides the order of everything, and whether each FIXED detail (one value per product: a
CRI, an IP rating, a UGR) is shown. A choice left out of a saved list is put back, at the end.

A typology nobody has decided for keeps the Configurator's automatic behaviour (for_typology and
for_product return None), so nothing changes for it until someone saves a choice.

Values are read by config_rules.stated_values - a product's list of choices, or the one value it
states - the same rule the Configure tab and the product form already share.
"""
import re

from app import models
from app.config_rules import FIELD_ORDER, field_labels_for, stated_values

# Never Configurator questions. What names the product (its series, family, variant, code) is
# not a choice made when quoting it; accessories have their own picker; the dimensions have the
# dimension picker; a driver's power belongs on the driver's own row.
IDENTITY_KEYS = {"series", "family", "variant", "sub_typology", "name_line", "product_code", "model_no",
                 "make", "typology", "category", "product_name", "type", "size", "version"}
# DuraGreen's printed `version` is a real choice all the same: while nobody has decided a typology in
# NPD, duragreen.configure_extras asks it in Configure, since it picks which printed code applies.
ASKED_ELSEWHERE = {"accessories", "components", "cutout_dimensions", "luminaire_length",
                   "profile_dimension", "driver_power"}
# Details that are never asked as a choice, even where a product lists several values: they
# describe the product or follow from another choice rather than being picked. A lumen package
# follows from the wattage (6W is 510 lm) - asked on its own it would let a line read 6W and
# 1530 lm. A driver's power belongs on the driver's row. They can still be SHOWN as details.
# main._NON_CONFIG_FIELDS is this set (the product form's own-choices list reads it too).
NOT_A_CHOICE = {"accessories", "product_segment", "luminaire_type", "collection",
                "certifications", "compliances", "lifecycle", "regional_availability",
                "material", "warranty", "ugr", "lumen_maintenance", "input_voltage",
                "color_consistency", "lumen_package", "ik_rating", "driver_power"}
# A luminaire's rated life (L80, L90, L95) is a consequence of the configuration, not an input.
_DERIVED = re.compile(r"^lifetime(_|$)")
# A value this long is a paragraph, not something to pick or filter by.
LONGEST_VALUE = 60

_PUNCTUATION_ONLY = re.compile(r"^[\s+\-–—*/|.,;:]*$")
# Kept in capitals when an NPD label ("IP RATING") is put in sentence case.
_ACRONYMS = {"IP", "IK", "CRI", "CCT", "UGR", "LED", "SDCM", "DALI", "DMX", "RGB", "RGBW", "TW",
             "PWM", "R9", "LM", "DC", "AC", "UV", "PIR"}


def sentence_case(label):
    """"CUTOUT DIMENSIONS" -> "Cutout dimensions", "IP RATING" -> "IP rating", and an
    abbreviation written with dots ("C.C/ C.V", "T.H.D") left as it is."""
    words = str(label or "").replace("_", " ").split()
    out = []
    for i, w in enumerate(words):
        bare = re.sub(r"[^A-Za-z0-9]", "", w).upper()
        if bare in _ACRONYMS or "." in w:
            out.append(w.upper())
        else:
            out.append(w.capitalize() if i == 0 else w.lower())
    return " ".join(out)


def is_choice(values, key=""):
    """Two or more real, distinct values of a detail that is picked - something a buyer chooses."""
    if key in NOT_A_CHOICE or _DERIVED.match(key or ""):
        return False
    clean = {str(v).strip().lower() for v in values if str(v).strip() and not _PUNCTUATION_ONLY.match(str(v))}
    return len(clean) >= 2


def _products(db, discipline, make, typology, approved_only):
    q = db.query(models.Specsheet).filter(models.Specsheet.discipline == discipline,
                                          models.Specsheet.make == make,
                                          models.Specsheet.typology == typology)
    if approved_only:
        q = q.filter(models.Specsheet.approval_status == "approved")
    return q.all()


def _template_labels(db, discipline, typology):
    """({field_key: label}, identity keys) from the typology's NPD parameter template. Two
    parameters of the same name under different banners - the luminaire's IP RATING and the power
    supply's - are told apart by their banner, or the Configurator would ask "IP rating" twice."""
    rows = (db.query(models.ParameterTemplate)
            .filter(models.ParameterTemplate.discipline == discipline,
                    models.ParameterTemplate.typology == typology)
            .order_by(models.ParameterTemplate.sort_order, models.ParameterTemplate.id).all())
    first_with_name = {}
    labels, identity = {}, set()
    for t in rows:
        label = sentence_case(t.label)
        # The first parameter with a name (in template order) keeps it; a later one gets its banner.
        if first_with_name.setdefault(label.lower(), t.field_key) != t.field_key:
            banner = re.sub(r"\s*PARAMETERS?\s*$", "", (t.group_name or "").strip(), flags=re.I)
            label = "%s (%s)" % (label, banner.lower()) if banner else label
        labels.setdefault(t.field_key, label)
        if (t.group_name or "").strip().upper() == "IDENTITY":
            identity.add(t.field_key)
    return labels, identity


def template_labels(db, discipline, typology):
    """{field_key: name} from one typology's NPD template, as the Configurator names them."""
    return _template_labels(db, discipline, typology)[0]


def label_for(key, make, template_labels):
    """What a detail is called: its NPD parameter's name, else the brand's word for it, else the key."""
    if key in template_labels:
        return template_labels[key]
    if (make or "").lower() == "duragreen":
        from app import duragreen
        if key in duragreen.LABEL_OF:
            return duragreen.LABEL_OF[key]
    brand = field_labels_for(make)
    if key in brand:
        return brand[key]
    return sentence_case(key)


def candidates(db, discipline, make, typology, approved_only=False):
    """Every detail the products of one brand's typology hold a value for, as the NPD editor
    lists them: [{key, label, choice, products, choice_products, values}], in automatic order -
    FIELD_ORDER's ten first, then the rest by how many products have them."""
    sheets = _products(db, discipline, make, typology, approved_only)
    labels, identity = _template_labels(db, discipline, typology)
    seen = {}
    for s in sheets:
        keys = list((s.options or {}).keys()) + list((s.fields or {}).keys())
        for key in keys:
            if key in seen or key in IDENTITY_KEYS or key in ASKED_ELSEWHERE or key in identity:
                continue
            seen[key] = None
    out = []
    for key in seen:
        products = choice_products = 0
        values = []
        for s in sheets:
            vals = stated_values(s.options, s.fields, key)
            if not vals:
                continue
            products += 1
            if is_choice(vals, key):
                choice_products += 1
            for v in vals:
                if v not in values:
                    values.append(v)
        if not products or all(len(str(v)) > LONGEST_VALUE for v in values):
            continue
        name = label_for(key, make, labels)
        out.append({"key": key, "label": name, "npd_label": name, "choice": choice_products > 0,
                    "products": products, "choice_products": choice_products, "values": values[:12],
                    "more_values": max(0, len(values) - 12)})
    rank = {k: i for i, k in enumerate(FIELD_ORDER)}
    out.sort(key=lambda c: (rank.get(c["key"], len(FIELD_ORDER)), -c["products"], c["label"]))
    return out, len(sheets)


def saved(db, discipline, make, typology):
    """[(key, label or "")] saved in NPD for one brand's typology, in order - [] when nobody has
    decided. A blank label means the NPD parameter's own name."""
    rows = (db.query(models.ConfiguratorField)
            .filter(models.ConfiguratorField.discipline == discipline,
                    models.ConfiguratorField.make == make,
                    models.ConfiguratorField.typology == typology)
            .order_by(models.ConfiguratorField.sort_order, models.ConfiguratorField.id).all())
    out, seen = [], set()
    for r in rows:
        if r.field_key not in seen:
            seen.add(r.field_key)
            out.append((r.field_key, (r.label or "").strip()))
    return out


def saved_keys(db, discipline, make, typology):
    return [k for k, _label in saved(db, discipline, make, typology)]


def automatic_keys(cands):
    """What the Configurator asks for a typology nobody has decided for: the ten shared details
    the products have, then every other choice they offer - the behaviour before 21 Oct."""
    shared = [c["key"] for c in cands if c["key"] in FIELD_ORDER]
    own = [c["key"] for c in cands if c["key"] not in FIELD_ORDER and c["choice"]]
    return shared + own


def effective(cands, saved_rows):
    """The saved list as it is asked today, as candidate dicts with the saved name applied:
    details no product holds any more dropped, and any choice the list leaves out put back at
    the end (see the module docstring), marked `added_back`."""
    by_key = {c["key"]: c for c in cands}
    out = []
    for key, label in saved_rows:
        if key in by_key:
            out.append(dict(by_key[key], label=label or by_key[key]["npd_label"]))
    have = {c["key"] for c in out}
    out += [dict(c, added_back=True) for c in cands if c["choice"] and c["key"] not in have]
    return out


def for_typology(db, discipline, make, typology):
    """[{key, label, choice}] the Configure tab asks for one brand's typology (approved products
    only), or None when nobody has decided - the caller then keeps its automatic list."""
    rows = saved(db, discipline, make, typology)
    if not rows:
        return None
    cands, _n = candidates(db, discipline, make, typology, approved_only=True)
    return [{"key": c["key"], "label": c["label"], "choice": c["choice"]} for c in effective(cands, rows)]


def for_product(db, sheet):
    """[{key, label, choice}] the product form asks for one product: its typology's decided list,
    limited to the details this product holds, or None when nobody has decided for its typology.
    Whether a detail is a choice is this product's own answer - EMBER -Mini comes in one finish,
    so its Finish is shown as that one finish, not as a question."""
    discipline = sheet.discipline or models.DEFAULT_DISCIPLINE
    rows = saved(db, discipline, sheet.make or "", sheet.typology or "")
    if not rows:
        return None
    cands, _n = candidates(db, discipline, sheet.make or "", sheet.typology or "")
    out = []
    for c in effective(cands, rows):
        vals = stated_values(sheet.options, sheet.fields, c["key"])
        if vals:
            out.append({"key": c["key"], "label": c["label"], "choice": is_choice(vals, c["key"])})
    return out


def precedence(keys):
    """The order the rule engine weighs fields in: FIELD_ORDER's for the ten it knows (a rule's
    precedence must not change because a list was reordered for reading), then any other."""
    return list(FIELD_ORDER) + [k for k in keys if k not in FIELD_ORDER]
