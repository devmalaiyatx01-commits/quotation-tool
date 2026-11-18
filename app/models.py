import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, ForeignKey, DateTime, JSON, UniqueConstraint
)
from sqlalchemy.orm import relationship, backref

from .database import Base


def now():
    return datetime.datetime.utcnow()


# The two tools this app serves. Architectural lighting (recessed downlights, spots, profiles -
# ordered on beam angle, CCT, IP) and Decorative lighting (chandeliers, pendants - ordered on
# material, finish, drop) are different trades run by different people, so every product and
# every BOQ belongs to exactly one of them and neither tool ever sees the other's data.
#
# Stored on the rows rather than split into separate tables so that one deployment, one
# database, one backup and one set of export/pricing code serve both - the alternative was
# duplicating machinery that took weeks to get right.
DISCIPLINES = ("architectural", "decorative")
DEFAULT_DISCIPLINE = "architectural"


class Specsheet(Base):
    __tablename__ = "specsheets"

    id = Column(Integer, primary_key=True)
    # Which tool this product belongs to - see DISCIPLINES. Indexed because every catalogue
    # query filters on it.
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    filename = Column(String, nullable=False)
    category = Column(String, default="")      # literal product-type text parsed off this one PDF's title block (descriptive, not a grouping)
    typology = Column(String, default="", index=True)  # business-assigned classification (mirrors Configure.gs's "Architectural Luminaires")
                                                        # - many specsheets can share one typology; this is what config rules are scoped to,
                                                        # not `category`, since a rule is about "products of this classification," not
                                                        # "products whose PDF happened to describe itself with this exact text."
    product_name = Column(String, default="")   # e.g. "NYX -Mini" - this exact page's product
    series = Column(String, default="", index=True)   # e.g. "NYX", "NYX Square" - the product line, shared across sizes
    family = Column(String, default="", index=True)   # e.g. "Mini", "Midi", "Nano" - the size/variant within a series
    variant = Column(String, default="")   # e.g. "With Trim", "Trimless" - distinguishes otherwise-identical catalog pages
    environment = Column(JSON, default=list)  # subset of ["Interior", "Exterior"] - NOT mutually exclusive: some
                                               # products are legitimately rated/sold for both (see
                                               # app/environment_classifier.py and DataBaseSchema.md). Auto-guessed
                                               # at upload from category text where the text says so explicitly
                                               # ("underwater", "inground", ...); [] means unclassified, same
                                               # "leave blank rather than guess" rule as `typology`.
    page_number = Column(Integer, default=1)  # which page of source_pdf_path this specsheet came from (catalogs are multi-page)
    make = Column(String, default="")
    source_pdf_path = Column(String, default="")
    source_url = Column(String, default="")   # the manufacturer's own page for THIS product, e.g.
                                               # https://www.exenia.eu/en/product/atollo-ceiling/. For a
                                               # catalogue-derived row source_pdf_path points at a page
                                               # shared by eight products, which is not a source of truth
                                               # anyone can check a single line against; the product page
                                               # is. Blank for rows that only ever came from a PDF.
    image_path = Column(String, default="")   # primary/first diagram - kept for backward-compat thumbnails
    diagrams = Column(JSON, default=list)     # [{"path": ..., "width": px, "height": px, "label": "Product Image"|"Dimension"|...}, ...]
    raw_text = Column(Text, default="")
    params = Column(JSON, default=dict)   # {"MECHANICAL PARAMETERS": {"FINISH": "BLACK", ...}, ...} - everything parsed, grouped
    fields = Column(JSON, default=dict)   # flattened canonical fields: finish, ip_rating, beam_angle, cct, wattage, control, warranty, cri, accessories, material, mounting
    options = Column(JSON, default=dict)  # same keys as `fields` but list of options split on '/'
    list_price = Column(Float, nullable=True)  # this exact specsheet row's verified list price, for
                                                # the common case where the row's own fields.wattage is
                                                # a single value (not multi-option), so one number
                                                # unambiguously applies to the whole row. Sourced from
                                                # the studio's own real price sheets (e.g.
                                                # SampleInputs/Pricing/*.xlsx), NOT guessed or derived.
                                                # None means no verified price yet - BOQItem.price still
                                                # defaults to 0/manual entry in that case. Never
                                                # auto-populated by a matching heuristic - only ever set
                                                # by a one-off backfill script after a human has
                                                # confirmed which specsheet row a price-sheet entry
                                                # actually is.
    list_price_by_wattage = Column(JSON, nullable=True)  # {"9W": 1000.0, ...} - for a row whose own
                                                # fields.wattage is genuinely multi-option (e.g. "5W /
                                                # 7W / 9W", one specsheet row covering several real
                                                # configurable wattages), where a real price sheet often
                                                # only gives a verified price for SOME of those wattages,
                                                # not all (e.g. NYX Pricing.pdf: NYX -Mini's 5W/7W/9W row
                                                # only has a real 9W price - applying that as a flat
                                                # list_price would wrongly imply 5W/7W cost the same,
                                                # unverified). A wattage missing from this dict has no
                                                # verified price (falls back to list_price, then 0) -
                                                # same "never guess" discipline as list_price itself, just
                                                # keyed instead of flat since one row can need several
                                                # independently-verified numbers. See
                                                # resolveListPrice() in static/app.js for the lookup
                                                # order (by-wattage first, then flat list_price, then 0).
    # --- New Product Development (2026-09-09) ------------------------------------------
    # "parsed" for everything read out of a manufacturer's PDF, "npd" for a product entered
    # through the NPD tab. The distinction matters because the two have opposite directions of
    # truth: a parsed row is a reading of a document that already existed and may be wrong
    # about it, while an NPD row IS the definition - its specsheet PDF is generated from these
    # columns, not the other way round. Everything else about the row is identical, which is
    # the point: an NPD product is a Specsheet, so the Library, Match, Configurator and BOQ
    # flows all accept it without knowing it was authored rather than parsed.
    origin = Column(String, default="parsed", index=True)

    # An NPD product is priced as one base figure plus an adder per chosen option, rather than
    # a verified price per whole configuration (PriceEntry, which is what a real price sheet
    # gives). Storing the two pieces instead of the product of them is what makes it tractable:
    # a downlight with 4 wattages x 3 CCTs x 4 beams x 2 finishes is 96 configurations and 13
    # numbers to maintain. Adders default to 0, so a product whose options cost the same is
    # simply a base price and nothing else.
    # A product authored in NPD is a draft until somebody says otherwise. "pending" keeps it
    # out of the Specsheet Library and everything fed from it - the Configurator, Match, the
    # BOQ pickers - so a half-finished definition cannot be quoted from by someone who never
    # knew it was being written. "approved" releases it; "held" is the deliberate no, kept
    # distinct from pending so a product that was looked at and parked is not confused with
    # one nobody has looked at yet.
    #
    # Everything parsed from a manufacturer's PDF is "approved": those were in the library
    # before this column existed, and a specsheet read from a real document is not a draft.
    approval_status = Column(String, default="approved", index=True)
    base_price = Column(Float, nullable=True)
    # {"wattage": {"9W": 0, "12W": 450.0}, "finish": {"WHITE": 0, "GOLD": 1200.0}} - keyed by
    # the same flattened field names as `fields`, so a configured BOQ line can be priced by
    # looking up the values it already carries. See npd_price_for() in app/npd.py.
    price_adders = Column(JSON, default=dict)

    created_at = Column(DateTime, default=now)

    boq_items = relationship("BOQItem", back_populates="specsheet")
    price_entries = relationship("PriceEntry", back_populates="specsheet")


class PriceEntry(Base):
    """A single verified (product configuration -> price) fact, sourced from one of
    the studio's own real price sheets (SampleInputs/Pricing/*) - replaces the earlier
    Specsheet.list_price/list_price_by_wattage columns (2026-08-20) with a proper table:
    those two only ever supported one specsheet row mapping to one flat price or one
    wattage-keyed dict, with zero provenance beyond a backfill script's own comments, and
    no path for data to enter the system except me hand-writing a script after a live
    conversation confirming the mapping. This table is the same "never auto-matched, only
    a human confirms" discipline, but as real schema: an uploaded price sheet's raw rows
    land here with specsheet_id=None (nothing mapped yet, doesn't affect any BOQ default),
    a human maps each to a real specsheet + config via the List Pricing tab, and only then
    does it feed the Configurator's price default (app/main.py's /api/list-pricing*
    endpoints, static/app.js's resolveListPrice()).

    `config` generalizes list_price_by_wattage's {wattage: price} shape to any set of
    fields a real price sheet keys its price on - not just wattage (e.g. NYX's Tunable
    White adder is really keyed on {wattage, cct} together) - stored as {field: value},
    e.g. {"wattage": "9W"} or {"wattage": "9W", "cct": "TW (2700K-6500K)"}. An empty dict
    means "applies to this specsheet regardless of configuration" (the EMBER case - a
    single-wattage row with nothing left to disambiguate).

    `status`: "locked" (green tag - a human has confirmed this exact number, e.g. cross-
    checked against multiple independent signals the way NYX's cutout+wattage+price-
    consistency match was) vs "open" (yellow tag - mapped to the right specsheet, but the
    number itself hasn't been independently double-checked yet, or is provisional).
    Meaningless until specsheet_id is set - an unmapped row's status is not shown anywhere.
    """
    __tablename__ = "price_entries"

    id = Column(Integer, primary_key=True)
    # Which tool this price belongs to. Needed as its own column rather than read through
    # specsheet_id, because an unmapped row - one parsed from a price sheet but not yet linked
    # to a product - has no specsheet to ask, and those are exactly the rows the pricing queue
    # is made of.
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    specsheet_id = Column(Integer, ForeignKey("specsheets.id"), nullable=True)
    source_file = Column(String, nullable=False)   # e.g. "NYX Pricing.pdf" - which real price sheet this came from
    source_sheet = Column(String, default="")      # e.g. "Downlights" - blank for a PDF/single-sheet source
    source_label = Column(Text, default="")        # the raw row text this was parsed from, kept for audit/re-verification
    config = Column(JSON, default=dict)             # {"wattage": "9W", ...} - see class docstring
    source_specs = Column(JSON, default=dict)        # every spec the source row stated, verbatim and
                                                     # unfiltered ({"wattage": "1-3W", "cct": "2700K,
                                                     # 3000K, 4000K, 6000K", "beam_angle": "10°/15°/...",
                                                     # "ip_rating": "IP67"}). Deliberately separate from
                                                     # `config`: config is the minimal key the
                                                     # Configurator matches on by EXACT equality at
                                                     # add-time, so a multi-value cell there would make
                                                     # the entry permanently unmatchable - while
                                                     # discovery (app/price_matcher.py) needs exactly
                                                     # those rich multi-value cells, read with range/set
                                                     # semantics, to identify which product a row is for.
                                                     # Two different jobs, two different shapes.
    price = Column(Float, nullable=False)
    status = Column(String, default="open")         # "locked" | "open" - see class docstring
    # Set when this is the price of one physical accessory part (models.AccessoryPart) rather
    # than of a product: specsheet_id is then NULL, because the part is shared by every product
    # confirmed to take it, and one number serves them all. A row with neither is unmapped.
    accessory_part_id = Column(Integer, ForeignKey("accessory_parts.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=now)

    specsheet = relationship("Specsheet", back_populates="price_entries")


class BOQ(Base):
    __tablename__ = "boqs"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)   # see DISCIPLINES
    project_name = Column(String, nullable=False)
    project_code = Column(String, default="")  # the studio's own project reference code (e.g. "CRA-2026-014") -
                                                # distinct from project_name (the human-readable title) and from
                                                # BOQItem.item_code (a per-line S.No within one BOQ); shown on
                                                # every export's cover page, genuinely optional
    area = Column(String, default="")       # e.g. "Interior"
    client = Column(String, default="")
    kind_attention = Column(String, default="")  # the specific contact person addressed on the
                                                   # NOTE sheet/floor headers' "Kind Attention:" line -
                                                   # distinct from `client` (the company/project),
                                                   # genuinely optional (the sample template's own
                                                   # saved BOQ leaves it blank)
    boq_date = Column(String, default="")    # stored as ISO date string, user-editable
    notes_text = Column(Text, default="")    # editable NOTE-page content (terms + concluding
                                              # signature) - blank means "use the default text",
                                              # never touches the fixed company letterhead
                                              # (COMPANY_LINES), which isn't part of this field
    discount_percent = Column(Float, default=0.0)  # the standard discount on the Quotation export:
                                                     # every PRODUCT line is quoted at it unless the line
                                                     # has a discount of its own (BOQItem.discount_percent).
                                                     # Accessories, drivers and power supplies are not
                                                     # discounted by it (decided 2026-09-29) - see
                                                     # quotation_export.line_discount, the one place the
                                                     # rule lives
    # Whether the exported quotation shows its discount - the Discounted Price column and the
    # Discount row (2026-09-29). Off quotes final prices only: the client sees each line's net
    # price and nothing about how it was reached. A quotation with no discount on any line never
    # shows one either way, so no document carries a "Discount 0%" row.
    show_discount = Column(Boolean, default=True)
    currency = Column(String, default="INR")  # one currency per BOQ (a quotation is denominated in a
                                               # single currency throughout, same reasoning as
                                               # discount_percent being BOQ-level, not per-item) - see
                                               # app.quotation_export.CURRENCY_PREFIXES for the actual
                                               # displayed prefix per code
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    # Who made this BOQ (2026-09-16), so a person can filter the list down to their own work.
    # Set once when the BOQ is created, duplicated, branched or turned into a detailed BOQ -
    # the person doing that is its maker. NULL means "not recorded": BOQs from before this
    # column existed, where the activity log (which only began 2026-09-11) could not say for
    # certain. A plain integer rather than a foreign key, the same as ActivityLog.user_id, so
    # removing an account never blocks on or rewrites the BOQs that person made.
    created_by_id = Column(Integer, nullable=True, index=True)
    created_by = relationship("User", primaryjoin="foreign(BOQ.created_by_id) == User.id",
                              viewonly=True)

    # How this BOQ is built, and which export layout it takes (2026-09-14):
    #
    #   "detailed" - floors, then areas, then the products in each. Every quantity is placed
    #                where it goes. This is what the app has always done and stays the default.
    #   "quick"    - a product is entered once with its total quantity and marked with the
    #                areas it goes into, under a reference code from the drawing. Made for
    #                quoting before an order exists, when the split between areas isn't
    #                decided yet; once the order is confirmed, "Create detailed BOQ from
    #                this" turns it into the other kind, areas becoming rooms.
    #
    # A quick BOQ still keeps its products in one floor and one area behind the scenes, so
    # that everything already written for items - the Configurator, pricing, accessories,
    # copying, exports - goes on working unchanged; the screens simply never show that pair.
    # Anything missing this column is a BOQ from before quick mode existed: detailed.
    mode = Column(String, default="detailed")

    # Branching (2026-08-14): a branch is a full independent copy of a BOQ - own floors/
    # rooms/items, own id, edited completely separately from whatever it was copied from -
    # not a second "view" onto shared data and never merged back, the same one-way-fork
    # semantics as a GitHub branch nobody ever merges. parent_boq_id just remembers where it
    # came from, so the editor can show "branched from X" and the BOQs list can group a
    # branch under its origin. NULL parent_boq_id = this is an original, unbranched BOQ.
    # No ondelete= here: deleting the parent leaves the branch fully intact (it owns its own
    # data entirely) and just loses the "branched from" note - verified this is exactly what
    # SQLAlchemy's default ORM delete behavior already does for a one-to-many relationship
    # without cascade="delete" (nulls the child's FK rather than blocking or deleting it),
    # and delete_boq() is the only place a BOQ ever gets deleted in this app.
    parent_boq_id = Column(Integer, ForeignKey("boqs.id"), nullable=True)
    branch_name = Column(String, default="")  # blank for an original BOQ; user-chosen for a branch

    floors = relationship("BOQFloor", back_populates="boq", cascade="all, delete-orphan", order_by="BOQFloor.sort_order")
    exports = relationship("BOQExport", back_populates="boq", cascade="all, delete-orphan", order_by="BOQExport.exported_at.desc()")
    branches = relationship("BOQ", backref=backref("parent_boq", remote_side=[id]), order_by="BOQ.created_at")
    areas = relationship("BOQArea", back_populates="boq", cascade="all, delete-orphan",
                         order_by="BOQArea.sort_order")
    ref_images = relationship("BOQRefImage", back_populates="boq", cascade="all, delete-orphan",
                              order_by="BOQRefImage.sort_order")


class BOQArea(Base):
    """One area this project is quoted for - "Lift Lobby", "All Bedrooms", "Balcony".

    Defined once per BOQ and then marked against as many products as go into it, which is the
    whole point: the areas are typed once, not re-typed for every product. Only a quick BOQ
    uses these; a detailed one has real rooms instead, under real floors.

    A product's areas are kept as this row's id (BOQItem.area_ids), not its name, so renaming
    an area here renames it everywhere it is used, on screen and in the next export.
    """
    __tablename__ = "boq_areas"

    id = Column(Integer, primary_key=True)
    boq_id = Column(Integer, ForeignKey("boqs.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)

    boq = relationship("BOQ", back_populates="areas")


class BOQRefImage(Base):
    """A reference picture for this project - a detail, a site photograph, a drawing crop.

    Uploaded once and then attached to any number of products, for the same reason areas are:
    the same picture usually explains several fixtures, and nobody should have to find and
    upload it again for each of them. Stored in the same folder as product images and served
    the same way; `filename` is the name on disk, `label` what the person called it.
    """
    __tablename__ = "boq_ref_images"

    id = Column(Integer, primary_key=True)
    boq_id = Column(Integer, ForeignKey("boqs.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    label = Column(String, default="")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)

    boq = relationship("BOQ", back_populates="ref_images")


class BOQFloor(Base):
    __tablename__ = "boq_floors"

    id = Column(Integer, primary_key=True)
    boq_id = Column(Integer, ForeignKey("boqs.id"), nullable=False)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)

    boq = relationship("BOQ", back_populates="floors")
    rooms = relationship("BOQRoom", back_populates="floor", cascade="all, delete-orphan", order_by="BOQRoom.sort_order")


class BOQRoom(Base):
    __tablename__ = "boq_rooms"

    id = Column(Integer, primary_key=True)
    floor_id = Column(Integer, ForeignKey("boq_floors.id"), nullable=False)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)
    remark = Column(Text, default="")  # a single whole-area note, rendered as one full-width row below this room's items (not per-item)

    floor = relationship("BOQFloor", back_populates="rooms")
    items = relationship("BOQItem", back_populates="room", cascade="all, delete-orphan", order_by="BOQItem.sort_order")


class BOQItem(Base):
    __tablename__ = "boq_items"

    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("boq_rooms.id"), nullable=False)
    specsheet_id = Column(Integer, ForeignKey("specsheets.id"), nullable=True)
    parent_item_id = Column(Integer, ForeignKey("boq_items.id", ondelete="SET NULL"), nullable=True)
    # The product row this accessory is associated with (null for regular products/drivers).
    # Accessories always render/insert directly below their parent - see add_item's
    # insertion logic in main.py. delete_item cascades: deleting a product deletes its
    # accessories rather than leaving them orphaned (an accessory alone in a room is
    # meaningless - see the feature this was built from).
    sort_order = Column(Integer, default=0)

    item_code = Column(String, default="")     # e.g. "CRA2.2" - project-specific numbering, shown as
                                                # "S.No" in both the BOQ and the Quotation export
    reference_number = Column(String, default="")  # a separate reference code, distinct from item_code -
                                                     # shown as "REF NO." on the Quotation export (matches
                                                     # the real sample quotation's own S.No/REF NO. split;
                                                     # previously item_code was wrongly reused for REF NO.,
                                                     # which meant a value typed into "S.No" silently
                                                     # surfaced under a different column on that export)
    model_no = Column(String, default="")      # manufacturer's own SKU/model code, e.g. "LL 102234" -
                                                # distinct from item_code, which is this studio's own
                                                # numbering; typed by hand like item_code, since no
                                                # specsheet field reliably carries a single clean code
    description = Column(Text, default="")
    dimensions = Column(String, default="")    # free text for the export's Dimension column, e.g.
                                                # "Ø62 mm x 70 mm". Typed by hand, because only
                                                # InHouse publishes a dimensioned line drawing this
                                                # app can use (Specsheet.diagrams, label "Additional
                                                # Diagram 2" - see show_line_diagram below); for every
                                                # other make the measurements exist only inside the
                                                # PDF's own artwork, and inventing them would be
                                                # putting a number nobody verified on an order.
    # Which of the product's pictures goes in the Dimension column when show_line_diagram is on
    # (2026-09-15) - the label of an entry in Specsheet.diagrams, such as "Profile 2". Blank
    # means the manufacturer's own line drawing, which is what every row meant before there was
    # a choice; a label that no longer exists on the specsheet falls back to it the same way.
    # Strip lights are why: their specsheets carry the profiles a strip is fitted into, and the
    # profile is often the drawing a client actually needs to see - see pdf_parser's
    # extract_profile_pictures.
    dimension_image = Column(String, default="")
    show_line_diagram = Column(Boolean, default=False)  # draw that InHouse line drawing in the
                                                # Dimension cell instead of the text above. Per item,
                                                # not per BOQ: one document can reasonably want the
                                                # drawing on the recessed fittings a contractor has to
                                                # cut holes for and not on the strip lighting.
    finish = Column(String, default="")
    wattage = Column(String, default="")
    beam = Column(String, default="")
    control = Column(String, default="")
    led_color = Column(String, default="")     # CCT (e.g. "3000K"), the specific variant chosen for
                                                # this item - same reasoning as finish/wattage/beam/
                                                # control: a specsheet's own `fields.cct` can be a
                                                # slash-separated multi-option string, so what was
                                                # actually picked has to be stored per-item, not derived
    config = Column(JSON, default=dict)        # {field_key: chosen value} for every configurable
                                                # axis that has no column of its own - the ones the
                                                # library gained later (cri, lumen_output, length,
                                                # mounting, ip_rating) plus whatever a make defines
                                                # for itself. Lumenpulse is the reason this exists:
                                                # its products are ordered on axes no other brand
                                                # has (Decorative Bezel Finish, Trim Shape, Joiners,
                                                # Assembly Configuration, ...), and a BOQ row that
                                                # cannot record them is not a specification of a
                                                # real, orderable product. Adding a column per axis
                                                # does not scale past one catalogue; finish/wattage/
                                                # beam/control/led_color keep theirs because exports
                                                # and pricing already read them by name.
    make = Column(String, default="")
    qty = Column(String, default="1")
    unit = Column(String, default="Nos.")
    remarks = Column(String, default="")
    is_accessory = Column(Boolean, default=False)
    # A driver row is ALSO an accessory row (is_accessory stays True for it too - drivers
    # still belong in the same "ACCESSORIES" export block, unchanged) - this is a finer-
    # grained sub-flag purely for the app's own UI (the compact row's thumbnail shows "A" or
    # "D" so a scanning eye can tell rows apart - see renderRoom() in app.js), never read by
    # any export. Deliberately a real column, not inferred from description text starting
    # with "Driver" - that text is freely hand-editable (Edit Manually unlocks it), so an
    # inferred flag would silently stop working the moment someone rewords it. Same lesson as
    # TUTORIAL.md's typology/category case study: a classification a user can accidentally
    # invalidate by editing an unrelated-looking field is a bug waiting to happen.
    is_driver = Column(Boolean, default=False)
    price = Column(Float, default=0.0)         # unit price - manually entered for now (0 by default);
                                                # a price-list-driven default is future work, not V1
    # This line's own discount on the quotation, in percent (2026-09-29). NULL - the usual case -
    # means it follows the quotation's rule: a product gets BOQ.discount_percent, an accessory or
    # driver gets none. A number overrides that for this line alone, 0 included (a product left
    # out of the discount). See quotation_export.line_discount.
    discount_percent = Column(Float, nullable=True)

    # Quick BOQs only (see BOQ.mode). Which of the project's areas this product goes into, and
    # which of its reference pictures belong beside it - both lists of ids into BOQArea /
    # BOQRefImage, in the order the person chose them. Ids rather than names or file paths, so
    # a renamed area or a re-labelled picture stays right wherever it is already used; a list
    # on the row rather than a join table because their order is part of what gets printed and
    # they are only ever read one BOQ at a time. Empty on a detailed BOQ's items.
    area_ids = Column(JSON, default=list)
    ref_image_ids = Column(JSON, default=list)

    room = relationship("BOQRoom", back_populates="items")
    specsheet = relationship("Specsheet", back_populates="boq_items")


class ConfigRule(Base):
    """One row of the studio's Configurator "Settings" sheet, ported: up to 5
    AND-triggers (each with OR'd values) -> an action applied to target field(s),
    scoped per typology (Specsheet.typology) since valid combinations differ by
    typology, and a rule should apply to every product classified under it - not
    just the one specsheet it was authored against. See app/config_rules.py for
    the evaluation engine.

    `specsheet_id` (2026-08-29) narrows a rule to ONE product, for constraints that are
    genuinely per-product rather than per-typology. Exenia forced this: its catalogue
    couples each optic to its own power ("Narrow Spot 11 - 8,2W - 9540cd"), and the same
    optic draws a different wattage on every body size - "Narrow Flood 24" maps to five
    different wattages inside the single "Base Projectors" typology, so a typology-wide
    rule would be wrong for four of them. NULL means typology-wide, which is what every
    hand-authored rule is and remains the default."""
    __tablename__ = "config_rules"

    id = Column(Integer, primary_key=True)
    typology = Column(String, nullable=False, index=True)
    specsheet_id = Column(Integer, ForeignKey("specsheets.id"), nullable=True, index=True)
    triggers = Column(JSON, default=list)         # [{"field": "wattage", "values": ["24W"]}, ...]
    target_fields = Column(JSON, default=list)    # ["beam_angle"]
    action = Column(String, nullable=False)       # Show Only | Hide Only | Enable Only | Disable Only | Show Field | Hide Field
    affected_options = Column(JSON, default=list)  # ["10x35°", "10x45°", ...] - ignored for *Field actions
    created_at = Column(DateTime, default=now)


class FieldCode(Base):
    """A short-code lookup per (field, option value), ported from Configure.gs's
    'Product Code' sheet (there: optionsMapping[fieldKey] = [{name, code}, ...]).
    Global, not scoped per typology - "WHITE" gets the same code wherever it
    appears, mirroring the original single flat SKU-builder. Populated by hand
    from the Config Rules tab's "Product Codes" panel since no PDF carries a
    short-code for its own option values; a value with no row here simply
    contributes nothing to the generated code (matches generateProductCode's
    own skip-if-no-match behavior) rather than falling back to raw text."""
    __tablename__ = "field_codes"

    id = Column(Integer, primary_key=True)
    field = Column(String, nullable=False, index=True)   # one of app.config_rules.FIELD_ORDER
    option_value = Column(String, nullable=False)         # raw parsed option text, e.g. "WHITE", "3000K"
    code = Column(String, default="")                     # e.g. "WH", "30"
    created_at = Column(DateTime, default=now)


class CodeAttribute(Base):
    """One thing a product code can say about a product - its type, its series, its trim, its
    wattage - and how a value of it becomes the letters in the code.

    The studio's own product code is built from these, in the order a CodeScheme lists them,
    so the list is data rather than code: a new axis (aperture shape, driver IP) is a row
    somebody adds in NPD, not a change a developer has to make. The abbreviation for each
    value is a FieldCode row keyed by the same `key`; `rule` is what happens when a value has
    no abbreviation of its own ("12W" -> "12W" by rule, where listing every wattage by hand
    would never end). See app/product_codes.py for how a code is assembled from them."""
    __tablename__ = "code_attributes"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    key = Column(String, nullable=False, index=True)      # "trim_details" - where the value lives
    label = Column(String, nullable=False)                # "Trim" - what people call it
    # "identity" names which product this is and is fixed per product; "config" is chosen
    # when the product is quoted. The identity part alone is what must be unique across the
    # catalogue - two products may not share it.
    part = Column(String, default="identity")
    rule = Column(String, default="list")                 # see product_codes.RULES
    # Written around what the rule produces, so a number says what it measures: 7 + "W" is
    # 7W, "CRI" + 95 is CRI95, "IP" + 20 is IP20. A typed abbreviation is taken as it is.
    prefix = Column(String, default="")
    suffix = Column(String, default="")
    # Zero or many values at once, each adding its own code - accessories. Everything else
    # holds exactly one value per configured product.
    multi = Column(Boolean, default=False)
    note = Column(Text, default="")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class CodeScheme(Base):
    """The shape of a product code for one brand's typology: which attributes it carries, in
    which order, and what separates them.

    `typology` blank is the brand's default, used by any typology that has no scheme of its
    own. `segments` is [{"key": "series", "include": true, "optional": false}, ...] in code
    order - a list on the row rather than a table of its own because its order IS the grammar,
    and it is always read and saved whole. `include` false keeps a segment in the structure
    but out of the code; `optional` true leaves it out for a product that records no value for
    it, instead of calling that product's code incomplete.

    `retired_at` is set when the typology is put back on the brand default. The row is kept
    rather than deleted, so a structure switched off by mistake can be restored exactly as it
    was; while retired it codes nothing. There is one row per brand and typology, retired or
    not, so saving a new structure for the typology reuses (and replaces) a retired one."""
    __tablename__ = "code_schemes"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    make = Column(String, nullable=False, index=True)
    typology = Column(String, default="", index=True)
    prefix = Column(String, default="")
    separator = Column(String, default="-")
    segments = Column(JSON, default=list)
    retired_at = Column(DateTime, nullable=True)
    updated_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class BOQExport(Base):
    __tablename__ = "boq_exports"

    id = Column(Integer, primary_key=True)
    boq_id = Column(Integer, ForeignKey("boqs.id"), nullable=False)
    file_path = Column(String, nullable=False)
    exported_at = Column(DateTime, default=now)

    boq = relationship("BOQ", back_populates="exports")


class User(Base):
    """A login account - see app/auth.py and deployment.md section 3.3. Deliberately
    minimal for a 20-30 person internal tool: no self-registration (accounts are
    admin-provisioned via create_user.py), no password reset flow. `role` is a coarse
    admin/member split - "admin" can edit Config Rules/Product Codes (which affect
    every BOQ built afterward), closing the gap the README used to flag: no gating
    on who could edit configuration rules."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="member")  # "admin" | "member"
    # Which tools this account may open - a subset of DISCIPLINES. Architectural and
    # Decorative lighting are run by different people, so this is an access list, not a
    # preference: an account with only ["decorative"] cannot reach architectural data at all,
    # and the chooser after login shows only what is in here. Empty/NULL reads as
    # ["architectural"], which is what every account that predates this column actually had.
    disciplines = Column(JSON, default=list)
    created_at = Column(DateTime, default=now)


class ActivityLog(Base):
    """Who did what, and when - one row per change anyone makes, plus every sign-in.

    Written by the app, read only by an admin (GET /api/activity), and never edited or
    deleted by anything: there is no endpoint that changes a row here, deliberately, so the
    record of who deleted a BOQ cannot be tidied away by the person who deleted it.

    `user_email` is stored alongside `user_id` rather than looked up later, so the record
    still names the person after their account is removed. `summary` is the sentence an admin
    reads ("Created BOQ 'Amaltas Marg'"); `action` is the stable key the Activity screen
    filters on ("boq.create"). `boq_id`/`boq_name` record which project a change touched,
    captured at the time - a deleted BOQ's name is exactly what someone will want to know.
    """
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=now, index=True)
    user_id = Column(Integer, index=True, nullable=True)
    user_email = Column(String, index=True)
    action = Column(String, index=True)
    summary = Column(String)
    boq_id = Column(Integer, index=True, nullable=True)
    boq_name = Column(String, nullable=True)
    method = Column(String, nullable=True)
    path = Column(String, nullable=True)
    discipline = Column(String, nullable=True)
    details = Column(JSON, nullable=True)


class UserPermission(Base):
    """One thing one person is allowed to do, granted by an admin from the People tab.

    A row existing is the grant; revoking deletes it. There are no hidden defaults read from
    anywhere else - what an admin sees against a person on the People tab is exactly what
    that person can do, which is the point of managing access from the app rather than from
    code. The permission keys and what each covers are listed in app/main.py (PERMISSIONS).

    An admin (User.role == "admin") holds every permission without rows, plus managing people.
    `expires_at` is present for access that should lapse on its own; empty means until revoked.
    """
    __tablename__ = "user_permissions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    permission = Column(String, nullable=False, index=True)
    granted_by = Column(String, nullable=True)        # the admin's email, kept if they leave
    granted_at = Column(DateTime, default=now)
    expires_at = Column(DateTime, nullable=True)


class AppSetting(Base):
    """Settings an admin changes from the app instead of from code - today, what a person is
    allowed to do the first time they sign in. One row per setting, its value as JSON."""
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(JSON, nullable=True)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=now, onupdate=now)


class ParameterTemplate(Base):
    """Which parameters a product of a given typology is described by, and in which group.

    A downlight is described by cutout dimensions, trim details and beam angle; a strip light
    is not. Rather than ask whoever adds a product to remember the full list every time, each
    typology carries its own template - the rows below - and the NPD form starts from it.

    Seeded from the products already in the library (seed_parameter_templates.py reads what
    InHouse's own specsheets actually use), then extended by hand: a parameter added while
    entering one product persists for that typology, so the second product of that kind starts
    from a better template than the first did. That is the whole reason this is a table and not
    a constant - the studio's own vocabulary grows, and it grows through use.

    `group_name` is the banner the parameter sits under on the generated specsheet
    ("MECHANICAL PARAMETERS"), matching the four-group layout of a InHouse sheet.
    `field_key` is the flattened name the rest of the app knows the value by (`fields`,
    price_adders, BOQItem columns), so a template row is also the bridge between what the
    sheet prints and what the Configurator prices.
    """
    __tablename__ = "parameter_templates"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    typology = Column(String, nullable=False, index=True)
    group_name = Column(String, nullable=False)     # "MECHANICAL PARAMETERS"
    label = Column(String, nullable=False)          # "CUTOUT DIMENSIONS" - as printed
    field_key = Column(String, nullable=False)      # "cutout_dimensions" - as stored
    sort_order = Column(Integer, default=0)
    # True when this parameter can hold several options a buyer picks between (wattage, CCT,
    # finish) rather than one fixed fact about the product (material, operating temperature).
    # Only these get an option list and price adders; the rest are single values.
    is_configurable = Column(Boolean, default=False)
    # False for the rows seeded from the existing library, True for anything a user added.
    # Kept so a template can be reviewed later without having to remember which parameters
    # came from the catalogue and which from one person's product entry.
    user_added = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)


class ConfiguratorField(Base):
    """One question the Configurator asks for one brand's typology, decided in NPD.

    When a product is added to a BOQ, the Configurator asks for its details - Finish, CRI, CCT,
    Beam ... Which ones, in which order, used to be fixed in code (config_rules.FIELD_ORDER) and
    worked out from whatever each product happened to hold. Now whoever looks after a brand in
    NPD decides it per typology: these rows are the details shown, in `sort_order`.

    No rows for a (make, typology) means nobody has decided yet, and the Configurator keeps its
    automatic behaviour. Rows are always saved whole for a (make, typology) - the list is one
    decision, read and written together.

    A detail some product of the typology offers a choice of (two CCTs, five beams) is always
    asked, whatever is saved here: a BOQ line has to say which one, or it names no orderable
    product, its code cannot complete and an option's price adder is missed. app/configurator.py
    adds any such field back if a saved list leaves it out. So what is really decided here is
    the order, and which fixed details (one value per product - a CRI, an IP rating) are shown.
    """
    __tablename__ = "configurator_fields"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    make = Column(String, nullable=False, index=True)
    typology = Column(String, nullable=False, index=True)
    field_key = Column(String, nullable=False)     # the key the value is stored under: cri, beam_angle ...
    # What the Configurator calls it, when the NPD parameter's own name reads badly there
    # ("ACTUAL POWER" for a wattage). Blank: the NPD name.
    label = Column(String, nullable=True)
    sort_order = Column(Integer, default=0)
    updated_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=now)


class DriverPrice(Base):
    """What a driver or power supply costs, as data the app owns rather than code.

    A driver is quoted as its own BOQ line and is chosen on four axes - type, control, IP and
    wattage - so one row is one buyable combination. `price` is nullable on purpose: several
    combinations are real and offerable while having no verified price (12V and 48V DC have
    never been priced, and 24V DC's Non Dim only above 200W), and a combination that exists
    without a price is a different thing from one that does not exist. Those come through as
    the plain 0 default the driver form has always used, never an invented figure.

    These lived as hand-edited constants in static/app.js until 2026-09-10 - CC_DRIVER_PRICES
    and the two 24V DC tables - which meant a new driver, or a price that had moved, needed
    somebody to change the source and redeploy. Everything else about a product is entered
    through NPD; its driver was the one thing that could not be. Seeded from those same
    constants by seed_driver_prices.py, so nothing about what the app quotes changed on the
    day they moved.
    """
    __tablename__ = "driver_prices"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    driver_type = Column(String, nullable=False, index=True)   # "24V DC", "Constant Current"
    control_type = Column(String, nullable=False)              # "Non Dim", "DALI", ...
    ip_rating = Column(String, nullable=False)                 # "IP Rated" / "Non IP Rated"
    wattage = Column(String, nullable=False)                   # "10W" - as the sheet writes it
    price = Column(Float, nullable=True)                       # None = offerable, unpriced
    # What the driver form shows under Driver Type, and the order the types appear in.
    sort_order = Column(Integer, default=0)
    # A short line about where the figure came from, shown beside it - these tables have
    # always carried that provenance in comments, and it is the reason to trust a number.
    source_note = Column(String, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class AccessoryDetail(Base):
    """What a person has defined about one accessory: the choices it is ordered on, and its picture.

    The accessory catalogue itself is not a table (app/accessories.py says why): which accessories
    exist is read from the specsheets that list them. What a datasheet cannot say is this. A strip
    light's datasheet prints "Profile Dimension: 16mm X 16mm / 17mm X 15mm" - the sizes of all its
    profiles together - and nothing on it says which size is the U-shape profile's. Someone who
    knows decides that once, here, and Add Product then asks it of the profile itself.

    Keyed by the catalogue's own key (accessories.fold of the name), so a row belongs to every
    spelling of the accessory, and accessories.rename carries it along when the name changes.

    A BOQ line copies what it was given when it was added (BOQItem.config["accessory_params"] and
    ["accessory_image"]), as every line copies its specsheet's values: changing a definition here
    never changes a quotation already made.
    """
    __tablename__ = "accessory_details"

    key = Column(String, primary_key=True)        # accessories.fold(name), e.g. "ushapeprofile"
    name = Column(String, default="")             # the catalogue's name when last saved
    # [{"name": "Dimension", "options": ["16mm X 16mm", ...]}], in the order they are asked.
    # No options means the value is typed when the accessory is added.
    params = Column(JSON, default=list)
    # A file in data/images ("accessory_<key>_<n>.png"), or "". Each upload gets a new file and
    # none is ever deleted: BOQ lines hold the file name they were added with.
    image = Column(String, default="")
    updated_at = Column(DateTime, default=now, onupdate=now)


class AccessoryPart(Base):
    """One accessory in the InHouse accessory library - a physical thing that can be ordered,
    counted and priced.

    A name is a label, not an identity. Two products can both list "U Shape Profile" and take two
    different profiles, 35x35 mm and 16x16 mm; and two strip lights can take the very same one.
    Keyed by name (AccessoryDetail above), the first pair would share a price right for only one of
    them, and kept per product, the second pair could never be added up into one quantity to order.
    So each accessory is a row of its own, and a product picks the ones it takes (AccessoryLink).
    Accessories that share a name are variants, told apart by their specifications.

    Everything that belongs to the physical thing lives here: its specifications (`params`, the
    same shape as AccessoryDetail.params, one value each), its picture, its own `code`, and its
    price (PriceEntry rows with accessory_part_id set). See app/accessory_parts.py.

    InHouse only (accessory_parts.PART_BRANDS). Other brands keep the name-keyed AccessoryDetail.
    """
    __tablename__ = "accessory_parts"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    make = Column(String, nullable=False, index=True)   # a part never spans brands
    name = Column(String, nullable=False)               # what people call it, e.g. "Honeycomb - NYX Mini"
    code = Column(String, default="")                   # this part's own product code; "" until given one
    params = Column(JSON, default=list)                 # [{"name", "options"}] - see AccessoryDetail
    # A file in data/images ("accessory_part-<id>_<n>.png"), or "" - never overwritten, as above.
    image = Column(String, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    links = relationship("AccessoryLink", back_populates="part")


class AccessoryLink(Base):
    """A product takes a library accessory - picked in the product's NPD form, never typed.

    Every row is a person's choice: there are no suggested links. Picking is what makes one
    accessory one accessory however its name is spelled (app/accessory_parts.py). The first fill,
    from what each datasheet lists, was the owner's decision to take the datasheets as read
    (scripts/backfills/backfill_inhouse_accessory_library.py, 6 Oct 2026); people correct it by
    picking, as with any other link. The product's
    accessory list and printed ACCESSORIES line (Specsheet.options / fields "accessories") are
    written from these rows, in `sort_order`, followed by any names its datasheet lists that are not
    in the library yet.
    """
    __tablename__ = "accessory_links"
    __table_args__ = (UniqueConstraint("specsheet_id", "part_id", name="uq_accessory_link_part"),)

    id = Column(Integer, primary_key=True)
    specsheet_id = Column(Integer, ForeignKey("specsheets.id", ondelete="CASCADE"), nullable=False, index=True)
    part_id = Column(Integer, ForeignKey("accessory_parts.id"), nullable=False, index=True)
    sort_order = Column(Integer, default=0, nullable=False)   # its place in the product's list
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    part = relationship("AccessoryPart", back_populates="links")


class VendorOrderCode(Base):
    """One order code exactly as a manufacturer prints it, with the configuration it orders.

    A manufacturer's own order code is a different thing from the studio's product code
    (CodeScheme / FieldCode / CodeAttribute), and the two must not share tables. The studio's
    abbreviations are one per value across the catalogue - "Polished Chrome" is one code. A
    vendor's are not: DuraGreen writes Polished Chrome as PC in its ACME range and L in MOOD,
    and in MOOD the letter S means Satin Nickel, Silver Specular or Spread Lens depending on
    where it sits in the code. Kept here, a vendor's letters can never leak into a InHouse code,
    or the other way round.

    Every row is a code the vendor printed - in its spec sheet's code table - never one this app
    assembled. `config` is the configuration in the app's own field names and values
    ({"wattage": "6W", "beam_angle": "120°", "cct": "3000K", "control": "On/off"}), so a BOQ
    line's choices can be looked up against it directly. `parts` is the same code cut into its
    pieces in order ([["type", "AFL"], ["size", "57"], ["wattage", "06W"], ["sep", "-"], ...]),
    joining back into `code` exactly; it is empty for a code that could not be cut up that way,
    which is then only ever used whole. See app/duragreen.py.
    """
    __tablename__ = "vendor_order_codes"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    make = Column(String, nullable=False, index=True)
    specsheet_id = Column(Integer, ForeignKey("specsheets.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String, nullable=False)
    config = Column(JSON, default=dict)
    printed = Column(JSON, default=dict)     # the table row as printed: wattage, lumens, optics ...
    parts = Column(JSON, default=list)
    source = Column(String, default="")      # which document, e.g. "EFL01.pdf"
    sort_order = Column(Integer, default=0)  # the order the table prints them
    created_at = Column(DateTime, default=now)


class VendorCodeRule(Base):
    """One line of a manufacturer's own published rule for building its order codes.

    DuraGreen's 2026 General Catalogue prints two "Specification Code" tables - ACME (catalogue page
    33) and MOOD (page 222): every segment of a code in order, every option of each segment with its
    letters, and where the separators go. `scope` is the table ("ACME", "MOOD"); a row is one option
    of one segment ("Driver": "Phase Cut" -> "P"). Scope "LEGEND" holds the codes the catalogue's
    product pages state in their decoded examples ("SB / solid brass structure") - a meaning the
    tables do not list but DuraGreen does print.

    Read from the catalogue by scripts/catalogues/read_duragreen_general_catalogue.py and loaded by
    scripts/backfills/backfill_duragreen_code_rules.py; used by app/duragreen.py to build the code
    for any configuration, not only the few its spec sheets print. Never the studio's own code
    tables - see VendorOrderCode for why the two are kept apart.
    """
    __tablename__ = "vendor_code_rules"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    make = Column(String, nullable=False, index=True)
    scope = Column(String, nullable=False, index=True)    # "ACME" | "MOOD" | "LEGEND"
    segment = Column(String, default="")                  # the table's heading: "Driver", "Bezel Colour"
    attribute = Column(String, default="")                # the app's field key: control, bezel_finish ...
    position = Column(Integer, default=0)                 # the segment's place in the code
    dash_after = Column(Boolean, default=False)           # a separator follows this segment
    meaning = Column(String, nullable=False)              # as printed: "Phase Cut", "Antique Brass"
    code = Column(String, nullable=False)                 # "P", "AB"
    source = Column(String, default="")                   # "2026 General Catalogue, page 33"
    created_at = Column(DateTime, default=now)


class VendorCodePart(Base):
    """What one option of one product contributes to its vendor's order code: "3000K" -> "30".

    Per product, not per brand, for the reason VendorOrderCode gives - the same words take
    different letters in different ranges. `status` says how sure it is, and only "stated"
    makes a code that is presented as the vendor's own:

      stated   the product's own spec sheet prints this code for this option
      range    the range's code matrix prints it, and the product's codes show it belongs to
               that range - a code built with it is shown as unconfirmed
      matched  the sheet's wording was matched to a matrix entry by meaning ("On/off" is the
               matrix's "Non Dim") - also shown as unconfirmed
    """
    __tablename__ = "vendor_code_parts"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    make = Column(String, nullable=False, index=True)
    specsheet_id = Column(Integer, ForeignKey("specsheets.id", ondelete="CASCADE"), nullable=False, index=True)
    attribute = Column(String, nullable=False)   # the app's field key: cct, control, beam_angle ...
    value = Column(String, nullable=False)        # the option as the product lists it
    code = Column(String, nullable=False)
    status = Column(String, default="stated")
    source = Column(String, default="")           # order_codes / code_legend / code_matrix ...
    note = Column(Text, default="")
    created_at = Column(DateTime, default=now)


class VendorCodeStructure(Base):
    """A naming structure a person set up for a manufacturer's order codes - for a range the
    manufacturer prints no code table for, or a new one it has sent the codes of (owner, 27 Oct
    2026: "if not, the user must be allowed to create a new structure and naming").

    It holds the structure's skeleton only: its segments in order, each with the detail it names
    and whether a dash follows - [{"segment": "Type", "attribute": "type", "dash_after": false}].
    The letters of each segment live where the manufacturer's own tables live, in
    vendor_code_rules (scope = `name`, `position` = the segment's place), so one engine reads
    both. A printed table (ACME, MOOD ...) has no row here: its rows ARE its structure."""
    __tablename__ = "vendor_code_structures"
    __table_args__ = (UniqueConstraint("make", "name", name="uq_vendor_code_structure_name"),)

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    make = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)         # the scope its letters are filed under: "LUNA"
    description = Column(Text, default="")
    segments = Column(JSON, default=list)
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class VendorProductNaming(Base):
    """Which naming structure one manufacturer's product is coded by, set by a person - a product
    no catalogue page names (its own spec sheet's codes only, or none at all), put in a range it
    belongs to ("the ACME naming structure should fit for that product") or in one a person made.

    `segments` says, per detail the structure names, how this product fills it:
      {"type": {"mode": "fixed", "value": "Adjustable"}, "cct": {"mode": "quoted"},
       "trim": {"mode": "out"}}
    fixed   always this value - a fact of the product, kept in NPD;
    quoted  chosen when it is quoted - an option the Configurator asks, priced by its adder;
    out     not part of this product's code.
    A detail not listed takes the product's own value where it has one. See app/duragreen.py
    (structure_code) for how the code is built from it."""
    __tablename__ = "vendor_product_naming"

    id = Column(Integer, primary_key=True)
    discipline = Column(String, default=DEFAULT_DISCIPLINE, index=True)
    make = Column(String, nullable=False, index=True)
    specsheet_id = Column(Integer, ForeignKey("specsheets.id", ondelete="CASCADE"), nullable=False,
                          unique=True, index=True)
    structure = Column(String, nullable=False)    # a scope of vendor_code_rules: "ACME", "LUNA"
    segments = Column(JSON, default=dict)
    entered_by = Column(String, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
