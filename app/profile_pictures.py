"""The profiles a strip light fits into, cut out of its specsheet page.

A InHouse strip specsheet prints, in a DIMENSION box, the strip's own dimensioned drawing and
beside it the aluminium profiles the strip is made to sit in - and, for the neon bands and lens
grazers, an ACCESSORIES box of profile and clip pictures. Only the strip's own drawing was ever
stored, so finding a profile meant opening the PDF. This cuts the rest out as pictures of their
own, which the Library shows and a BOQ row can print in its Dimension column.

How, and why this way rather than by pulling the PDF's embedded images out: a profile may be an
embedded picture on one page, line art on another, and part of one big composite on a third - all
three appear in this library. Rendering the box and then cutting it apart treats them alike, and
what comes out is what a person sees on the sheet.

    find_boxes          the DIMENSION / ACCESSORIES boxes on a page. A box title is centred in a
                        grey bar; the specification table has a row labelled "ACCESSORIES" too,
                        which is left-aligned in its cell, and that is how the two are told apart.
    _ink_mask           everything that is not near-white, with the box's own thin rules erased -
                        a border line running under two drawings would otherwise join them. Only
                        bands at most 3 px thick: a photograph's pale background is dense too.
    _components         connected ink, so a drawing is grouped by what it is rather than by the
                        straight white gaps between things, which are as small as 2.7 pt here.
    _merge_fragments    dimension numbers and arrowheads rejoin the drawing they belong to.
    only_text           a piece that is nothing but lettering - a caption drawn inside a composite
                        picture. Applied only to ACCESSORIES: a thin profile outline breaks into
                        many small dark specks when measured, and looks exactly like lettering.

Everything here uses Pillow and PyMuPDF only, the two libraries the live server already has.
"""
from collections import deque
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFilter

# The typologies this is for. A downlight's DIMENSION box holds its own measurements, not profiles,
# so extracting there would offer a "profile" that is nothing of the sort.
STRIP_TYPOLOGIES = (
    "Flexible Strip Light",
    "Flexible Neon / LED Band",
    "Flexible LED Lens Grazer",
    "LED Strip",
)

HEADERS = ("DIMENSION", "ACCESSORIES")
OUT_ZOOM = 3.0          # what a saved picture is rendered at - 216 dpi
GROUP_ZOOM = 1.5        # what shapes are found at
PROFILE_MAX_ASPECT = 2.5  # wider than this in a DIMENSION box is a view of the strip, not a profile
MIN_SIDE_PT = 18
CAPTION_REACH_PT = 30
ACCESSORY_SPLIT_GAP_PT = 4


def is_strip_typology(typology) -> bool:
    return (typology or "").strip() in STRIP_TYPOLOGIES


def find_boxes(page):
    """(title, rect) for every DIMENSION / ACCESSORIES box on the page."""
    blocks = page.get_text("blocks")
    bars = []
    for d in page.get_drawings():
        r = d.get("rect")
        if (r is not None and d.get("fill") is not None and 100 < r.width < page.rect.width * 0.75
                and 6 < r.height < 40):
            bars.append(r)
    boxes = []
    for b in blocks:
        title = b[4].strip().upper()
        if title not in HEADERS:
            continue
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        bar = next((r for r in sorted(bars, key=lambda r: r.width)
                    if r.x0 <= cx <= r.x1 and r.y0 - 2 <= cy <= r.y1 + 2
                    and abs(cx - (r.x0 + r.x1) / 2) < 12), None)
        if bar is None:
            continue                      # a table label, not a box title
        x0, x1, top = bar.x0, bar.x1, bar.y1 + 1
        next_titles = [bb[1] for bb in blocks
                       if bb[1] > top + 5 and bb[4].strip().upper() in HEADERS + ("PRODUCT IMAGE",)
                       and x0 - 2 <= (bb[0] + bb[2]) / 2 <= x1 + 2]
        limit = min(next_titles, default=page.rect.height - 40) - 4
        bottoms = []
        for img in page.get_images(full=True):
            for r in page.get_image_rects(img[0]):
                if r.y0 >= top - 2 and r.x0 >= x0 - 2 and r.x1 <= x1 + 2 and r.y1 <= limit:
                    bottoms.append(r.y1)
        for d in page.get_drawings():
            r = d.get("rect")
            if (r is not None and r.y0 >= top - 2 and r.x0 >= x0 - 2 and r.x1 <= x1 + 2
                    and r.y1 <= limit and r.height < 300):
                bottoms.append(r.y1)
        for bb in blocks:
            if bb[1] >= top - 2 and bb[0] >= x0 - 2 and bb[2] <= x1 + 2 and bb[3] <= limit:
                bottoms.append(bb[3])
        bottom = min(max(bottoms, default=top + 150) + 4, limit)
        if bottom > top + 20:
            boxes.append((title, fitz.Rect(x0 + 3, top + 1, x1 - 3, bottom)))
    return boxes


def _ink_mask(img):
    mask = img.convert("L").point(lambda v: 255 if v < 238 else 0)
    w, h = mask.size
    draw = ImageDraw.Draw(mask)

    def thin_dense_runs(values, limit):
        runs, start = [], None
        for i, v in enumerate(list(values) + [0]):
            if v >= 0.6 * 255:
                if start is None:
                    start = i
            elif start is not None:
                if i - start <= limit:
                    runs.append((start, i))
                start = None
        return runs

    for a, b in thin_dense_runs(mask.resize((1, h), Image.BOX).getdata(), 3):
        for y in range(a, b):
            draw.line([(0, y), (w, y)], fill=0)
    for a, b in thin_dense_runs(mask.resize((w, 1), Image.BOX).getdata(), 3):
        for x in range(a, b):
            draw.line([(x, 0), (x, h)], fill=0)
    return mask.filter(ImageFilter.MaxFilter(3))


def _components(mask):
    w, h = mask.size
    data = mask.tobytes()
    seen = bytearray(w * h)
    boxes = []
    for start in range(w * h):
        if data[start] == 0 or seen[start]:
            continue
        seen[start] = 1
        q = deque([start])
        x0 = x1 = start % w
        y0 = y1 = start // w
        while q:
            i = q.popleft()
            x, y = i % w, i // w
            if x < x0: x0 = x
            if x > x1: x1 = x
            if y < y0: y0 = y
            if y > y1: y1 = y
            for j in (i - 1 if x > 0 else -1, i + 1 if x < w - 1 else -1, i - w, i + w):
                if 0 <= j < w * h and data[j] and not seen[j]:
                    seen[j] = 1
                    q.append(j)
        boxes.append([x0, y0, x1 + 1, y1 + 1])
    return boxes


def _merge_fragments(boxes, near):
    big = [b for b in boxes if (b[2] - b[0]) >= near * 2.2 and (b[3] - b[1]) >= near * 2.2]
    small = [b for b in boxes if b not in big]
    changed = True
    while changed:
        changed = False
        for a in big:
            for b in big:
                if a is not b and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    a[0], a[1] = min(a[0], b[0]), min(a[1], b[1])
                    a[2], a[3] = max(a[2], b[2]), max(a[3], b[3])
                    big.remove(b)
                    changed = True
                    break
            if changed:
                break
    for s in small:
        best, dist = None, None
        for b in big:
            dx = max(b[0] - s[2], s[0] - b[2], 0)
            dy = max(b[1] - s[3], s[1] - b[3], 0)
            d = (dx * dx + dy * dy) ** 0.5
            if dist is None or d < dist:
                best, dist = b, d
        if best is not None and dist <= near:
            best[0], best[1] = min(best[0], s[0]), min(best[1], s[1])
            best[2], best[3] = max(best[2], s[2]), max(best[3], s[3])
    return big


def only_text(crop):
    """Lettering and nothing else: many small dark shapes, almost no grey. A pale part photographed
    on a pale background has no large dark shape either, but it has a few holes and edges, not
    dozens of letters - which is why the count matters."""
    w, h = crop.size
    if not w or not h:
        return True
    dark = crop.convert("L").point(lambda v: 255 if v < 150 else 0)
    shapes = _components(dark)
    if len(shapes) < 8:
        return False
    widest = max(b[2] - b[0] for b in shapes)
    tallest = max(b[3] - b[1] for b in shapes)
    grey = crop.convert("L").point(lambda v: 255 if 150 <= v < 238 else 0)
    grey_share = sum(grey.histogram()[255:]) / float(w * h)
    return widest < 0.22 * w and tallest < 0.35 * h and grey_share < 0.12


def _split_on_white_rows(img, rect, min_gap_pt):
    mask = img.convert("L").point(lambda v: 255 if v < 238 else 0)
    rows = list(mask.resize((1, mask.height), Image.BOX).getdata())
    gap_px = max(1, int(min_gap_pt * OUT_ZOOM))
    bands, start, empty, last = [], None, 0, 0
    for y, v in enumerate(rows + [0] * gap_px):
        if v > 0:
            if start is None:
                start = y
            empty = 0
            last = y
        elif start is not None:
            empty += 1
            if empty >= gap_px:
                bands.append((start, last + 1))
                start = None
    if len(bands) <= 1:
        return [rect]
    scale = rect.height / float(img.height)
    return [fitz.Rect(rect.x0, rect.y0 + a * scale, rect.x1, rect.y0 + b * scale) for a, b in bands]


def _text_share(piece_pt, text_rects):
    area = piece_pt.width * piece_pt.height
    if area <= 0:
        return 0.0
    covered = 0.0
    for t in text_rects:
        if piece_pt.intersects(t):
            hit = piece_pt & t
            covered += hit.width * hit.height
    return min(1.0, covered / area)


def _render(page, rect):
    pix = page.get_pixmap(matrix=fitz.Matrix(OUT_ZOOM, OUT_ZOOM), clip=rect, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _cut_box(page, rect, title):
    """[(kind, PIL image)] for one box - kind is "profile" or "accessory"."""
    small = page.get_pixmap(matrix=fitz.Matrix(GROUP_ZOOM, GROUP_ZOOM), clip=rect, alpha=False)
    simg = Image.frombytes("RGB", (small.width, small.height), small.samples)
    near = int(6 * GROUP_ZOOM)
    shapes = _merge_fragments(_components(_ink_mask(simg)), near)
    text_rects = [fitz.Rect(b[:4]) for b in page.get_text("blocks") if fitz.Rect(b[:4]).intersects(rect)]

    def to_pt(b, pad=0):
        return fitz.Rect(rect.x0 + (b[0] - pad) / GROUP_ZOOM, rect.y0 + (b[1] - pad) / GROUP_ZOOM,
                         rect.x0 + (b[2] + pad) / GROUP_ZOOM, rect.y0 + (b[3] + pad) / GROUP_ZOOM) & rect

    pieces = [{"pt": to_pt(b, 3)} for b in shapes]
    captions = [p for p in pieces if _text_share(p["pt"], text_rects) >= 0.6]
    drawings = [p for p in pieces if p not in captions]
    for c in captions:
        above = [d for d in drawings if d["pt"].y1 <= c["pt"].y0 + 2
                 and c["pt"].y0 - d["pt"].y1 <= CAPTION_REACH_PT
                 and d["pt"].x0 < c["pt"].x1 and c["pt"].x0 < d["pt"].x1]
        if above:
            target = min(above, key=lambda d: c["pt"].y0 - d["pt"].y1)
            target["pt"] = target["pt"] | c["pt"]

    out = []
    for p in sorted(drawings, key=lambda p: (round(p["pt"].y0 / 20), p["pt"].x0)):
        r = p["pt"]
        if r.width < MIN_SIDE_PT and r.height < MIN_SIDE_PT:
            continue
        if title == "DIMENSION":
            # the strip's own dimensioned drawing is already stored as the product's line diagram
            if r.width / max(r.height, 1) >= PROFILE_MAX_ASPECT:
                continue
            pix = page.get_pixmap(matrix=fitz.Matrix(OUT_ZOOM, OUT_ZOOM), clip=r, alpha=False)
            out.append(("profile", Image.frombytes("RGB", (pix.width, pix.height), pix.samples)))
            continue
        pix = page.get_pixmap(matrix=fitz.Matrix(OUT_ZOOM, OUT_ZOOM), clip=r, alpha=False)
        crop = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        parts = _split_on_white_rows(crop, r, ACCESSORY_SPLIT_GAP_PT)
        joined = []
        for part in parts:
            # a short strip is a title or a caption, not a picture of its own
            if part.height < MIN_SIDE_PT and joined:
                joined[-1] = joined[-1] | part
            elif joined and joined[-1].height < MIN_SIDE_PT:
                joined[-1] = joined[-1] | part
            else:
                joined.append(part)
        # A part that is nothing but lettering is a caption, and it belongs to the picture beside
        # it: merged back, never dropped. Dropping such parts cost one product both its U-channel
        # drawings - a thin outline measures like lettering once its caption is cut away from it -
        # so nothing found here is ever thrown away, only joined.
        rendered = []
        for part in joined:
            ppix = page.get_pixmap(matrix=fitz.Matrix(OUT_ZOOM, OUT_ZOOM), clip=part, alpha=False)
            rendered.append([part, Image.frombytes("RGB", (ppix.width, ppix.height), ppix.samples)])
        final = []
        for index, (part, piece) in enumerate(rendered):
            probe = piece.copy()
            probe.thumbnail((240, 240))
            caption = only_text(probe) and len(rendered) > 1
            if caption and final:
                merged = final[-1][0] | part
                final[-1] = [merged, _render(page, merged)]
                continue
            if caption and index + 1 < len(rendered):
                merged = part | rendered[index + 1][0]
                rendered[index + 1] = [merged, _render(page, merged)]
                continue
            final.append([part, piece])
        for part, piece in final:
            if part.height < MIN_SIDE_PT:
                continue
            out.append(("accessory", piece))
    return out


def extract_profile_pictures(pdf_path, page_number, images_dir, base_name, typology=None):
    """The profile and accessory pictures on one specsheet page, saved as files.

    Returns diagram entries ready to sit in Specsheet.diagrams:
    {"path", "width", "height", "label": "Profile 1"|"Accessory 1", "kind": "profile"|"accessory",
     "source": "extracted"}.

    Labels deliberately avoid the words npd.pick_images matches on ("product", "image", "photo",
    "dimension", "diagram", "drawing", "line"), so an extracted picture can never be mistaken for
    a product's photograph or its line diagram on the generated specsheet.

    Returns [] for anything that is not a strip typology, and for any page where nothing is found -
    a page this cannot read gives nothing rather than something wrong. Never raises: a picture that
    cannot be cut out costs that picture, never the upload.
    """
    if typology is not None and not is_strip_typology(typology):
        return []
    try:
        path = Path(pdf_path)
        if not path.exists():
            return []
        images_dir = Path(images_dir)
        images_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(str(path))
        page = doc[max(0, (page_number or 1) - 1)]
        found = []
        for title, rect in find_boxes(page):
            found += _cut_box(page, rect, title)
        entries, counts = [], {"profile": 0, "accessory": 0}
        for kind, image in found:
            counts[kind] += 1
            label = "%s %d" % ("Profile" if kind == "profile" else "Accessory", counts[kind])
            out_path = images_dir / ("%s_%s%d.png" % (base_name, kind, counts[kind]))
            image.save(str(out_path))
            entries.append({"path": str(out_path), "width": image.width, "height": image.height,
                            "label": label, "kind": kind, "source": "extracted"})
        return entries
    except Exception:
        return []


def merge_into_diagrams(diagrams, extracted):
    """`diagrams` with any previously extracted pictures replaced by `extracted`.

    The product's own pictures - its photograph, its line diagram - are left exactly as they are,
    wherever they sit, so re-running this never disturbs what the Library and the exports read.
    """
    kept = [d for d in (diagrams or []) if d.get("source") != "extracted"]
    return kept + list(extracted or [])
