"""Lay out the PDF: a cover page with an overview, then one page per trip."""
import io
import math
import os
from datetime import date, datetime

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from . import geo, maps
from .images import jpeg_bytes, open_upright

PAGE_W, PAGE_H = A4
M = 40  # page margin
INK = HexColor("#1f2328")
MUTED = HexColor("#6a737d")
ACCENT = HexColor("#c8283c")
RULE = HexColor("#d0d7de")

_FONT_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]


def _fonts():
    """Use a Unicode TrueType font when one is installed, so names like "Kraków" print correctly."""
    for reg, bold in _FONT_CANDIDATES:
        if os.path.exists(reg) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("PS", reg))
                pdfmetrics.registerFont(TTFont("PS-Bold", bold))
                return "PS", "PS-Bold"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold"


def fmt_day(d):
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b %Y')}"


def fmt_range(a, b):
    if a == b:
        return f"{a.day} {a.strftime('%B %Y')}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {b.strftime('%B %Y')}"
    if a.year == b.year:
        return f"{a.day} {a.strftime('%b')} – {b.day} {b.strftime('%b %Y')}"
    return f"{a.day} {a.strftime('%b %Y')} – {b.day} {b.strftime('%b %Y')}"


def _d(s):
    return datetime.fromisoformat(s)


def _nights(n):
    return "day trip" if n == 0 else f"{n} night" + ("" if n == 1 else "s")


class Report:
    def __init__(self, project, out_path):
        self.p = project
        self.trips = [t for t in project["trips"] if t.get("include", True)]
        self.c = canvas.Canvas(out_path, pagesize=A4)
        self.c.setTitle(project.get("title") or "Travel together")
        self.c.setAuthor(" & ".join(project.get("people") or []))
        self.font, self.bold = _fonts()
        self.page = 0

    # --- small drawing helpers -------------------------------------------------------------------
    def text(self, x, y, s, size=10, bold=False, color=INK, align="left"):
        c = self.c
        c.setFillColor(color)
        c.setFont(self.bold if bold else self.font, size)
        {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}[align](x, y, s)

    def fit(self, s, size, width, bold=False):
        """Shorten s with an ellipsis until it fits in width."""
        f = self.bold if bold else self.font
        if pdfmetrics.stringWidth(s, f, size) <= width:
            return s
        while s and pdfmetrics.stringWidth(s + "…", f, size) > width:
            s = s[:-1]
        return s + "…"

    def wrap(self, s, size, width, bold=False):
        f = self.bold if bold else self.font
        lines = []
        for para in s.splitlines() or [""]:
            line = ""
            for word in para.split():
                trial = (line + " " + word).strip()
                if pdfmetrics.stringWidth(trial, f, size) <= width:
                    line = trial
                else:
                    if line:
                        lines.append(line)
                    line = word
            lines.append(line)
        return lines

    def image(self, img, x, y, w, h, contain=True, quality=78):
        """Draw a PIL image inside the box (x, y, w, h), keeping its shape."""
        iw, ih = img.size
        s = min(w / iw, h / ih) if contain else max(w / iw, h / ih)
        dw, dh = iw * s, ih * s
        # Passing JPEG bytes lets reportlab embed them as-is, which keeps the PDF small.
        data = ImageReader(io.BytesIO(jpeg_bytes(img, quality)))
        self.c.drawImage(data, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)
        return x + (w - dw) / 2, y + (h - dh) / 2, dw, dh

    def footer(self):
        self.page += 1
        self.c.setStrokeColor(RULE)
        self.c.line(M, 30, PAGE_W - M, 30)
        self.text(M, 18, "Dates and places are taken from the metadata (EXIF) stored in the original photo files.",
                  7, color=MUTED)
        self.text(PAGE_W - M, 18, f"Page {self.page}", 7, color=MUTED, align="right")
        self.c.showPage()

    def map_image(self, points, route=None, labels=None, box=(515, 260), max_zoom=12):
        px = (int(box[0] * 2.3), int(box[1] * 2.3))
        return maps.render(points, size=px, route=route, labels=labels, max_zoom=max_zoom)

    # --- pages -----------------------------------------------------------------------------------
    def build(self):
        self.cover()
        for n, t in enumerate(self.trips, 1):
            self.trip_page(n, t)
        self.c.save()

    def cover(self):
        p = self.p
        people = p.get("people") or []
        title = p.get("title") or ("Travelling together: " + " & ".join(people) if people else "Travelling together")
        y = PAGE_H - M - 20
        self.text(M, y, title, 22, bold=True)
        y -= 22
        starts = [_d(t["start"]).date() for t in self.trips]
        ends = [_d(t["end"]).date() for t in self.trips]
        if self.trips:
            nights = sum(t["nights"] for t in self.trips)
            sub = (f"{len(self.trips)} trips, {nights} nights away together, "
                   f"{fmt_range(min(starts), max(ends))}")
        else:
            sub = "No trips selected"
        self.text(M, y, sub, 11, color=MUTED)
        y -= 16

        if self.trips:
            pts, labels = [], []
            for n, t in enumerate(self.trips, 1):
                main = max(t["stops"], key=lambda s: s["count"])
                pts.append((main["lat"], main["lon"]))
                labels.append(n)
            mh = 280
            img = self.map_image(pts, labels=labels, box=(PAGE_W - 2 * M, mh), max_zoom=10)
            self.image(img, M, y - mh, PAGE_W - 2 * M, mh, quality=90)
            y -= mh + 26

        # Summary table
        cols = [(M, "No."), (M + 30, "Destination"), (M + 290, "Dates"), (M + 430, "Nights"), (M + 478, "Photos")]
        row_h = 17

        def header(y):
            for x, h in cols:
                self.text(x, y, h, 8, bold=True, color=MUTED)
            self.c.setStrokeColor(RULE)
            self.c.line(M, y - 5, PAGE_W - M, y - 5)
            return y - row_h

        y = header(y)
        for n, t in enumerate(self.trips, 1):
            if y < 60:
                self.footer()
                y = header(PAGE_H - M - 10)
            a, b = _d(t["start"]).date(), _d(t["end"]).date()
            self.text(cols[0][0], y, str(n), 9)
            self.text(cols[1][0], y, self.fit(t.get("title", ""), 9, 250), 9)
            self.text(cols[2][0], y, fmt_range(a, b), 9)
            self.text(cols[3][0], y, str(t["nights"]), 9)
            self.text(cols[4][0], y, str(t["photo_count"]), 9)
            y -= row_h

        y -= 10
        note = p.get("cover_note") or (
            "Each following page shows one trip: a map of the places we visited, the dates, and a few of the "
            "photos we took together. Trips were found from the date, time and GPS location recorded "
            "automatically by our phones and cameras in each photo.")
        for line in self.wrap(note, 9, PAGE_W - 2 * M):
            if y < 50:
                break
            self.text(M, y, line, 9, color=MUTED)
            y -= 13
        self.footer()

    def trip_page(self, n, t):
        a, b = _d(t["start"]), _d(t["end"])
        y = PAGE_H - M - 6
        self.text(M, y, f"TRIP {n}", 9, bold=True, color=ACCENT)
        y -= 22
        self.text(M, y, self.fit(t.get("title", ""), 18, PAGE_W - 2 * M, bold=True), 18, bold=True)
        y -= 17
        self.text(M, y, f"{fmt_day(a.date())} – {fmt_day(b.date())}  ·  {_nights(t['nights'])}  ·  "
                        f"{t['photo_count']} photos taken", 10, color=MUTED)
        y -= 12

        # Map, with stops numbered in the order first visited
        stops = t["stops"]
        order = sorted(range(len(stops)), key=lambda i: stops[i]["first"])
        num = {i: k + 1 for k, i in enumerate(order)}
        mh = 230
        img = self.map_image([(s["lat"], s["lon"]) for s in stops], route=t.get("route"),
                             labels=[num[i] for i in range(len(stops))], box=(PAGE_W - 2 * M, mh))
        self.image(img, M, y - mh, PAGE_W - 2 * M, mh, quality=90)
        y -= mh + 16

        # Itinerary: the numbered stops, biggest few if there are many
        shown = [i for i in order if stops[i]["count"] >= 2 or len(stops) <= 6][:8]
        col_w = (PAGE_W - 2 * M) / 2
        for k, i in enumerate(shown):
            s = stops[i]
            cx = M + (k % 2) * col_w
            cy = y - (k // 2) * 14
            label = geo.place_label(s.get("place") or {}) or f"{s['lat']:.3f}, {s['lon']:.3f}"
            when = fmt_range(_d(s["first"]).date(), _d(s["last"]).date())
            extra = f" · {_nights(s['nights'])}" if s.get("nights") else ""
            self.text(cx, cy, str(num[i]), 8, bold=True, color=ACCENT)
            self.text(cx + 14, cy, self.fit(f"{label}  —  {when}{extra}", 8.5, col_w - 20), 8.5)
        y -= math.ceil(len(shown) / 2) * 14 + 4

        if t.get("notes"):
            for line in self.wrap(t["notes"], 9.5, PAGE_W - 2 * M)[:4]:
                self.text(M, y, line, 9.5)
                y -= 13
            y -= 4

        # Photos
        by_path = {p["path"]: p for p in t["photos"]}
        sel = [by_path[s] for s in t.get("selected", []) if s in by_path]
        if sel:
            self.photo_grid(sel, stops, M, 44, PAGE_W - 2 * M, y - 44)
        self.footer()

    def photo_grid(self, photos, stops, x, y, w, h):
        n = len(photos)
        cols = 1 if n == 1 else 2 if n <= 4 else 3
        rows = math.ceil(n / cols)
        gap, cap = 10, 22
        cw = (w - gap * (cols - 1)) / cols
        ch = (h - gap * (rows - 1)) / rows
        for k, p in enumerate(photos):
            r, cidx = divmod(k, cols)
            in_row = min(cols, n - r * cols)
            offset = (cols - in_row) * (cw + gap) / 2  # centre a short last row
            bx = x + offset + cidx * (cw + gap)
            by = y + h - (r + 1) * ch - r * gap
            try:
                img = open_upright(p["path"], 1100)
                ix, iy, iw, ih = self.image(img, bx, by + cap, cw, ch - cap)
            except Exception:
                ix, iy, iw, ih = bx, by + cap, cw, ch - cap
                self.c.setStrokeColor(RULE)
                self.c.rect(ix, iy, iw, ih)
                self.text(ix + iw / 2, iy + ih / 2, "Photo could not be opened", 8, color=MUTED, align="center")
            when = _d(p["time"])
            place = ""
            if p.get("stop") is not None and p["stop"] < len(stops):
                place = geo.place_label(stops[p["stop"]].get("place") or {})
            line1 = f"{fmt_day(when.date())}, {when.strftime('%H:%M')}" + (f"  ·  {place}" if place else "")
            self.text(ix, iy - 10, self.fit(line1, 8, max(iw, 120)), 8)
            src = os.path.basename(p["path"]) + (f"  ·  {p['camera']}" if p.get("camera") else "")
            self.text(ix, iy - 19, self.fit(src, 6.5, max(iw, 120)), 6.5, color=MUTED)


def build_pdf(project, out_path):
    Report(project, out_path).build()
    return out_path
