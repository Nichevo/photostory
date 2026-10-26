"""Spot photos taken on a plane, train or boat.

Clues, strongest first: Google Timeline's activity at that moment, the photo's own GPS altitude
and speed, how fast we moved between nearby photos, and what the map has at that spot
(open water, a ferry route, a railway line)."""
from datetime import datetime

from PIL import Image

from . import geo

LABELS = {"plane": "On the plane", "train": "On the train", "boat": "On the boat"}


def _num(v):
    try:
        if isinstance(v, tuple):
            return v[0] / v[1] if v[1] else None
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def exif_motion(path):
    """(altitude in metres, speed in km/h) from the photo's GPS tags; None for each when missing."""
    try:
        with Image.open(path) as img:
            gps = img.getexif().get_ifd(0x8825)
    except Exception:
        return None, None
    alt = _num(gps.get(6))
    if alt is not None and gps.get(5) in (1, b"\x01"):
        alt = -alt
    speed = _num(gps.get(13))
    if speed is not None:
        ref = gps.get(12, "K")
        ref = ref.decode(errors="ignore") if isinstance(ref, bytes) else str(ref)
        speed *= {"M": 1.609, "N": 1.852}.get(ref.strip("\x00 ")[:1].upper(), 1.0)
    return alt, speed


def moving_speed(p, photos, minutes=30):
    """Our speed in km/h between this photo and the nearest photo with its own GPS, or None."""
    if p.get("lat") is None or p.get("loc_src") == "timeline":
        return None
    t = datetime.fromisoformat(p["time"])
    best = None
    for q in photos:
        if q is p or q.get("lat") is None or q.get("loc_src") == "timeline":
            continue
        dt = abs((datetime.fromisoformat(q["time"]) - t).total_seconds())
        if 60 <= dt <= minutes * 60 and (best is None or dt < best[0]):
            best = (dt, q)
    if not best:
        return None
    km = geo.haversine_km((p["lat"], p["lon"]), (best[1]["lat"], best[1]["lon"]))
    return km / (best[0] / 3600)


def mode(p, photos, look=None, detail=None):
    """"plane", "train", "boat" or "" for one photo."""
    if p.get("travel"):
        return p["travel"]
    alt, speed = exif_motion(p["path"])
    moving = moving_speed(p, photos)
    fastest = max(v for v in (speed, moving, 0) if v is not None)
    if (alt is not None and alt > 4500) or fastest > 250:
        return "plane"
    if p.get("lat") is None:
        return ""
    near = (look or geo.surroundings)(p["lat"], p["lon"]) or {}
    if near.get("water") or (detail or {}).get("sea") or (near.get("ferry") and fastest > 8):
        return "boat"
    if near.get("rail") and fastest > 40:
        return "train"
    return ""
