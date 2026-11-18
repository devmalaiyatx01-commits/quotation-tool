"""Build TUTORIAL.pdf: the whole of TUTORIAL.md, typeset, with a diagram for every concept.

    python scripts/maintenance/build_tutorial_pdf.py            # writes TUTORIAL.pdf at the repo root
    python scripts/maintenance/build_tutorial_pdf.py --out x.pdf

What it produces, in order: a cover; a clickable table of contents; a list of figures; an
architecture atlas (the system-wide diagrams); then every Part of the tutorial with its section
diagrams placed right after each section's first paragraph. Headings become PDF bookmarks, and
the tutorial's own [links](#anchors) jump to the section they name.

The text comes from TUTORIAL.md every time - nothing is copied into this script - so after the
tutorial changes, rebuilding is the whole update. The diagrams live in tutorial_diagrams*.py,
keyed by section number; a section that gains a number keeps its figure, and a figure whose key
no longer matches any heading is reported at the end rather than silently dropped.

Fonts are the Windows system's Segoe UI and Consolas, because the base PDF fonts cannot draw
the tutorial's rupee signs and arrows. Nothing is installed; reportlab is already a dependency.
"""
# --- repo-root shim ------------------------------------------------------------------
# Running a script file puts ITS OWN directory on sys.path, not the repo root, so `app`
# would not import and a `Path(__file__).parent` would no longer be the project root.
# Both are fixed here by walking up to the directory that contains app/ - the same rule
# app/database.py uses. Run this script from anywhere; paths below stay repo-relative.
import pathlib as _pathlib, sys as _sys
_REPO_ROOT = next((p for p in _pathlib.Path(__file__).resolve().parents
                   if (p / "app").is_dir()), None)
if _REPO_ROOT is None:
    raise SystemExit("Cannot find the repo root: no parent directory contains app/.")
_sys.path.insert(0, str(_REPO_ROOT))
# --- end shim ------------------------------------------------------------------------
import argparse
import datetime
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
                                Table, TableStyle, KeepTogether, NextPageTemplate, CondPageBreak, Flowable)
from reportlab.platypus.tableofcontents import TableOfContents

FONT_DIR = "C:/Windows/Fonts/"
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
FRAME_W = PAGE_W - 2 * MARGIN
ACCENT = colors.HexColor("#3563A9")
INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#6B7280")


def register_fonts():
    fonts = {"UI": "segoeui.ttf", "UI-B": "segoeuib.ttf", "UI-I": "segoeuii.ttf", "UI-BI": "segoeuiz.ttf",
             "Mono": "consola.ttf", "Mono-B": "consolab.ttf", "Mono-I": "consolai.ttf", "Mono-BI": "consolaz.ttf",
             "Sym": "seguisym.ttf"}
    for name, f in fonts.items():
        pdfmetrics.registerFont(TTFont(name, FONT_DIR + f))
    for fam, n, b, i, bi in (("UI", "UI", "UI-B", "UI-I", "UI-BI"), ("Mono", "Mono", "Mono-B", "Mono-I", "Mono-BI")):
        addMapping(fam, 0, 0, n)
        addMapping(fam, 1, 0, b)
        addMapping(fam, 0, 1, i)
        addMapping(fam, 1, 1, bi)


# ------------------------------------------------------------------------------------ styles

def S(name, **kw):
    base = dict(fontName="UI", fontSize=9.4, leading=13.2, textColor=INK, spaceAfter=5)
    base.update(kw)
    return ParagraphStyle(name, **base)


STY = {}


def build_styles():
    STY.update({
        "body": S("body"),
        "h2": S("h2", fontName="UI-B", fontSize=19, leading=23, textColor=ACCENT, spaceBefore=4, spaceAfter=10),
        "h3": S("h3", fontName="UI-B", fontSize=13, leading=16.5, spaceBefore=12, spaceAfter=6),
        "h4": S("h4", fontName="UI-B", fontSize=10.6, leading=14, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#374151")),
        "li": S("li", spaceAfter=2.5),
        "quote": S("quote", fontName="UI-I", textColor=colors.HexColor("#374151")),
        "code": S("code", fontName="Mono", fontSize=7.3, leading=9.1, spaceAfter=0, textColor=colors.HexColor("#111827")),
        "cell": S("cell", fontSize=7.9, leading=10.2, spaceAfter=0),
        "cellh": S("cellh", fontName="UI-B", fontSize=7.9, leading=10.2, spaceAfter=0),
        "figtitle": S("figtitle", fontName="UI-B", fontSize=9.2, leading=12, textColor=ACCENT, spaceAfter=3, spaceBefore=6),
        "figcap": S("figcap", fontName="UI-I", fontSize=8.1, leading=11, textColor=MUTED, spaceAfter=10),
        "cover1": S("cover1", fontName="UI-B", fontSize=30, leading=36, textColor=ACCENT, alignment=TA_LEFT),
        "cover2": S("cover2", fontSize=14, leading=20, textColor=INK),
        "cover3": S("cover3", fontSize=9.5, leading=14, textColor=MUTED),
        "tocTitle": S("tocTitle", fontName="UI-B", fontSize=19, leading=23, textColor=ACCENT, spaceAfter=12),
    })


# ------------------------------------------------------------------------------ inline markup

def slug(text):
    """GitHub's anchor for a heading - what the tutorial's own [links](#...) point at."""
    s = re.sub(r"[`*]", "", text).strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


ANCHORS = set()


def inline(text):
    """Markdown inline -> reportlab paragraph XML: links, `code`, **bold**, *italic*."""
    links = []

    def keep_link(m):
        links.append((m.group(1), m.group(2)))
        return "\x00%d\x00" % (len(links) - 1)

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", keep_link, text)
    codes = []

    def keep_code(m):
        codes.append(m.group(1))
        return "\x01%d\x01" % (len(codes) - 1)

    text = re.sub(r"`([^`]+)`", keep_code, text)
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = text.replace("\u2713", '<font name="Sym">\u2713</font>')
    text = re.sub(r"\x01(\d+)\x01", lambda m: '<font name="Mono" size="-0.6" color="#9A3412">%s</font>'
                  % esc(codes[int(m.group(1))]), text)

    def put_link(m):
        label, url = links[int(m.group(1))]
        inner = inline(label)
        if url.startswith("#"):
            return '<a href="%s" color="#3563A9">%s</a>' % (url, inner) if url[1:] in ANCHORS else inner
        if url.startswith("http"):
            return '<a href="%s" color="#3563A9">%s</a>' % (esc(url), inner)
        return inner                              # a repo file: its name is the useful part
    return re.sub(r"\x00(\d+)\x00", put_link, text)


# --------------------------------------------------------------------------- markdown blocks

def parse(md):
    """Markdown -> [(kind, ...)] blocks. Handles exactly what TUTORIAL.md uses."""
    lines = md.split("\n")
    blocks, i = [], 0
    item_re = re.compile(r"^(\s*)(-|\*|\d+\.)\s+(.*)$")
    while i < len(lines):
        line = lines[i]
        st = line.strip()
        if st.startswith("```"):
            lang = st[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            indent = min((len(l) - len(l.lstrip()) for l in body if l.strip()), default=0)
            blocks.append(("code", [l[indent:] for l in body], lang))
            i += 1
            continue
        if not st:
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            blocks.append(("h", len(m.group(1)), m.group(2).strip()))
            i += 1
            continue
        if st == "---":
            blocks.append(("hr",))
            i += 1
            continue
        if st.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            blocks.append(("table", rows))
            continue
        if st.startswith(">"):
            q = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(("quote", " ".join(q)))
            continue
        m = item_re.match(line)
        if m:
            items = []
            while i < len(lines):
                m = item_re.match(lines[i])
                if m:
                    items.append([len(m.group(1)) // 2, m.group(2), m.group(3)])
                    i += 1
                    continue
                nxt = lines[i]
                if nxt.strip() and not nxt.strip().startswith("```") and (nxt.startswith("  ") or nxt.startswith("\t")) \
                        and not re.match(r"^\s*(#|\|)", nxt):
                    items[-1][2] += " " + nxt.strip()
                    i += 1
                    continue
                break
            blocks.append(("list", items))
            continue
        para = [st]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            ns = nxt.strip()
            if not ns or ns.startswith("```") or re.match(r"^#{1,4}\s", nxt) or ns.startswith("|") \
                    or ns.startswith(">") or item_re.match(nxt) or ns == "---":
                break
            para.append(ns)
            i += 1
        blocks.append(("p", " ".join(para)))
    return blocks


# ------------------------------------------------------------------------------- flowables

class Heading(Paragraph):
    """A paragraph that also becomes a TOC entry, a bookmark and a link target."""

    def __init__(self, text, style, level, key, toc=True):
        anchor = '<a name="%s"/>' % key
        super().__init__(anchor + inline(text), style)
        self.toc_level, self.toc_text, self.toc_key, self.in_toc = level, re.sub(r"[`*]", "", text), key, toc


class FigMark(Flowable):
    """Zero-size marker that records a figure in the list of figures."""

    def __init__(self, number, title, key):
        super().__init__()
        self.number, self.title, self.key = number, title, key
        self.width = self.height = 0

    def draw(self):
        pass


class Doc(BaseDocTemplate):
    def __init__(self, path, **kw):
        super().__init__(path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN + 4,
                         bottomMargin=MARGIN, title="TUTORIAL - illustrated edition",
                         author="Quotation Tool / the studio", **kw)
        frame = Frame(MARGIN, MARGIN, FRAME_W, PAGE_H - 2 * MARGIN - 4, id="f", leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate("cover", [frame], onPage=lambda c, d: None),
                               PageTemplate("body", [frame], onPage=self._decorate)])
        self.part = ""
        self.outline_level = -1

    def beforeDocument(self):
        # multiBuild runs the story more than once (for the page numbers in the contents); the
        # running header must start empty on every pass, not carry the last pass's final Part.
        self.part = ""
        self.outline_level = -1

    def _decorate(self, canv, doc):
        canv.saveState()
        canv.setFont("UI", 7.5)
        canv.setFillColor(MUTED)
        canv.drawString(MARGIN, PAGE_H - MARGIN + 6, self.part[:95])
        canv.drawRightString(PAGE_W - MARGIN, MARGIN - 14, "%d" % doc.page)
        canv.drawString(MARGIN, MARGIN - 14, "TUTORIAL.md - illustrated edition")
        canv.setStrokeColor(colors.HexColor("#E5E7EB"))
        canv.line(MARGIN, PAGE_H - MARGIN + 2, PAGE_W - MARGIN, PAGE_H - MARGIN + 2)
        canv.restoreState()

    def afterFlowable(self, fl):
        if isinstance(fl, Heading):
            if fl.toc_level == 0:
                self.part = fl.toc_text
            key = fl.toc_key                  # the paragraph's own <a name>: what links jump to
            self.canv.bookmarkPage("o-" + key)  # a separate name for the sidebar outline
            level = min(fl.toc_level, self.outline_level + 1)
            self.canv.addOutlineEntry(fl.toc_text, "o-" + key, level=level, closed=level >= 1)
            self.outline_level = level
            if fl.in_toc and fl.toc_level <= 1:
                self.notify("TOCEntry", (fl.toc_level, fl.toc_text, self.page, key))
        elif isinstance(fl, FigMark):
            key = "fig-%d" % fl.number
            self.canv.bookmarkPage(key)
            self.notify("FigEntry", (0, "Figure %d  %s" % (fl.number, fl.title), self.page, key))


class FigureList(TableOfContents):
    def notify(self, kind, stuff):
        if kind == "FigEntry":
            self.addEntry(*stuff)


def code_block(lines):
    rows = []
    for l in lines or [""]:
        # Every space kept, not just the leading ones: the tutorial's ASCII diagrams line up
        # in columns, and a paragraph would collapse each run of spaces to one.
        l = l.rstrip().replace("\t", "    ")
        txt = esc(l).replace(" ", "&nbsp;")
        rows.append([Paragraph(txt or "&nbsp;", ParagraphStyle("c", parent=STY["code"], wordWrap="CJK"))])
    t = Table(rows, colWidths=[FRAME_W - 4], splitByRow=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F8FA")),
                           ("LINEBEFORE", (0, 0), (0, -1), 2, colors.HexColor("#9DB8E0")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                           ("TOPPADDING", (0, 0), (-1, -1), 0.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.4),
                           ("TOPPADDING", (0, 0), (-1, 0), 5), ("BOTTOMPADDING", (0, -1), (-1, -1), 5)]))
    return [t, Spacer(1, 7)]


def md_table(rows):
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    lens = [max(min(len(r[c]), 60) for r in rows) + 4 for c in range(ncol)]
    widths = [FRAME_W * l / sum(lens) for l in lens]
    data = [[Paragraph(inline(c), STY["cellh"] if ri == 0 else STY["cell"]) for c in r] for ri, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2F8")),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D1D5DB")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return [t, Spacer(1, 8)]


def fit(drawing, max_w=FRAME_W, max_h=PAGE_H - 2 * MARGIN - 120):
    s = min(1.0, max_w / drawing.width, max_h / drawing.height)
    if s < 1.0:
        drawing.renderScale = s
        drawing.width *= s
        drawing.height *= s
    return drawing


class Figures:
    def __init__(self, figs):
        self.figs, self.n, self.used = figs, 0, set()

    def flow(self, fg):
        self.n += 1
        self.used.add(id(fg))
        d = fit(fg["make"]())
        return [CondPageBreak(min(d.height + 70, PAGE_H - 2 * MARGIN - 40)),
                KeepTogether([FigMark(self.n, fg["title"], fg["key"]),
                              Paragraph("Figure %d &nbsp;&middot;&nbsp; %s" % (self.n, esc(fg["title"])), STY["figtitle"]),
                              d, Paragraph(esc(fg["caption"]), STY["figcap"])])]

    def for_heading(self, text):
        num = text.split(" ", 1)[0]
        out = []
        for fg in self.figs:
            k = fg["key"]
            if id(fg) in self.used:
                continue
            if (k == num and re.fullmatch(r"[\d.]+", num)) or (k.startswith("H:") and text.startswith(k[2:])):
                out.append(fg)
        return out

    def for_paragraph(self, text):
        plain = re.sub(r"[*`]", "", text)
        return [fg for fg in self.figs if fg["key"].startswith("P:") and id(fg) not in self.used
                and plain.startswith(re.sub(r"[*`]", "", fg["key"][2:]))]

    def with_key(self, key):
        return [fg for fg in self.figs if fg["key"] == key]


# -------------------------------------------------------------------------------- the book

def build(out_path):
    register_fonts()
    build_styles()
    import tutorial_diagrams
    figs = tutorial_diagrams.all_figures()
    F = Figures(figs)
    md = (_REPO_ROOT / "TUTORIAL.md").read_text(encoding="utf-8")
    blocks = parse(md)
    for b in blocks:
        if b[0] == "h":
            ANCHORS.add(slug(b[2]))

    commit = subprocess.run(["git", "-C", str(_REPO_ROOT), "log", "-1", "--format=%h %ad", "--date=short"],
                            capture_output=True, text=True).stdout.strip()
    story = [Spacer(1, 150),
             Paragraph("TUTORIAL", STY["cover1"]),
             Spacer(1, 6),
             Paragraph("A Software Engineering Course, Taught Through This Repo", STY["cover2"]),
             Spacer(1, 18),
             Paragraph("Illustrated edition: the full text of TUTORIAL.md, an architecture atlas, and a "
                       "diagram for every concept - the system, the data, the request, the deploy, and "
                       "every case study.", STY["cover2"]),
             Spacer(1, 40),
             Paragraph("Quotation Tool &middot; the studio", STY["cover3"]),
             Paragraph("Built %s from TUTORIAL.md at commit %s" % (datetime.date.today().isoformat(), esc(commit or "?")), STY["cover3"]),
             Paragraph("Rebuild: python scripts/maintenance/build_tutorial_pdf.py", STY["cover3"]),
             NextPageTemplate("body"), PageBreak()]

    toc = TableOfContents()
    toc.levelStyles = [S("t0", fontName="UI-B", fontSize=10, leading=14, leftIndent=0, spaceBefore=5),
                       S("t1", fontSize=8.6, leading=11.4, leftIndent=14, spaceAfter=0)]
    story += [Paragraph("Contents", STY["tocTitle"]), toc, PageBreak()]
    lof = FigureList()
    lof.levelStyles = [S("f0", fontSize=8.2, leading=11, leftIndent=0, spaceAfter=0)]
    story += [Paragraph("List of figures", STY["tocTitle"]), lof, PageBreak()]

    # ---- the atlas
    story.append(Heading("Architecture atlas", STY["h2"], 0, "architecture-atlas"))
    story.append(Paragraph(
        "The system-wide pictures, before the course begins: where a request travels, what runs on the "
        "server, which file does what, how the data is shaped, how the front end redraws, how a price "
        "and an export are chosen, and how a change reaches the live server. Every one was checked "
        "against the code on the day it was drawn. The section diagrams that follow each explain one "
        "concept; these explain how the concepts fit together.", STY["body"]))
    for fg in F.with_key("LEGEND") + F.with_key("ATLAS"):
        story += F.flow(fg)

    # ---- the tutorial
    pending, skip_section, first = [], False, True
    for b in blocks:
        kind = b[0]
        if kind == "h":
            level, text = b[1], b[2]
            story += sum((F.flow(fg) for fg in pending), [])
            pending = []
            if level == 1:
                continue                                   # the title is the cover
            skip_section = level == 2 and text.lower() == "table of contents"
            if skip_section:
                continue
            key = slug(text)
            if level == 2:
                if not first:
                    story.append(PageBreak())
                first = False
                story.append(Heading(text, STY["h2"], 0, key))
            elif level == 3:
                story.append(CondPageBreak(60))
                story.append(Heading(text, STY["h3"], 1, key))
            else:
                story.append(CondPageBreak(40))
                story.append(Heading(text, STY["h4"], 2, key, toc=False))
            pending = F.for_heading(text)
            continue
        if skip_section:
            continue
        if kind == "p":
            story.append(Paragraph(inline(b[1]), STY["body"]))
            story += sum((F.flow(fg) for fg in pending + F.for_paragraph(b[1])), [])
            pending = []
        elif kind == "list":
            for level, marker, text in b[1]:
                bullet = "\u2022" if marker in "-*" else marker
                st = ParagraphStyle("li%d" % level, parent=STY["li"], leftIndent=14 + level * 14,
                                    bulletIndent=3 + level * 14, bulletFontName="UI")
                story.append(Paragraph(inline(text), st, bulletText=bullet))
            story.append(Spacer(1, 4))
        elif kind == "code":
            story += code_block(b[1])
        elif kind == "table":
            story += md_table(b[1])
        elif kind == "quote":
            q = Table([[Paragraph(inline(b[1]), STY["quote"])]], colWidths=[FRAME_W - 10])
            q.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, -1), 2.5, colors.HexColor("#C9A227")),
                                   ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFBEA")),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 9)]))
            story += [q, Spacer(1, 8)]
        elif kind == "hr":
            story.append(Spacer(1, 6))
    story += sum((F.flow(fg) for fg in pending), [])

    unplaced = [fg for fg in figs if id(fg) not in F.used]
    doc = Doc(str(out_path))
    doc.multiBuild(story)
    return F.n, unplaced, doc.page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(_REPO_ROOT / "TUTORIAL.pdf"))
    args = ap.parse_args()
    n, unplaced, pages = build(args.out)
    print("wrote %s: %d pages, %d figures" % (args.out, pages, n))
    for fg in unplaced:
        print("  NOT PLACED (no matching section): [%s] %s" % (fg["key"], fg["title"]))


if __name__ == "__main__":
    main()
