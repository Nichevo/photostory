"""Draw a map image from OpenStreetMap tiles, with numbered markers and a route line."""
import io
import math
import urllib.request

from PIL import Image, ImageDraw, ImageFont

from .util import APP_DIR, USER_AGENT

TILE = 256
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ACCENT = (200, 40, 60)
_offline = False


def _xy(lat, lon, z):
    lat = max(min(lat, 85.0), -85.0)
    n = 2 ** z * TILE
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _tile(z, x, y):
    global _offline
    n = 2 ** z
    x %= n
    if y < 0 or y >= n:
        return None
    path = APP_DIR / "tiles" / str(z) / str(x) / f"{y}.png"
    if path.exists():
        try:
            return Image.open(path).convert("RGB")
        except Exception:
            pass
    if _offline:
        return None
    try:
        req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        _offline = True  # don't wait on every tile once the network has failed
        return None


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(points, size=(1200, 700), route=None, labels=None, max_zoom=12, min_zoom=2):
    """points: [(lat, lon)]; route: list of point indices to join with a line; labels: text per point."""
    W, H = size
    pad = 70
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    zoom = min_zoom
    for z in range(max_zoom, min_zoom - 1, -1):
        x0, y1 = _xy(min(lats), min(lons), z)
        x1, y0 = _xy(max(lats), max(lons), z)
        if x1 - x0 <= W - 2 * pad and y1 - y0 <= H - 2 * pad:
            zoom = z
            break
    cx = sum(_xy(lat, lon, zoom)[0] for lat, lon in [(min(lats), min(lons)), (max(lats), max(lons))]) / 2
    cy = sum(_xy(lat, lon, zoom)[1] for lat, lon in [(min(lats), min(lons)), (max(lats), max(lons))]) / 2
    left, top = cx - W / 2, cy - H / 2

    img = Image.new("RGB", (W, H), (226, 232, 236))
    got_tiles = False
    for tx in range(int(left // TILE), int((left + W) // TILE) + 1):
        for ty in range(int(top // TILE), int((top + H) // TILE) + 1):
            t = _tile(zoom, tx, ty)
            if t is not None:
                img.paste(t, (int(tx * TILE - left), int(ty * TILE - top)))
                got_tiles = True
    draw = ImageDraw.Draw(img)
    if not got_tiles:
        _graticule(draw, zoom, left, top, W, H)

    px = [(_xy(lat, lon, zoom)[0] - left, _xy(lat, lon, zoom)[1] - top) for lat, lon in points]
    if route and len(route) > 1:
        line = [px[i] for i in route]
        draw.line(line, fill=(255, 255, 255), width=9, joint="curve")
        draw.line(line, fill=ACCENT, width=5, joint="curve")
    font = _font(22)
    r = 17
    for i, (x, y) in enumerate(px):
        draw.ellipse([x - r - 3, y - r - 3, x + r + 3, y + r + 3], fill=(255, 255, 255))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT)
        text = str(labels[i]) if labels else str(i + 1)
        draw.text((x, y), text, fill=(255, 255, 255), font=font, anchor="mm")
    small = _font(16)
    credit = "© OpenStreetMap contributors" if got_tiles else "Map tiles unavailable (offline)"
    tw = draw.textlength(credit, font=small)
    draw.rectangle([W - tw - 16, H - 26, W, H], fill=(255, 255, 255))
    draw.text((W - tw - 8, H - 23), credit, fill=(60, 60, 60), font=small)
    return img


def _graticule(draw, zoom, left, top, W, H):
    """Without tiles, at least draw latitude/longitude lines so the map isn't blank."""
    step = 10 if zoom < 5 else 1 if zoom < 9 else 0.1
    n = 2 ** zoom * TILE

    def lon_at(x):
        return (x + left) / n * 360 - 180

    def lat_at(y):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + top) / n))))

    lon = math.floor(lon_at(0) / step) * step
    while lon <= lon_at(W):
        x = _xy(0, lon, zoom)[0] - left
        draw.line([(x, 0), (x, H)], fill=(200, 208, 214), width=1)
        lon += step
    lat = math.floor(lat_at(H) / step) * step
    while lat <= lat_at(0):
        y = _xy(lat, 0, zoom)[1] - top
        draw.line([(0, y), (W, y)], fill=(200, 208, 214), width=1)
        lat += step
