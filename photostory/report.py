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
TINT = HexColor("#fbecee")

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
        return f"{a.day} {a.strftime('%b %Y')}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {b.strftime('%b %Y')}"
    if a.year == b.year:
        return f"{a.day} {a.strftime('%b')} – {b.day} {b.strftime('%b %Y')}"
    return f"{a.day} {a.strftime('%b %Y')} – {b.day} {b.strftime('%b %Y')}"


def _d(s):
    return datetime.fromisoformat(s)


def caption(trip, p):
    text = (trip.get("captions") or {}).get(p["path"]) or (trip.get("events") or {}).get(p["path"]) or ""
    return text.strip()


def events(trip):
    """Highlighted moments in a trip, e.g. "Got engaged": [(photo, label)] in time order."""
    starred = set(trip.get("stars") or []) | {k for k, v in (trip.get("events") or {}).items() if v.strip()}
    photos = sorted((p for p in trip["photos"] if p["path"] in starred), key=lambda p: p["time"])
    return [(p, caption(trip, p) or _place(trip, p) or "Highlight") for p in photos]


def _place(trip, p):
    if p.get("stop") is not None and p["stop"] < len(trip["stops"]):
        return geo.place_label(trip["stops"][p["stop"]].get("place") or {})
    return ""


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

    def star(self, cx, cy, r, color):
        c = self.c
        path = c.beginPath()
        for k in range(10):
            a = math.pi / 2 + k * math.pi / 5
            rr = r if k % 2 == 0 else r * 0.45
            pt = (cx + rr * math.cos(a), cy + rr * math.sin(a))
            path.moveTo(*pt) if k == 0 else path.lineTo(*pt)
        path.close()
        c.setFillColor(color)
        c.drawPath(path, stroke=0, fill=1)

    def footer(self):
        self.page += 1
        self.c.setStrokeColor(RULE)
        self.c.line(M, 30, PAGE_W - M, 30)
        self.text(M, 18, "Dates and places are taken from the metadata (EXIF) stored in the original photo files"
                  + (", and Google Maps Timeline for photos without a location." if self.p.get("timeline_used") else "."),
                  7, color=MUTED)
        self.text(PAGE_W - M, 18, f"Page {self.page}", 7, color=MUTED, align="right")
        self.c.showPage()


    # --- pages -----------------------------------------------------------------------------------
    def build(self):
        self.cover()
        if len(self.trips) > 1:
            self.mini_maps()
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
            hero = cover_photo(self.p, self.trips)
            if hero:
                try:
                    img = crop_to(open_upright(hero["path"], 1600), COVER_PHOTO_BOX)
                    self.image(img, M, y - COVER_PHOTO_BOX[1], *COVER_PHOTO_BOX)
                    y -= COVER_PHOTO_BOX[1] + 8
                except Exception:
                    pass
            spec = cover_map(self.trips)
            if spec["points"]:
                img = render_map(spec, COVER_MAP_BOX, self.p.get("cover_map_view"))
                self.image(img, M, y - COVER_MAP_BOX[1], *COVER_MAP_BOX, quality=90)
                y -= COVER_MAP_BOX[1] + 24

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

        highlights = [(n, ph, label) for n, t in enumerate(self.trips, 1) for ph, label in events(t)]
        if highlights:
            y -= 12
            if y < 90:
                self.footer()
                y = PAGE_H - M - 10
            self.text(M, y, "HIGHLIGHTS", 8, bold=True, color=ACCENT)
            y -= 16
            for n, ph, label in highlights:
                if y < 60:
                    self.footer()
                    y = PAGE_H - M - 10
                self.star(M + 4, y + 3, 4.5, ACCENT)
                self.text(M + 14, y, self.fit(label, 9, 250, bold=True), 9, bold=True)
                where = _place(self.trips[n - 1], ph)
                self.text(M + 290, y, self.fit(f"{fmt_day(_d(ph['time']).date())}  ·  Trip {n}"
                                              + (f"  ·  {where}" if where else ""), 9, PAGE_W - 2 * M - 290), 9)
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

    def mini_maps(self):
        """A page (or more) with a small map of each trip."""
        cols, rows = 2, 3
        gap = 16
        cw = (PAGE_W - 2 * M - gap) / cols
        mh = 165
        for start in range(0, len(self.trips), cols * rows):
            y = PAGE_H - M - 6
            self.text(M, y, "WHERE WE WENT", 9, bold=True, color=ACCENT)
            y -= 22
            for k, t in enumerate(self.trips[start:start + cols * rows]):
                n = start + k + 1
                r, cidx = divmod(k, cols)
                x = M + cidx * (cw + gap)
                top = y - r * (mh + 62)
                if t["stops"]:
                    img = render_map(trip_map(t), (cw, mh))
                    self.image(img, x, top - mh, cw, mh, quality=88)
                else:
                    self.c.setFillColor(TINT)
                    self.c.rect(x, top - mh, cw, mh, stroke=0, fill=1)
                    self.text(x + cw / 2, top - mh / 2, "No location in these photos", 9, color=MUTED, align="center")
                self.text(x, top - mh - 14, self.fit(f"{n}. {t.get('title', '')}", 10.5, cw, bold=True), 10.5, bold=True)
                a, b = _d(t["start"]).date(), _d(t["end"]).date()
                self.text(x, top - mh - 28, f"{fmt_range(a, b)}  ·  {_nights(t['nights'])}", 9, color=MUTED)
                ev = events(t)
                if ev:
                    self.star(x + 4, top - mh - 39, 4, ACCENT)
                    self.text(x + 12, top - mh - 42, self.fit(", ".join(lbl for _, lbl in ev), 9, cw - 14), 9, color=ACCENT)
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

        ev = events(t)[:4]
        if ev:
            bh = 10 + 15 * len(ev)
            self.c.setFillColor(TINT)
            self.c.roundRect(M, y - bh, PAGE_W - 2 * M, bh, 4, stroke=0, fill=1)
            ly = y - 15
            for ph, label in ev:
                self.star(M + 12, ly + 3.5, 5, ACCENT)
                self.text(M + 24, ly, self.fit(label, 10.5, 260, bold=True), 10.5, bold=True, color=ACCENT)
                when = _d(ph["time"])
                where = _place(t, ph)
                self.text(M + 290, ly, self.fit(f"{fmt_day(when.date())}, {when.strftime('%H:%M')}"
                                               + (f"  ·  {where}" if where else ""), 9, PAGE_W - 2 * M - 300), 9)
                ly -= 15
            y -= bh + 8

        # Map, with stops numbered in the order first visited
        stops = t["stops"]
        order = sorted(range(len(stops)), key=lambda i: stops[i]["first"])
        num = {i: k + 1 for k, i in enumerate(order)}
        if stops:
            img = render_map(trip_map(t), TRIP_MAP_BOX, t.get("map_view"))
            self.image(img, M, y - TRIP_MAP_BOX[1], *TRIP_MAP_BOX, quality=90)
            y -= TRIP_MAP_BOX[1] + 16

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
            # Shorten the place name rather than the dates and nights.
            tail = f"  —  {when}{extra}"
            room = col_w - 24 - pdfmetrics.stringWidth(tail, self.font, 8.5)
            self.text(cx + 14, cy, self.fit(label, 8.5, max(room, 40)) + tail, 8.5)
        y -= math.ceil(len(shown) / 2) * 14 + 4

        if t.get("notes"):
            for line in self.wrap(t["notes"], 9, PAGE_W - 2 * M)[:8]:
                self.text(M, y, line, 9)
                y -= 12
            y -= 4

        # Photos: four on the trip's page, then more pages of six if more were chosen
        by_path = {p["path"]: p for p in t["photos"]}
        sel = [by_path[s] for s in t.get("selected", []) if s in by_path]
        first, rest = sel[:4], sel[4:]
        if first:
            self.photo_grid(first, t, M, 44, PAGE_W - 2 * M, y - 44)
        self.footer()
        while rest:
            page, rest = rest[:6], rest[6:]
            y = PAGE_H - M - 6
            self.text(M, y, f"TRIP {n} (CONTINUED)", 9, bold=True, color=ACCENT)
            y -= 20
            self.text(M, y, self.fit(t.get("title", ""), 14, PAGE_W - 2 * M, bold=True), 14, bold=True)
            y -= 16
            self.photo_grid(page, t, M, 44, PAGE_W - 2 * M, y - 44, cols=2)
            self.footer()

    def photo_grid(self, photos, trip, x, y, w, h, cols=None):
        labels = dict((p["path"], label) for p, label in events(trip))
        n = len(photos)
        cols = cols or (1 if n == 1 else 2 if n <= 4 else 3)
        rows = math.ceil(n / cols)
        gap = 10
        cap = 31 if any(caption(trip, p) for p in photos) else 22
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
            if p["path"] in labels:
                # Make highlighted moments stand out: a coloured frame and a label on the photo.
                c = self.c
                c.setStrokeColor(ACCENT)
                c.setLineWidth(3)
                c.rect(ix, iy, iw, ih, stroke=1, fill=0)
                c.setLineWidth(1)
                text = self.fit(labels[p["path"]], 10, iw - 36, bold=True)
                bw = pdfmetrics.stringWidth(text, self.bold, 10) + 30
                c.setFillColor(ACCENT)
                c.rect(ix, iy + ih - 20, bw, 20, stroke=0, fill=1)
                self.star(ix + 11, iy + ih - 9.5, 5, HexColor("#ffffff"))
                self.text(ix + 21, iy + ih - 13.5, text, 10, bold=True, color=HexColor("#ffffff"))
            when = _d(p["time"])
            place = _place(trip, p)
            line1 = f"{fmt_day(when.date())}, {when.strftime('%H:%M')}" + (f"  ·  {place}" if place else "")
            ty = iy - 10
            text = caption(trip, p)
            if text and p["path"] not in labels:
                self.text(ix, ty, self.fit(text, 9, max(iw, 120), bold=True), 9, bold=True)
                ty -= 10
            self.text(ix, ty, self.fit(line1, 8, max(iw, 120)), 8)
            src = os.path.basename(p["path"]) + (f"  ·  {p['camera']}" if p.get("camera") else "")
            if p.get("loc_src") == "timeline":
                src += "  ·  location from Google Timeline"
            self.text(ix, ty - 9, self.fit(src, 6.5, max(iw, 120)), 6.5, color=MUTED)


TRIP_MAP_BOX = (PAGE_W - 2 * M, 230)
COVER_MAP_BOX = (PAGE_W - 2 * M, 200)
COVER_PHOTO_BOX = (PAGE_W - 2 * M, 250)


def map_px(box):
    # Draw maps at about 170 dpi so they stay sharp in print.
    return int(box[0] * 2.3), int(box[1] * 2.3)


def trip_map(t):
    stops = t["stops"]
    order = sorted(range(len(stops)), key=lambda i: stops[i]["first"])
    num = {i: k + 1 for k, i in enumerate(order)}
    return {"points": [(s["lat"], s["lon"]) for s in stops], "route": t.get("route"),
            "labels": [num[i] for i in range(len(stops))], "max_zoom": 12}


def cover_map(trips):
    pts, labels = [], []
    for n, t in enumerate(trips, 1):
        if t["stops"]:
            main = max(t["stops"], key=lambda s: s["count"])
            pts.append((main["lat"], main["lon"]))
            labels.append(n)
    return {"points": pts, "labels": labels, "max_zoom": 10}


def render_map(spec, box, view=None):
    return maps.render(spec["points"], size=map_px(box), route=spec.get("route"), labels=spec.get("labels"),
                       max_zoom=spec["max_zoom"], view=view)


def cover_photo(project, trips):
    """The photo for the cover: the one chosen, or the best photo of both of you."""
    chosen = project.get("cover_photo")
    photos = [p for t in trips for p in t["photos"] if p["path"] in set(t.get("selected", []))]
    for p in photos:
        if p["path"] == chosen:
            return p
    if not photos:
        return None
    return max(photos, key=lambda p: p.get("score", 0) + (10 if p.get("w", 0) >= p.get("h", 0) else 0))


def crop_to(img, box):
    """Crop the middle of an image to the box's shape, a little above centre where faces usually are."""
    w, h = img.size
    want = box[0] / box[1]
    if w / h > want:
        nw = int(h * want)
        return img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    nh = int(w / want)
    top = max(0, min(h - nh, int((h - nh) * 0.4)))
    return img.crop((0, top, w, top + nh))


def build_pdf(project, out_path):
    Report(project, out_path).build()
    return out_path
