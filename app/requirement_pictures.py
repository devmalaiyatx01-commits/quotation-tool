"""Each requirement's picture, cut out of the client's own document.

Why this exists
---------------
While choosing a product for a requirement, a person wants to see what the client drew or
photographed for it. Those pictures are already in the client's PDF. Asking an AI assistant to
copy 150 of them into a spreadsheet, each into the right row, is slow on a free plan and is the
kind of step that goes wrong without anyone noticing. So the spreadsheet only says where a row
came from - its Ref, as printed, and its Source page - and the picture is found here, in the PDF
itself, with no model involved. The same PDF always gives the same pictures.

How a row's picture is found, most specific first
-------------------------------------------------
1. The row's Ref ("L1", "DL-02") is found on its page. Every picture on the page belongs to the
   printed Ref nearest it, vertically - schedules put a fixture's picture in its own row - and a
   row takes the nearest of the pictures that belong to it. A row whose own picture is missing
   is never handed its neighbour's: that picture is nearer the neighbour's Ref.
2. The Ref is on the page but no picture sits level with it (a line drawing is vector art, not
   a picture): the row's band of the page is cut out instead, from just above the Ref down to
   the next Ref below it, so the drawing comes with it.
3. No Ref, or not found: if the page has exactly one picture, that is it (a specsheet-style page,
   one product each). Otherwise the whole page is offered, and said to be the whole page.

The finding is reported with how it was made (`kind` and `note`), so the Match tab can say
"the picture beside L1 on page 3" or "page 3 - the Ref was not found on it" instead of implying
a certainty it does not have.

Nothing is stored beyond the uploaded document itself; pictures are rendered from it on request.
"""
import re
import secrets
import shutil
import time
from pathlib import Path

import fitz

from app.database import DATA_DIR

# A picture this small is an icon or a bullet; one covering most of the page is a background or a
# scanned page, and says nothing about one row. Thin is fine - a pull-down spot photographed side
# on is 12pt wide - so the floor is on area, with only a small floor on each side.
MIN_SIDE_PT = 6
MIN_AREA_PT2 = 120
MAX_PICTURE_SHARE = 0.6
# How far (vertically, centre to centre) a picture may sit from its row's Ref and still be that
# row's. Rows in real schedules are 25-50pt tall; a picture in the next row down is caught by the
# "nearest Ref wins" rule below, not by this distance.
MAX_REACH_PT = 70


def page_number(source) -> int:
    """The first page number in a Source cell: "3", "p3", "Page 3", "pg. 3-4" -> 3. 0 if none."""
    m = re.search(r"\d+", str(source or ""))
    return int(m.group(0)) if m else 0


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (text or "").lower())


class _Page:
    """One page, read once: its words, the pictures on it, and where each Ref sits."""

    def __init__(self, page):
        self.page = page
        # One text layer per page, shared by the word list, every description search AND the
        # picture list: building it is by far the most expensive thing here (~70ms a page), and
        # search_for() and get_image_info() each build a fresh one unless handed this. Keeping
        # images in it lets the picture list come from the same layer - page._image_info is where
        # get_image_info() looks before building its own, so filling it in is how that is said.
        self.textpage = page.get_textpage(flags=fitz.TEXTFLAGS_WORDS | fitz.TEXT_PRESERVE_IMAGES)
        self.words = page.get_text("words", textpage=self.textpage)
        page._image_info = self.textpage.extractIMGINFO(hashes=True)
        # MuPDF also keeps the page's decoded pictures in a cache with no limit set. Let go here, a
        # 60 MB PDF of drawings peaked at +189 MB instead of +244, every picture found the same.
        fitz.TOOLS.store_shrink(100)
        self.keys = [_alnum(w[4]) for w in self.words]
        self.keyset = set(self.keys)
        self.column = None          # where this page prints its Ref column (see column_of)
        self._column_for = None
        self._found = {}
        self.pictures = []
        self.xref_of = {}           # a picture's rect -> the embedded image drawn there (0: inline)
        area = page.rect.width * page.rect.height
        for info in page.get_image_info(xrefs=True):
            r = fitz.Rect(info["bbox"]) & page.rect
            if r.is_empty or min(r.width, r.height) < MIN_SIDE_PT or r.width * r.height < MIN_AREA_PT2:
                continue
            if r.width * r.height > MAX_PICTURE_SHARE * area:
                continue
            if not any(abs(r.x0 - o.x0) < 1 and abs(r.y0 - o.y0) < 1 and abs(r.x1 - o.x1) < 1 for o in self.pictures):
                self.pictures.append(r)
                self.xref_of[tuple(r)] = info.get("xref") or 0

    def _glued(self, first, last):
        """Whether words first..last are part of a longer code, joined to a neighbour by a dash:
        the PDF prints "WW1-T" as "WW1-" and "T", and "WW1" must not be found in it."""
        def near(a, b):
            wa, wb = self.words[a], self.words[b]
            return abs(wa[1] - wb[1]) < 2 and 0 <= wb[0] - wa[2] < 3
        dashes = "-–—/"
        after, before = last + 1, first - 1
        if after < len(self.words) and near(last, after) and (
                self.words[last][4].endswith(tuple(dashes)) or self.words[after][4].startswith(tuple(dashes))):
            return True
        if before >= 0 and near(before, first) and (
                self.words[before][4].endswith(tuple(dashes)) or self.words[first][4].startswith(tuple(dashes))):
            return True
        return False

    def find(self, ref: str):
        if ref not in self._found:
            self._found[ref] = self._find(ref)
        return self._found[ref]

    def _find(self, ref: str):
        """Every place `ref` is printed, top to bottom. Compared on letters and digits only, over
        up to three neighbouring words, so "WW1-T" is found when the PDF splits it as "WW1" "-T"
        or prints an en dash - and "L1" is still never found inside "L10", because a match has to
        use up whole words exactly."""
        target = _alnum(ref)
        if not target:
            return []
        hits = []
        for i, key in enumerate(self.keys):
            if not key or not target.startswith(key):
                continue
            acc, rect = "", None
            for k in range(3):
                if i + k >= len(self.keys):
                    break
                part = self.keys[i + k]
                acc += part
                w = self.words[i + k]
                rect = fitz.Rect(w[:4]) if rect is None else rect | fitz.Rect(w[:4])
                if acc == target:
                    if not self._glued(i, i + k):
                        hits.append(rect)
                    break
                if not target.startswith(acc):
                    break
        return sorted(hits, key=lambda r: (round(r.y0), r.x0))


    def column_of(self, refs):
        """Where this page prints its Ref column, as an x-range, or None if it has no such column.

        A schedule numbered 1, 2, 3... has Refs that are also printed inside its descriptions -
        the "36" of "36 deg", the "24" of "24V DC" - and the one nearest a picture is usually not
        the row's number at all. What tells them apart is that the row numbers line up in a column
        and no other number does. Found rather than assumed: a schedule with real codes ("L1",
        "DL-02") has no such collisions, the test below does not pass, and nothing is filtered.
        """
        bins, seen = {}, set()
        for ref in refs:
            for r in self.find(ref):
                b = int(r.x0 // 8)
                bins.setdefault(b, {"refs": set(), "x": []})
                bins[b]["refs"].add(ref)
                bins[b]["x"].append(r.x0)
                seen.add(ref)
        if not bins:
            return None
        best = max(bins, key=lambda b: len(bins[b]["refs"]))
        lined_up = bins[best]["refs"]
        # Three different Refs sharing an x-position, and most of the Refs on this page among
        # them: fewer than that is a coincidence, not a column.
        if len(lined_up) < 3 or len(lined_up) * 2 < len(seen):
            return None
        xs = bins[best]["x"]
        return (min(xs) - 6, max(xs) + 6)

    def prepare(self, refs):
        """Work out this page's Ref column once, for the sheet's whole set of Refs."""
        if self._column_for is not refs:
            self.column = self.column_of(refs)
            self._column_for = refs
        return self

    def hits(self, ref):
        """Where `ref` is printed as a row's number, not as a quantity or a dimension.

        On a page with a Ref column, only the column counts - with no fallback to the other
        places the same digits appear. A qty of 26 sitting a third of a point nearer a picture
        than the row's own "1" is exactly how a row ends up with no picture, and falling back
        would keep that failure for every Ref this page does not itself number.
        """
        found = self.find(ref)
        if self.column is None:
            return found
        return [r for r in found if self.column[0] <= r.x0 <= self.column[1]]


# Words too common in a lighting schedule to say which page a row is on.
_COMMON = {"white", "black", "warm", "cool", "grey", "gray", "with", "from", "into", "this",
           "that", "each", "type", "size", "long", "wide", "deep", "high", "watt", "watts",
           "lumen", "lumens", "beam", "angle", "light", "lights", "lighting", "driver", "drivers",
           "power", "supply", "profile", "recessed", "surface", "mounted", "mounting", "finish",
           "colour", "color", "constant", "current", "voltage", "dimmable", "cutout", "trim"}


def _distinctive(text: str) -> list:
    """The few words most likely to name this row's own product - longest first.

    A model name ("OLYN", "MIRACH", "ATRIA") is printed on one page of a schedule and nowhere
    else, which is exactly what is needed to find a row's page when its Ref cannot.
    """
    out, seen = [], set()
    for w in sorted(re.findall(r"[A-Za-z]{4,}", text or ""), key=len, reverse=True):
        key = w.lower()
        if key in _COMMON or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) == 3:
            break
    return out


def _box(rect, page, pad=3):
    r = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad) & page.rect
    return [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)]


def _row_band(page, anchor, anchors):
    """From just above the anchor to just above the next anchor below it (or 150pt, if it is the
    last), across the page. Where a schedule's row is, without having to see its lines."""
    below = [a.y0 for a in anchors if a.y0 > anchor.y1 + 4]
    bottom = min(below) - 2 if below else anchor.y0 + 150
    bottom = min(max(bottom, anchor.y1 + 30), anchor.y0 + 260, page.rect.y1)
    return fitz.Rect(page.rect.x0, max(page.rect.y0, anchor.y0 - 6), page.rect.x1, bottom)


def _share_columns(pages: dict):
    """Give every page the Ref column the rest of the document agrees on.

    A schedule prints its row numbers in the same place on every page, but a last page carrying
    two rows has too few of them to see a column of its own - and without one, every quantity and
    dimension on it counts as a row number again. The pages that can see their column decide it
    for the pages that cannot.
    """
    seen = [p.column for p in pages.values() if p.column]
    if len(seen) < 2:
        return
    lo = sum(c[0] for c in seen) / len(seen)
    hi = sum(c[1] for c in seen) / len(seen)
    if any(abs(c[0] - lo) > 12 or abs(c[1] - hi) > 12 for c in seen):
        return                       # the pages do not agree; better to filter nothing
    for page in pages.values():
        if page.column is None:
            page.column = (lo, hi)


def _page_of(get, page_count: int, description: str):
    """The one page this row's own words are printed on, or None if that is not certain."""
    words = _distinctive(description)
    if not words:
        return None
    for probe in (words, words[:2], words[:1]):
        found = [i for i in range(page_count) if all(w in get(i).keyset for w in probe)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            # Printed on several pages; a shorter probe can only be printed on more of them.
            return None
    return None


def locate(doc, rows: list) -> dict:
    """{key: {page, box, kind, note, page_size}} for rows [{key, page, ref, description}], in the
    sheet's order.

    Every row that names a page (or whose Ref is printed on exactly one page) gets an entry: its
    own picture, its row of the page, or the whole page - `kind` says which, `note` says why."""
    pages = {}

    def get(index):
        if index not in pages:
            pages[index] = _Page(doc[index])
        return pages[index].prepare(all_refs)

    all_refs = sorted({str(r.get("ref") or "").strip() for r in rows} - {""})

    # 1. which page each row is on
    placed = []
    for row in rows:
        ref = str(row.get("ref") or "").strip()
        number = page_number(row.get("page"))
        index = number - 1 if 1 <= number <= doc.page_count else None
        if index is None and ref and doc.page_count <= 60:
            # No usable page: look for the Ref everywhere, but only settle on a page where it is
            # the only one - a Ref printed on several pages does not say which.
            found = [i for i in range(doc.page_count) if get(i).find(ref)]
            if len(found) == 1:
                index = found[0]
            elif len(found) > 1:
                # Printed on several pages, which is what a schedule numbered 1, 2, 3... always
                # looks like: those same digits are every other row's quantity and dimension. The
                # page that prints it in its own Ref column is the page that means it.
                _share_columns(pages)
                numbered = [i for i in found if get(i).hits(ref)]
                index = numbered[0] if len(numbered) == 1 else None
        if index is None and doc.page_count <= 60:
            # Still nothing. A Ref of "1" is printed on every page of anything, so a schedule that
            # numbers its rows can never be placed by Ref - but its own words can place it. The
            # product's name is on one page and nowhere else.
            index = _page_of(get, doc.page_count, row.get("description"))
        if index is not None:
            placed.append((row, ref, index))

    # 2. each row's anchor on its page: the k-th row with a given Ref on a page takes the k-th
    #    place that Ref is printed there, so an area-by-area schedule listing ED-75 twice on one
    #    page gives each of those rows its own line. A row with no Ref is anchored by the start of
    #    its description, when that is printed on the page as it is.
    # Every page that carries a row, read before any anchoring: sharing the Ref column needs the
    # pages that can see one, and a sheet whose Source column is filled never opens the others.
    for _row, _ref, index in placed:
        get(index)
    _share_columns(pages)

    anchor_of, seen = {}, {}
    for row, ref, index in placed:
        p = get(index)
        if ref:
            hits = p.hits(ref)
        else:
            text = " ".join(str(row.get("description") or "").split())[:40]
            hits = (sorted(p.page.search_for(text, textpage=p.textpage), key=lambda r: (round(r.y0), r.x0))
                    if len(text) >= 12 else [])
        k = seen.get((index, ref or text), 0)
        seen[(index, ref or text)] = k + 1
        if hits:
            anchor_of[str(row.get("key"))] = hits[min(k, len(hits) - 1)]

    # 3. which Ref each picture belongs to: the nearest printed Ref of ANY row in the sheet. A
    #    row only gets a picture that is nearer to it than to any other Ref, so it can never be
    #    handed the next row's picture just because its own is missing.
    claims = {}
    for index in {i for _r, _ref, i in placed}:
        p = get(index)
        anchors = [a for ref in all_refs for a in p.hits(ref)]
        anchors += [anchor_of[str(r.get("key"))] for r, ref, i in placed if i == index and not ref and str(r.get("key")) in anchor_of]
        owned = {}
        for pic in p.pictures:
            mid = (pic.y0 + pic.y1) / 2
            near = min(anchors, key=lambda a: abs((a.y0 + a.y1) / 2 - mid), default=None)
            if near is not None and abs((near.y0 + near.y1) / 2 - mid) <= max(MAX_REACH_PT, pic.height / 2 + 6):
                owned.setdefault(tuple(near), []).append(pic)
        claims[index] = (owned, anchors)

    out = {}
    for row, ref, index in placed:
        key = str(row.get("key"))
        p = get(index)
        page = p.page
        owned, anchors = claims[index]
        label = ref or "this row"
        anchor = anchor_of.get(key)
        if anchor is not None:
            mine = owned.get(tuple(anchor), [])
            if mine:
                mid = (anchor.y0 + anchor.y1) / 2
                pic = min(mine, key=lambda r: abs((r.y0 + r.y1) / 2 - mid))
                entry = {"kind": "picture", "box": _box(pic, page, pad=0), "xref": p.xref_of.get(tuple(pic), 0),
                         "note": "the picture beside %s" % label}
            else:
                entry = {"kind": "row", "box": _box(_row_band(page, anchor, anchors), page, pad=0),
                         "note": "no picture in the row for %s" % label}
        elif len(p.pictures) == 1:
            entry = {"kind": "picture", "box": _box(p.pictures[0], page, pad=0),
                     "xref": p.xref_of.get(tuple(p.pictures[0]), 0),
                     "note": "the only picture on the page" + (" (%s is not printed on it)" % ref if ref else "")}
        else:
            entry = {"kind": "page", "box": _box(page.rect, page, pad=0),
                     "note": ("the whole page - %s is not printed on it" % ref) if ref
                     else "the whole page - this row has no Ref to look for"}
        entry.update({"page": index + 1, "page_size": [round(page.rect.width, 1), round(page.rect.height, 1)]})
        out[key] = entry
    return out


def is_image(doc, xref) -> bool:
    """Whether `xref` is an image in this document - checked before decoding one a request names."""
    try:
        return 0 < int(xref) < doc.xref_length() and doc.xref_get_key(int(xref), "Subtype")[1] == "/Image"
    except Exception:
        return False


def render_image(doc, xref: int, width: int) -> tuple:
    """(bytes, media type) of one embedded picture, at most `width` pixels wide.

    Decoding just the picture is what makes a list of 150 of them quick: drawing any part of a
    page makes MuPDF interpret the whole page, every large picture on it included - about 0.7s a
    crop on a real schedule, against a few milliseconds for the picture alone, which also comes
    out at its own full resolution. A picture with transparency keeps it, as a PNG."""
    info = doc.extract_image(xref)
    pix = fitz.Pixmap(info["image"])
    if info.get("smask"):
        try:
            pix = fitz.Pixmap(pix, fitz.Pixmap(doc, info["smask"]))
        except Exception:
            pass
    if pix.colorspace and pix.colorspace.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.width > width:
        pix = fitz.Pixmap(pix, width, max(1, round(pix.height * width / pix.width)))
    if pix.alpha:
        return pix.tobytes("png"), "image/png"
    return pix.tobytes("jpg", jpg_quality=85), "image/jpeg"


def render(doc, page_number_1: int, box, width: int) -> bytes:
    """A JPEG of `box` on the page, `width` pixels wide (never enlarged past 4x)."""
    page = doc[page_number_1 - 1]
    clip = fitz.Rect(box) & page.rect
    if clip.is_empty:
        clip = page.rect
    zoom = max(0.2, min(4.0, width / max(clip.width, 1)))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("jpg", jpg_quality=82)


# --- where an attached document is kept ------------------------------------------------------
#
# A client's PDF is kept only so its pictures can be drawn while someone works through the
# requirement, under a random token that is the only way to reach it - never listed, never served
# whole. Documents older than KEEP_DAYS are removed whenever a new one arrives, so the folder does
# not grow without end on a small server, and a client's document does not outlive the job.


STORE = DATA_DIR / "match_documents"
KEEP_DAYS = 30
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
SHEET_PICTURE_RE = re.compile(r"^\d{1,6}\.(png|jpg|jpeg|gif|bmp)$")
WEB_IMAGE_EXT = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "gif": "gif", "bmp": "bmp"}


# Anything that keeps a document open registers here. Windows will not delete a file that is
# still open, and rmtree() below ignores errors - so without this, a document held open for
# serving thumbnails would quietly outlive its KEEP_DAYS and never be cleared.
_RELEASERS = []


def on_release(fn):
    """Register `fn(token)`, called to close any open handle before that document is deleted."""
    _RELEASERS.append(fn)


def _clear_old():
    if not STORE.is_dir():
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    for entry in STORE.iterdir():
        # Only folders this module made: a token name, and nothing else in here is touched.
        if entry.is_dir() and TOKEN_RE.match(entry.name) and entry.stat().st_mtime < cutoff:
            for release in _RELEASERS:
                try:
                    release(entry.name)
                except Exception:
                    pass
            shutil.rmtree(entry, ignore_errors=True)


def new_folder() -> tuple:
    """(token, folder) for a new document's files."""
    _clear_old()
    token = secrets.token_hex(16)
    folder = STORE / token
    folder.mkdir(parents=True)
    return token, folder


def folder_for(token: str):
    """The folder behind a token, or None - for a malformed token as for one that has expired."""
    if not TOKEN_RE.match(token or ""):
        return None
    folder = STORE / token
    return folder if folder.is_dir() else None


def save_sheet_pictures(folder: Path, pictures: dict) -> dict:
    """Writes {sheet row: (bytes, ext)} and returns {sheet row: file name}. A picture in a format
    a browser cannot show (Excel also stores EMF/WMF drawings) is converted to PNG when PyMuPDF
    can read it, and left out when it cannot."""
    out = {}
    target = folder / "sheet"
    target.mkdir(exist_ok=True)
    for row, (data, ext) in pictures.items():
        ext = WEB_IMAGE_EXT.get((ext or "").lower().lstrip("."))
        if ext is None:
            try:
                data, ext = fitz.Pixmap(data).tobytes("png"), "png"
            except Exception:
                continue
        name = "%d.%s" % (row, ext)
        (target / name).write_bytes(data)
        out[row] = name
    return out
