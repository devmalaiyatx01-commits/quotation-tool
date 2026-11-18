"""Configuration-rule engine for product typologies, ported from the studio's
existing Google Apps Script product configurator (Configure.gs / evaluateAllLogic).

The original tool runs this against a single flat SKU-builder field set shared
by one product line. Here it's scoped per specsheet **category** (typology) -
each typology has its own small, fixed field set (finish, cri, cct, beam_angle, wattage,
lumen_output, length, mounting, ip_rating, control) sourced from that specsheet's own parsed options,
and its own rule set, since valid combinations differ by typology (e.g. "IP65
strip -> no DALI" doesn't mean anything for a downlight).

Rule shape (mirrors one row of the Apps Script "Settings" sheet):
    {
        "triggers": [{"field": "wattage", "values": ["24W"]}, ...]  # up to 5, AND across
                                                                      # entries, OR within `values`
        "target_fields": ["beam_angle"],
        "action": "Show Only" | "Hide Only" | "Enable Only" | "Disable Only"
                  | "Show Field" | "Hide Field",
        "affected_options": ["10x35°", "10x45°", "10x60°"],  # ignored for *Field actions
    }

Precedence, faithfully ported from the original: among rules matching the
current selections, the ones whose triggers reach *deepest* into the field
order win; ties are broken by trigger count (more specific wins); any
remaining ties are ALL applied cumulatively (Hide Only rules remove options,
Show Only rules narrow to a union, then the union is intersected in - exactly
the two-pass "hide then whitelist" behavior in evaluateAllLogic).
"""

import re

FIELD_VIS_ACTIONS = {"Show Field", "Hide Field"}
OPTION_VIS_ACTIONS = {"Show Only", "Hide Only"}
OPTION_ENABLE_ACTIONS = {"Enable Only", "Disable Only"}
ALL_ACTIONS = FIELD_VIS_ACTIONS | OPTION_VIS_ACTIONS | OPTION_ENABLE_ACTIONS

MAX_ITERATIONS = 5

# The configurable variant fields exposed in "Add Product from Library", in
# precedence order (a rule triggered by a later field outranks one triggered
# by an earlier field, when both match with the same trigger count).
#
# The order is also the order a fixture is really specified in, coarse to fine,
# which is what makes "an earlier choice narrows a later field" read naturally:
# CRI sits before CCT because a CRI grade is a distinct LED bin and each bin is
# only produced in certain colour temperatures - you pick the die, then the
# temperature it is binned in, not the other way round.
FIELD_ORDER = ["finish", "cri", "cct", "beam_angle", "wattage", "lumen_output",
               "length", "mounting", "ip_rating", "control"]
FIELD_LABELS = {
    "finish": "Finish",
    "cri": "CRI",
    "cct": "CCT",
    "beam_angle": "Beam",
    "wattage": "Wattage",
    "lumen_output": "Lumen Output",
    "length": "Length",
    "mounting": "Mounting",
    "ip_rating": "IP Rating",
    "control": "Control",
}

# What a field is called depends on the make. Lumenpulse specifies the same axes under its
# own names - a beam angle is an "optic", a CCT is a "colour", and output is quoted in
# lumens rather than watts - and a spec written against their catalogue has to read back in
# their words or it cannot be checked against the datasheet. The KEYS stay shared, so rules,
# matching, pricing and product codes are unaffected; only the label changes.
BRAND_FIELD_LABELS = {
    # Exenia's configurator asks for an "Optic", quotes "Power" rather than wattage, and calls
    # a linear run's length a "Dimension". Its CRI and CCT steps are one combined step on
    # their site ("CRI92 3000K"); the app splits that pair into two fields, so those two keep
    # the plain names rather than being labelled with the combined step's.
    "Exenia": {
        "beam_angle": "Optic",
        "wattage": "Power",
        "length": "Dimension",
    },
    "Lumenpulse": {
        "beam_angle": "Optics (Nominal Distribution)",
        "cct": "Colour and Colour Temperature",
        "cri": "Colour Rendering",
        "lumen_output": "Output (Nominal Lumens)",
        "ip_rating": "Ingress Protection Rating",
    },
}


def field_labels_for(make: str) -> dict:
    """FIELD_LABELS with any brand-specific overrides applied."""
    return {**FIELD_LABELS, **BRAND_FIELD_LABELS.get(make or "", {})}


def stated_values(options, fields, field) -> list:
    """The values a product offers for one Configurator field, as its definition holds them
    (NPD, for an authored product): its list of choices, or - for a field it has no choice on -
    the one value it states.

    A product made in only one CRI keeps that CRI as a plain field ("> 90 (R9 > 90)"), never as
    a one-item list - all 67 InHouse downlights do. Reading the lists alone, the Configurator
    said "Not parsed from any specsheet" for a CRI that NPD shows on every one of them, and a
    product added from Configure reached the BOQ row without it while the same product added
    from Search had it. This is the rule the product form already used for a fixed value
    (fixedFieldValue in static/app.js), now the one rule for both: one value however it is
    worded; a "/" or a comma marks a list of alternatives that is not one value."""
    opts = [o for o in ((options or {}).get(field) or []) if not _PLACEHOLDER.match(str(o or ""))]
    if opts:
        return list(opts)
    value = str((fields or {}).get(field) or "").strip()
    # A dash is how NPD writes "nothing here" (an IK rating of "-"), not a value to offer.
    if _PLACEHOLDER.match(value):
        return []
    return [value] if "/" not in value and "," not in value else []


_PLACEHOLDER = re.compile(r"^[\s+\-–—*/|.,;:]*$")


def _norm(v):
    return (v or "").strip().lower()


def _match_score(rule, state, field_order):
    """None if the rule's triggers don't all match; otherwise (score, max_depth)."""
    score = 0
    max_depth = -1
    for trig in rule.get("triggers", []):
        field = trig.get("field")
        if not field:
            continue
        score += 1
        depth = field_order.index(field) if field in field_order else -1
        max_depth = max(max_depth, depth)
        values = {_norm(v) for v in trig.get("values", [])}
        if _norm(state.get(field)) not in values:
            return None
    return (score, max_depth)


def _best_rules(matches, actions):
    """From (rule, score, max_depth) tuples, keep only those whose action is in
    `actions`, then keep only the ones tied for deepest+most-specific (all of them,
    not just one - matching the original's "apply every tied rule" behavior)."""
    filtered = [m for m in matches if m[0]["action"] in actions]
    if not filtered:
        return []
    filtered.sort(key=lambda m: (m[2], m[1]), reverse=True)
    top_depth, top_score = filtered[0][2], filtered[0][1]
    return [m[0] for m in filtered if m[2] == top_depth and m[1] == top_score]


def resolve_fields(rules, base_options, state, field_order):
    """
    rules: list of rule dicts (see module docstring)
    base_options: {field: [option, ...]} - the specsheet's own parsed variant options
    state: {field: selected_value} - current selections (may be partial/empty)
    field_order: list of field keys in precedence order (later = more specific)

    Returns {field: {"hidden": bool, "visible_options": [...], "enabled_options": [...], "value": str}}
    after iterating to a fixed point, exactly like evaluateAllLogic's while loop.
    """
    state = dict(state or {})

    for _ in range(MAX_ITERATIONS):
        changed = False
        results = {}

        for field in field_order:
            matches = []
            for rule in rules:
                if field not in rule.get("target_fields", []):
                    continue
                m = _match_score(rule, state, field_order)
                if m is not None:
                    matches.append((rule, m[0], m[1]))

            best_vis = _best_rules(matches, FIELD_VIS_ACTIONS)
            force_hide = any(r["action"] == "Hide Field" for r in best_vis)
            force_show = any(r["action"] == "Show Field" for r in best_vis)
            hidden = force_hide or (False if force_show else False)

            if hidden:
                results[field] = {"hidden": True, "visible_options": [], "enabled_options": [], "value": ""}
                continue

            base = base_options.get(field, [])
            visible = list(base)

            best_opt_vis = _best_rules(matches, OPTION_VIS_ACTIONS)
            show_vals, has_show = set(), False
            for r in best_opt_vis:
                opts = {_norm(o) for o in r.get("affected_options", [])}
                if r["action"] == "Hide Only":
                    visible = [o for o in visible if _norm(o) not in opts]
                else:
                    has_show = True
                    show_vals |= opts
            if has_show:
                visible = [o for o in visible if _norm(o) in show_vals]

            enabled = list(base)
            best_opt_en = _best_rules(matches, OPTION_ENABLE_ACTIONS)
            enable_vals, has_enable = set(), False
            for r in best_opt_en:
                opts = {_norm(o) for o in r.get("affected_options", [])}
                if r["action"] == "Disable Only":
                    enabled = [o for o in enabled if _norm(o) not in opts]
                else:
                    has_enable = True
                    enable_vals |= opts
            if has_enable:
                enabled = [o for o in enabled if _norm(o) in enable_vals]

            visible_n = {_norm(o) for o in visible}
            enabled_n = {_norm(o) for o in enabled}
            current = state.get(field) or ""
            value = current if (_norm(current) in visible_n and _norm(current) in enabled_n) else ""

            results[field] = {
                "hidden": False,
                "visible_options": visible,
                "enabled_options": enabled,
                "value": value,
            }

        for field, r in results.items():
            new_val = "" if r["hidden"] else r["value"]
            if _norm(state.get(field)) != _norm(new_val):
                changed = True
            state[field] = new_val

        if not changed:
            break

    return results
