"""A small diagram engine for the illustrated TUTORIAL.pdf (see build_tutorial_pdf.py).

Why hand-rolled rather than Graphviz or Mermaid: neither is installed here, and installing
packages needs approval. reportlab already is, and its graphics module draws vector shapes
straight into the PDF, so every diagram stays sharp at any zoom and searchable as text.

Three kinds of picture, each laid out by the engine rather than by hand-placed coordinates:

  Flow      a flowchart on a grid. Nodes sit in (column, row) cells; rows grow to fit their
            tallest node; edges are routed orthogonally between node sides. Zones shade a
            block of cells to show a boundary (a trust zone, a machine, a layer).
  Seq       a sequence diagram: participants across the top, messages down the page.
  charts    bar and line charts for the handful of places the tutorial has real numbers.

Every diagram is a reportlab Drawing, which is itself a Flowable, so the builder places it in
the document like a paragraph.
"""
import math

from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, PolyLine, Group, Path, Ellipse
from reportlab.lib import colors
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth

# Fonts are registered by the builder (Segoe UI family and Consolas). Names used here:
F_REG, F_BOLD, F_ITAL, F_MONO = "UI", "UI-B", "UI-I", "Mono"

INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#6B7280")

# kind -> (fill, stroke). One palette across every diagram, so a colour means the same thing
# on page 10 and page 200: blue is code that runs, amber a decision, purple stored data,
# green a start/end or the good path, red the bad path, grey something outside our control.
KINDS = {
    "process":  ("#E8F0FB", "#3563A9"),
    "decision": ("#FFF3D6", "#B7791F"),
    "start":    ("#E3F4E8", "#2F7A4A"),
    "end":      ("#E3F4E8", "#2F7A4A"),
    "store":    ("#EFE9F8", "#6A4C9C"),
    "external": ("#EEF0F2", "#5B6470"),
    "actor":    ("#EEF0F2", "#5B6470"),
    "doc":      ("#F2F7E6", "#5F7A1A"),
    "bad":      ("#FCE8E8", "#B42318"),
    "good":     ("#E6F6EC", "#1E7A45"),
    "warn":     ("#FFF1E6", "#C4540A"),
    "note":     ("#FFFBEA", "#C9A227"),
    "code":     ("#F3F4F6", "#4B5563"),
    "table":    ("#FFFFFF", "#6A4C9C"),
    "plain":    ("#FFFFFF", "#9CA3AF"),
    "text":     (None, None),
}
ZONES = {
    "untrusted": ("#FDF2F2", "#E8A0A0"),
    "trusted":   ("#F0F9F3", "#8CC7A1"),
    "server":    ("#F1F6FD", "#9DB8E0"),
    "data":      ("#F7F3FC", "#BBA6DD"),
    "neutral":   ("#F7F7F8", "#C9CDD3"),
    "warn":      ("#FFF8F0", "#E9B98C"),
}
EDGE_COLORS = {"normal": "#4B5563", "bad": "#B42318", "good": "#1E7A45", "muted": "#9CA3AF",
               "data": "#6A4C9C", "blue": "#3563A9", "warn": "#C4540A"}


def _c(hexstr):
    return colors.HexColor(hexstr) if hexstr else None


def fit_lines(text, font, size, width, min_size=5.6):
    """(lines, size): wrap `text` into `width`, shrinking the font until no single word is
    wider than the box. Explicit newlines are kept as line breaks."""
    text = text or ""
    s = size
    while True:
        words = text.replace("\n", " ").split()
        widest = max((stringWidth(w, font, s) for w in words), default=0)
        if widest <= width or s <= min_size:
            lines = []
            for part in text.split("\n"):
                lines.extend(simpleSplit(part, font, s, width) or [""])
            return lines, s
        s -= 0.4


class Node:
    def __init__(self, nid, text, c, r, kind="process", span=1, sub=None, w=None, h=None,
                 fs=8.2, fields=None, mono=False, rspan=1):
        self.id, self.text, self.c, self.r, self.kind = nid, text, c, r, kind
        self.span, self.rspan, self.sub, self.w, self.h, self.fs = span, rspan, sub, w, h, fs
        self.fields, self.mono = fields, mono


class Flow:
    """A flowchart on a grid. Build it with node()/edge()/zone(), then call drawing()."""

    def __init__(self, cols=4, width=493, hgap=28, vgap=20, margin=8, min_row=30):
        self.cols, self.W, self.hgap, self.vgap, self.margin = cols, width, hgap, vgap, margin
        self.min_row = min_row
        self.nodes, self.edges, self.zones, self.labels = {}, [], [], []
        self.cellw = (width - 2 * margin) / cols

    # ---------------------------------------------------------------- building
    def node(self, nid, text, c, r, kind="process", **kw):
        self.nodes[nid] = Node(nid, text, c, r, kind, **kw)
        return self

    def edge(self, a, b, label=None, route=None, style="normal", dashed=False, t=None,
             both=False, off=16, pts=None, a_side=None, b_side=None):
        self.edges.append(dict(a=a, b=b, label=label, route=route, style=style, dashed=dashed,
                               t=t, both=both, off=off, pts=pts, a_side=a_side, b_side=b_side))
        return self

    def zone(self, label, c0, r0, c1, r1, kind="neutral"):
        self.zones.append((label, c0, r0, c1, r1, kind))
        return self

    def text(self, s, c, r, size=7, color=MUTED, anchor="middle", bold=False):
        self.labels.append((s, c, r, size, color, anchor, bold))
        return self

    # ---------------------------------------------------------------- measuring
    def _node_w(self, n):
        return n.w if n.w else self.cellw * n.span - self.hgap

    def _measure(self, n):
        """Height a node needs for its text, and the wrapped lines to draw."""
        w = self._node_w(n)
        if n.kind == "decision":
            inner = w * 0.60
        elif n.kind == "store":
            inner = w - 12
        else:
            inner = w - 12
        font = F_MONO if n.mono else F_BOLD
        if n.kind == "table":
            font = F_BOLD
        t_lines, t_size = fit_lines(n.text, font, n.fs, inner)
        s_lines, s_size = ([], n.fs - 1.4)
        if n.sub:
            s_lines, s_size = fit_lines(n.sub, F_MONO if n.mono else F_REG, n.fs - 1.2, inner)
        f_lines = []
        if n.fields:
            for f in n.fields:
                f_lines.extend(fit_lines(f, F_REG, n.fs - 1.4, inner)[0])
        text_h = len(t_lines) * t_size * 1.18 + len(s_lines) * s_size * 1.2 + len(f_lines) * (n.fs - 1.4) * 1.22
        if n.kind == "table":
            text_h += 8
        pad = 7 if n.kind not in ("decision",) else 0
        h = text_h + 2 * pad
        if n.kind == "decision":
            h = max(text_h * 1.9 + 10, 34)
        if n.kind == "store":
            h += 8
        if n.kind == "doc":
            h += 5
        h = max(h, 24 if n.kind != "text" else 10)
        if n.h:
            h = max(h, n.h)
        n._w, n._h = w, h
        n._t, n._ts, n._s, n._ss, n._f = t_lines, t_size, s_lines, s_size, f_lines
        n._tfont = font
        return h

    def _layout(self):
        rows = max((n.r + n.rspan for n in self.nodes.values()), default=1)
        rows = max(rows, max((z[4] + 1 for z in self.zones), default=0))
        need = [self.min_row] * int(math.ceil(rows))
        for n in self.nodes.values():
            h = self._measure(n) + self.vgap
            if n.rspan == 1:
                ri = int(n.r)
                need[ri] = max(need[ri], h)
        # rows whose top carries a zone label get a little extra headroom
        self.top_pad = [0] * len(need)
        for ri in {int(z[2]) for z in self.zones if z[0]}:
            need[ri] += 12
            self.top_pad[ri] = 12
        self.row_h = need
        self.row_y = []                 # top y of each row, measured from the top
        y = self.margin
        for h in need:
            self.row_y.append(y)
            y += h
        self.H = y + self.margin
        for n in self.nodes.values():
            if n.rspan > 1 and not n.h:     # a node spanning rows fills them
                ri = int(n.r)
                n._h = max(n._h, sum(need[ri:ri + n.rspan]) - self.vgap)
            x0 = self.margin + n.c * self.cellw + (self.cellw * n.span - n._w) / 2
            ri = int(n.r)
            frac = n.r - ri
            top = self.row_y[ri] + frac * need[ri]
            pad = self.top_pad[ri] if not frac else 0
            if n.rspan > 1:
                span_h = sum(need[ri:ri + n.rspan])
                cy_top = top + pad + (span_h - pad) / 2
            else:
                cy_top = top + pad + (need[ri] - pad) / 2
            if frac:
                cy_top = self.row_y[ri] + need[ri] * (0.5 + frac)
            n.cx = x0 + n._w / 2
            n.cy = self.H - cy_top          # reportlab y grows upwards
            n.x0, n.x1 = n.cx - n._w / 2, n.cx + n._w / 2
            n.y0, n.y1 = n.cy - n._h / 2, n.cy + n._h / 2

    def _grid_xy(self, c, r):
        ri = int(r)
        top = self.row_y[min(ri, len(self.row_y) - 1)] + (r - ri) * self.row_h[min(ri, len(self.row_h) - 1)]
        return self.margin + c * self.cellw, self.H - top

    # ---------------------------------------------------------------- drawing
    def drawing(self):
        self._layout()
        d = Drawing(self.W, self.H)
        for z in self.zones:
            self._draw_zone(d, *z)
        for e in self.edges:
            self._draw_edge(d, e)
        for n in self.nodes.values():
            self._draw_node(d, n)
        for e in self.edges:
            self._draw_edge_label(d, e)
        for s, c, r, size, color, anchor, bold in self.labels:
            x, y = self._grid_xy(c, r)
            d.add(String(x, y - size, s, fontName=F_BOLD if bold else F_ITAL, fontSize=size,
                         fillColor=color, textAnchor=anchor))
        return d

    def _draw_zone(self, d, label, c0, r0, c1, r1, kind):
        fill, stroke = ZONES[kind]
        x0 = self.margin + c0 * self.cellw + 2
        x1 = self.margin + (c1 + 1) * self.cellw - 2
        top = self.row_y[int(r0)] + (r0 - int(r0)) * self.row_h[int(r0)] + 1
        bot_row = int(r1)
        bottom = self.row_y[bot_row] + self.row_h[bot_row] * (1 + (r1 - bot_row)) - 3
        y1, y0 = self.H - top, self.H - bottom
        d.add(Rect(x0, y0, x1 - x0, y1 - y0, rx=6, ry=6, fillColor=_c(fill), strokeColor=_c(stroke),
                   strokeWidth=0.8, strokeDashArray=[3, 2]))
        if label:
            size = 6.8
            while size > 5 and stringWidth(label, F_BOLD, size) > (x1 - x0) - 12:
                size -= 0.2
            d.add(String(x0 + 6, y1 - 10, label, fontName=F_BOLD, fontSize=size,
                         fillColor=_darker(stroke, 0.62)))

    def _draw_node(self, d, n):
        fill, stroke = KINDS[n.kind]
        f, s = _c(fill), _c(stroke)
        x0, y0, w, h = n.x0, n.y0, n._w, n._h
        k = n.kind
        if k == "decision":
            d.add(Polygon([n.cx, n.y1, n.x1, n.cy, n.cx, n.y0, n.x0, n.cy], fillColor=f,
                          strokeColor=s, strokeWidth=1))
        elif k in ("start", "end"):
            d.add(Rect(x0, y0, w, h, rx=h / 2, ry=h / 2, fillColor=f, strokeColor=s, strokeWidth=1.1))
        elif k == "store":
            eh = 7
            d.add(Rect(x0, y0 + eh / 2, w, h - eh, fillColor=f, strokeColor=None))
            d.add(Ellipse(n.cx, y0 + eh / 2, w / 2, eh / 2, fillColor=f, strokeColor=s, strokeWidth=1))
            d.add(Rect(x0, y0 + eh / 2, w, h - eh, fillColor=f, strokeColor=None))
            d.add(Line(x0, y0 + eh / 2, x0, y0 + h - eh / 2, strokeColor=s, strokeWidth=1))
            d.add(Line(x0 + w, y0 + eh / 2, x0 + w, y0 + h - eh / 2, strokeColor=s, strokeWidth=1))
            d.add(Ellipse(n.cx, y0 + h - eh / 2, w / 2, eh / 2, fillColor=f, strokeColor=s, strokeWidth=1))
        elif k == "doc":
            p = Path(fillColor=f, strokeColor=s, strokeWidth=1)
            wave = 5
            p.moveTo(x0, y0 + wave)
            p.lineTo(x0, y0 + h)
            p.lineTo(x0 + w, y0 + h)
            p.lineTo(x0 + w, y0 + wave)
            p.curveTo(x0 + w * 0.75, y0 - wave, x0 + w * 0.5, y0 + wave * 2, x0 + w * 0.25, y0 + wave)
            p.curveTo(x0 + w * 0.12, y0, x0 + w * 0.05, y0, x0, y0 + wave)
            p.closePath()
            d.add(p)
        elif k == "note":
            d.add(Rect(x0, y0, w, h, fillColor=f, strokeColor=s, strokeWidth=0.8, strokeDashArray=[2, 2]))
        elif k == "actor":
            d.add(Rect(x0, y0, w, h, rx=3, ry=3, fillColor=f, strokeColor=s, strokeWidth=1.6))
        elif k == "table":
            d.add(Rect(x0, y0, w, h, rx=2, ry=2, fillColor=f, strokeColor=s, strokeWidth=1))
            band = n._ts * 1.18 * len(n._t) + 6
            d.add(Rect(x0, y0 + h - band, w, band, rx=2, ry=2, fillColor=_c("#EFE9F8"), strokeColor=s, strokeWidth=1))
        elif k == "text":
            pass
        else:
            d.add(Rect(x0, y0, w, h, rx=4, ry=4, fillColor=f, strokeColor=s, strokeWidth=1))
        self._draw_node_text(d, n)

    def _draw_node_text(self, d, n):
        lh_t, lh_s, lh_f = n._ts * 1.18, n._ss * 1.2, (n.fs - 1.4) * 1.22
        if n.kind == "table":
            y = n.y1 - 4 - n._ts
            for line in n._t:
                d.add(String(n.cx, y, line, fontName=F_BOLD, fontSize=n._ts, fillColor=INK, textAnchor="middle"))
                y -= lh_t
            y -= 6
            for line in n._f:
                d.add(String(n.x0 + 6, y + 1.5, line, fontName=F_REG, fontSize=n.fs - 1.4, fillColor=INK))
                y -= lh_f
            return
        # A text block centred on the node: a cursor walks down from the block's top, and each
        # line's baseline sits one ascent below the cursor.
        rows = ([(l, n._tfont, n._ts, lh_t, INK) for l in n._t]
                + [(l, F_MONO if n.mono else F_REG, n._ss, lh_s,
                    _darker(KINDS[n.kind][1] or "#374151", 0.55)) for l in n._s]
                + [(l, F_REG, n.fs - 1.4, lh_f, INK) for l in n._f])
        total = sum(r[3] for r in rows)
        cursor = n.cy + total / 2
        if n.kind == "store":
            cursor -= 2
        if n.kind == "doc":
            cursor += 2
        for text, font, size, lh, col in rows:
            d.add(String(n.cx, cursor - size * 0.93, text, fontName=font, fontSize=size,
                         fillColor=col, textAnchor="middle"))
            cursor -= lh

    # ---------------------------------------------------------------- edges
    def _port(self, n, side):
        q, v = n._w / 4, n._h / 4
        return {"top": (n.cx, n.y1), "bottom": (n.cx, n.y0), "left": (n.x0, n.cy),
                "right": (n.x1, n.cy),
                "top-l": (n.cx - q, n.y1), "top-r": (n.cx + q, n.y1),
                "bottom-l": (n.cx - q, n.y0), "bottom-r": (n.cx + q, n.y0),
                "left-t": (n.x0, n.cy + v), "left-b": (n.x0, n.cy - v),
                "right-t": (n.x1, n.cy + v), "right-b": (n.x1, n.cy - v)}[side]

    @staticmethod
    def _vertical(side):
        return side.startswith("top") or side.startswith("bottom")

    def _route(self, e):
        a, b = self.nodes[e["a"]], self.nodes[e["b"]]
        route = e["route"]
        dx, dy = b.cx - a.cx, b.cy - a.cy
        if (e["a_side"] or e["b_side"]) and not e["pts"]:
            # Ports named explicitly: leave and enter along each port's own direction.
            sa = e["a_side"] or ("bottom" if dy < 0 else "top" if dy > 0 else ("right" if dx > 0 else "left"))
            sb = e["b_side"] or ("top" if dy < 0 else "bottom" if dy > 0 else ("left" if dx > 0 else "right"))
            p0, p1 = self._port(a, sa), self._port(b, sb)
            va, vb = self._vertical(sa), self._vertical(sb)
            if va and vb:
                if abs(p0[0] - p1[0]) < 1:
                    return [p0, p1]
                my = (p0[1] + p1[1]) / 2
                return [p0, (p0[0], my), (p1[0], my), p1]
            if not va and not vb:
                if abs(p0[1] - p1[1]) < 1:
                    return [p0, p1]
                mx = (p0[0] + p1[0]) / 2
                return [p0, (mx, p0[1]), (mx, p1[1]), p1]
            if va:
                return [p0, (p0[0], p1[1]), p1]
            return [p0, (p1[0], p0[1]), p1]
        if e["pts"]:
            pts = [self._port(a, e["a_side"] or "bottom")]
            for (c, r) in e["pts"]:
                x, y = self._grid_xy(c, r)
                pts.append((x, y))
            pts.append(self._port(b, e["b_side"] or "top"))
            return pts
        if route is None:
            if abs(dx) < 3:
                route = "v"
            elif abs(dy) < 3:
                route = "h"
            elif dy > 0:
                route = "back"
            elif a.kind == "decision":
                route = "hv"
            else:
                route = "vhv"
        if route == "v":
            if dy < 0:
                return [(a.cx, a.y0), (a.cx, b.y1)] if abs(dx) < 3 else [(a.cx, a.y0), (b.cx, b.y1)]
            return [(a.cx, a.y1), (a.cx, b.y0)]
        if route == "h" and abs(dy) >= 3:
            route = "hvh"                   # boxes at different heights: step, never slant
        if route == "h":
            if dx > 0:
                return [(a.x1, a.cy), (b.x0, a.cy)]
            return [(a.x0, a.cy), (b.x1, a.cy)]
        if route == "d":                    # a deliberate diagonal, bottom to top
            return [(a.cx, a.y0), (b.cx, b.y1)] if dy < 0 else [(a.cx, a.y1), (b.cx, b.y0)]
        if route == "hv":
            sx = a.x1 if dx > 0 else a.x0
            end_y = b.y1 if dy < 0 else b.y0
            return [(sx, a.cy), (b.cx, a.cy), (b.cx, end_y)]
        if route == "vh":
            sy = a.y0 if dy < 0 else a.y1
            ex = b.x0 if dx > 0 else b.x1
            return [(a.cx, sy), (a.cx, b.cy), (ex, b.cy)]
        if route == "vhv":
            sy = a.y0 if dy < 0 else a.y1
            ey = b.y1 if dy < 0 else b.y0
            my = (sy + ey) / 2
            return [(a.cx, sy), (a.cx, my), (b.cx, my), (b.cx, ey)]
        if route == "hvh":
            sx = a.x1 if dx > 0 else a.x0
            ex = b.x0 if dx > 0 else b.x1
            mx = (sx + ex) / 2
            return [(sx, a.cy), (mx, a.cy), (mx, b.cy), (ex, b.cy)]
        if route in ("back", "back-right"):
            x = max(a.x1, b.x1) + e["off"]
            return [(a.x1, a.cy), (x, a.cy), (x, b.cy), (b.x1, b.cy)]
        if route == "back-left":
            x = min(a.x0, b.x0) - e["off"]
            return [(a.x0, a.cy), (x, a.cy), (x, b.cy), (b.x0, b.cy)]
        if route == "under":            # leave a's bottom, run below, enter b's bottom
            y = min(a.y0, b.y0) - e["off"]
            return [(a.cx, a.y0), (a.cx, y), (b.cx, y), (b.cx, b.y0)]
        if route == "over":
            y = max(a.y1, b.y1) + e["off"]
            return [(a.cx, a.y1), (a.cx, y), (b.cx, y), (b.cx, b.y1)]
        raise ValueError(route)

    def _draw_edge(self, d, e):
        pts = self._route(e)
        e["_pts"] = pts
        col = _c(EDGE_COLORS[e["style"]])
        flat = [v for p in pts for v in p]
        d.add(PolyLine(flat, strokeColor=col, strokeWidth=1.05,
                       strokeDashArray=[3, 2] if e["dashed"] else None))
        _arrow(d, pts[-2], pts[-1], col)
        if e["both"]:
            _arrow(d, pts[1], pts[0], col)

    def _draw_edge_label(self, d, e):
        if not e["label"]:
            return
        pts = e["_pts"]
        a = self.nodes[e["a"]]
        t = e["t"] if e["t"] is not None else (0.28 if a.kind == "decision" else 0.5)
        x, y = _point_along(pts, t)
        _label(d, x, y, e["label"], _c(EDGE_COLORS[e["style"]]))


def _darker(hexstr, factor=0.75):
    c = colors.HexColor(hexstr)
    return colors.Color(c.red * factor, c.green * factor, c.blue * factor)


def _arrow(d, p0, p1, col, size=5.2):
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    x, y = p1
    left = (x - size * math.cos(ang - 0.42), y - size * math.sin(ang - 0.42))
    right = (x - size * math.cos(ang + 0.42), y - size * math.sin(ang + 0.42))
    d.add(Polygon([x, y, left[0], left[1], right[0], right[1]], fillColor=col, strokeColor=col, strokeWidth=0.5))


def _point_along(pts, t):
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs]
    total = sum(lengths) or 1
    target = total * t
    for (a, b), L in zip(segs, lengths):
        if target <= L or L == lengths[-1]:
            f = target / L if L else 0
            f = min(max(f, 0), 1)
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        target -= L
    return pts[-1]


def _label(d, x, y, text, col, size=6.6):
    lines = text.split("\n")
    w = max(stringWidth(l, F_ITAL, size) for l in lines) + 5
    h = len(lines) * size * 1.15 + 2.5
    d.add(Rect(x - w / 2, y - h / 2, w, h, rx=2, ry=2, fillColor=colors.white,
               strokeColor=colors.HexColor("#E5E7EB"), strokeWidth=0.4))
    yy = y + h / 2 - size - 0.6
    for l in lines:
        d.add(String(x, yy, l, fontName=F_ITAL, fontSize=size, fillColor=col, textAnchor="middle"))
        yy -= size * 1.15


# ------------------------------------------------------------------ sequence diagrams

class Seq:
    """A sequence diagram. participants: [(id, label, kind)], then message()/note()/divider()."""

    def __init__(self, participants, width=493, margin=8):
        self.parts = participants
        self.W, self.margin = width, margin
        self.items = []

    def msg(self, a, b, label, reply=False, style="normal"):
        self.items.append(("msg", a, b, label, reply, style))
        return self

    def self_(self, a, label, style="normal"):
        self.items.append(("self", a, label, style))
        return self

    def note(self, a, b, text, kind="note"):
        self.items.append(("note", a, b, text, kind))
        return self

    def divider(self, text):
        self.items.append(("div", text))
        return self

    def drawing(self):
        n = len(self.parts)
        colw = (self.W - 2 * self.margin) / n
        xs = {pid: self.margin + colw * (i + 0.5) for i, (pid, _l, _k) in enumerate(self.parts)}
        head_h = 30
        # measure each item's height
        heights = []
        for it in self.items:
            if it[0] == "msg":
                _k, a, b, label, _r, _s = it
                span = max(abs(xs[b] - xs[a]) - 10, 60)
                lines, _ = fit_lines(label, F_REG, 7, span)
                heights.append(len(lines) * 8.4 + 12)
            elif it[0] == "self":
                lines, _ = fit_lines(it[2], F_REG, 7, colw * 0.9)
                heights.append(len(lines) * 8.4 + 24)
            elif it[0] == "note":
                _k, a, b, text, _kind = it
                width = abs(xs[b] - xs[a]) + colw * 0.8
                lines, _ = fit_lines(text, F_REG, 7, width - 10)
                heights.append(len(lines) * 8.6 + 14)
            else:
                heights.append(20)
        H = head_h + sum(heights) + 14 + self.margin
        d = Drawing(self.W, H)
        top = H - self.margin
        for pid, label, kind in self.parts:
            fill, stroke = KINDS.get(kind or "process")
            bw = min(colw - 8, 120)
            lines, size = fit_lines(label, F_BOLD, 7.8, bw - 8)
            bh = max(22, len(lines) * size * 1.2 + 8)
            d.add(Rect(xs[pid] - bw / 2, top - bh, bw, bh, rx=4, ry=4, fillColor=_c(fill),
                       strokeColor=_c(stroke), strokeWidth=1.1))
            yy = top - bh / 2 + (len(lines) * size * 1.2) / 2 - size
            for l in lines:
                d.add(String(xs[pid], yy, l, fontName=F_BOLD, fontSize=size, fillColor=INK, textAnchor="middle"))
                yy -= size * 1.2
            d.add(Line(xs[pid], top - bh, xs[pid], self.margin + 4, strokeColor=_c("#C0C6CE"),
                       strokeWidth=0.8, strokeDashArray=[2, 2]))
        y = top - head_h - 4
        for it, h in zip(self.items, heights):
            if it[0] == "msg":
                _k, a, b, label, reply, style = it
                col = _c(EDGE_COLORS[style])
                x0, x1 = xs[a], xs[b]
                span = max(abs(x1 - x0) - 10, 60)
                lines, size = fit_lines(label, F_REG, 7, span)
                ly = y - 7
                for l in lines:
                    d.add(String((x0 + x1) / 2, ly, l, fontName=F_REG, fontSize=size, fillColor=INK, textAnchor="middle"))
                    ly -= 8.4
                ay = y - h + 4
                d.add(Line(x0, ay, x1, ay, strokeColor=col, strokeWidth=1,
                           strokeDashArray=[3, 2] if reply else None))
                _arrow(d, (x0, ay), (x1, ay), col)
            elif it[0] == "self":
                _k, a, label, style = it
                col = _c(EDGE_COLORS[style])
                x = xs[a]
                room_right = self.W - self.margin - x - 8
                left = room_right < 110          # last column: write the label to the left
                width = colw * 0.9 if left else min(colw * 0.9, room_right)
                lines, size = fit_lines(label, F_REG, 7, width)
                ly = y - 7
                for l in lines:
                    if left:
                        d.add(String(x - 5, ly, l, fontName=F_REG, fontSize=size, fillColor=INK, textAnchor="end"))
                    else:
                        d.add(String(x + 6, ly, l, fontName=F_REG, fontSize=size, fillColor=INK))
                    ly -= 8.4
                y1 = ly + 2
                y2 = y1 - 8
                d.add(PolyLine([x, y1, x + 18, y1, x + 18, y2, x + 1, y2], strokeColor=col, strokeWidth=1))
                _arrow(d, (x + 18, y2), (x + 1, y2), col)
            elif it[0] == "note":
                _k, a, b, text, kind = it
                fill, stroke = KINDS[kind]
                xa, xb = min(xs[a], xs[b]) - colw * 0.4, max(xs[a], xs[b]) + colw * 0.4
                xa, xb = max(xa, self.margin), min(xb, self.W - self.margin)
                lines, size = fit_lines(text, F_REG, 7, xb - xa - 10)
                bh = h - 6
                d.add(Rect(xa, y - bh - 2, xb - xa, bh, fillColor=_c(fill), strokeColor=_c(stroke),
                           strokeWidth=0.7, strokeDashArray=[2, 2] if kind == "note" else None))
                ly = y - 10
                for l in lines:
                    d.add(String((xa + xb) / 2, ly, l, fontName=F_REG, fontSize=size, fillColor=INK, textAnchor="middle"))
                    ly -= 8.6
            else:
                d.add(Line(self.margin, y - 10, self.W - self.margin, y - 10, strokeColor=_c("#D1D5DB"), strokeWidth=0.6))
                tw = stringWidth(it[1], F_BOLD, 6.8) + 10
                d.add(Rect(self.W / 2 - tw / 2, y - 15, tw, 10, fillColor=colors.white, strokeColor=None))
                d.add(String(self.W / 2, y - 12.5, it[1], fontName=F_BOLD, fontSize=6.8, fillColor=MUTED, textAnchor="middle"))
            y -= h
        return d


# ------------------------------------------------------------------ charts

def bar_chart(categories, series, names, width=493, height=190, unit="", colors_=None, value_fmt="{:g}"):
    """Grouped vertical bars with value labels. series: list of value lists (one per name)."""
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.legends import Legend
    d = Drawing(width, height)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 46, 30, width - 170, height - 48
    bc.data = series
    bc.categoryAxis.categoryNames = categories
    bc.categoryAxis.labels.fontName, bc.categoryAxis.labels.fontSize = F_REG, 7
    bc.valueAxis.labels.fontName, bc.valueAxis.labels.fontSize = F_REG, 7
    bc.valueAxis.valueMin = 0
    top = max(max(s) for s in series)
    bc.valueAxis.valueMax = top * 1.18
    bc.valueAxis.valueStep = _nice_step(top * 1.18)
    bc.valueAxis.gridStrokeColor = colors.HexColor("#E5E7EB")
    bc.valueAxis.visibleGrid = 1
    palette = colors_ or ["#3563A9", "#1E7A45", "#C4540A", "#6A4C9C"]
    for i in range(len(series)):
        bc.bars[i].fillColor = colors.HexColor(palette[i % len(palette)])
        bc.bars[i].strokeColor = None
    bc.barLabelFormat = lambda v: value_fmt.format(v) + unit
    bc.barLabels.fontName, bc.barLabels.fontSize = F_BOLD, 6.8
    bc.barLabels.nudge = 7
    bc.groupSpacing, bc.barSpacing = 12, 2
    d.add(bc)
    lg = Legend()
    lg.x, lg.y = width - 116, height - 30
    lg.fontName, lg.fontSize = F_REG, 7.2
    lg.alignment = "right"
    lg.colorNamePairs = [(colors.HexColor(palette[i % len(palette)]), names[i]) for i in range(len(names))]
    d.add(lg)
    return d


def _nice_step(top):
    raw = top / 5
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def complexity_chart(width=493, height=200):
    """Growth of O(1), O(log n), O(n), O(n log n), O(n^2) for n = 1..20."""
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.charts.legends import Legend
    d = Drawing(width, height)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 44, 28, width - 170, height - 44
    ns = list(range(1, 21))
    curves = [
        ("O(1)", [1 for n in ns], "#1E7A45"),
        ("O(log n)", [math.log2(n) + 1 for n in ns], "#3563A9"),
        ("O(n)", [n for n in ns], "#6A4C9C"),
        ("O(n log n)", [n * math.log2(n) if n > 1 else 1 for n in ns], "#C4540A"),
        ("O(n²)", [n * n for n in ns], "#B42318"),
    ]
    # A curve stops where it leaves the chart - clamping it at the top edge would draw a
    # plateau, which is exactly the wrong lesson about O(n^2).
    lp.data = [[(n, v) for n, v in zip(ns, vals) if v <= 120] for _n, vals, _c2 in curves]
    for i, (_n, _v, col) in enumerate(curves):
        lp.lines[i].strokeColor = colors.HexColor(col)
        lp.lines[i].strokeWidth = 1.6
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax, lp.xValueAxis.valueStep = 1, 20, 1
    lp.yValueAxis.valueMin, lp.yValueAxis.valueMax, lp.yValueAxis.valueStep = 0, 120, 20
    for ax in (lp.xValueAxis, lp.yValueAxis):
        ax.labels.fontName, ax.labels.fontSize = F_REG, 7
    lp.yValueAxis.visibleGrid = 1
    lp.yValueAxis.gridStrokeColor = colors.HexColor("#E5E7EB")
    d.add(lp)
    d.add(String(44 + (width - 170) / 2, 8, "input size n", fontName=F_ITAL, fontSize=7, fillColor=MUTED, textAnchor="middle"))
    d.add(String(10, height - 12, "steps", fontName=F_ITAL, fontSize=7, fillColor=MUTED))
    lg = Legend()
    lg.x, lg.y = width - 112, height - 30
    lg.fontName, lg.fontSize = F_REG, 7.6
    lg.columnMaximum = 5
    lg.colorNamePairs = [(colors.HexColor(c2), n) for n, _v, c2 in curves]
    d.add(lg)
    return d
