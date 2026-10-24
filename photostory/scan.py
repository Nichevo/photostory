"""Find photos in a folder and read their date, time and GPS position from EXIF."""
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PIL import Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # HEIC photos are skipped without it
    pass

from .util import load_json, save_json

PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff", ".webp"}

# Dropbox Camera Uploads names files like "2024-05-12 14.33.21.jpg"; phones use "IMG_20240512_143321.jpg".
_NAME_PATTERNS = [
    re.compile(r"(20\d\d|19\d\d)-(\d\d)-(\d\d)[ _](\d\d)\.(\d\d)\.(\d\d)"),
    re.compile(r"(20\d\d|19\d\d)(\d\d)(\d\d)[_-](\d\d)(\d\d)(\d\d)"),
]


def _ratio(v):
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            return v[0] / v[1]
        except Exception:
            return None


def _dms(values, ref):
    try:
        d, m, s = (_ratio(x) for x in values)
        if d is None or m is None or s is None:
            return None
        val = d + m / 60 + s / 3600
    except (TypeError, ValueError):
        return None
    if isinstance(ref, bytes):
        ref = ref.decode(errors="ignore")
    if ref and ref.strip().upper() in ("S", "W"):
        val = -val
    return val


def _parse_dt(s):
    if not s:
        return None
    if isinstance(s, bytes):
        s = s.decode(errors="ignore")
    s = s.strip().replace("\x00", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _date_from_name(name):
    for pat in _NAME_PATTERNS:
        m = pat.search(name)
        if m:
            try:
                return datetime(*map(int, m.groups()))
            except ValueError:
                pass
    return None


def read_photo(path):
    """Return a dict of metadata for one photo, or None if it can't be read."""
    info = {"path": path, "time": None, "lat": None, "lon": None, "camera": "", "w": 0, "h": 0}
    dt = None
    try:
        with Image.open(path) as img:
            info["w"], info["h"] = img.size
            exif = img.getexif()
            sub = exif.get_ifd(0x8769)
            dt = _parse_dt(sub.get(36867) or sub.get(36868) or exif.get(306))
            info["camera"] = " ".join(str(exif.get(t, "")).strip("\x00 ") for t in (271, 272)).strip()
            if exif.get(274) in (5, 6, 7, 8):
                info["w"], info["h"] = info["h"], info["w"]
            gps = exif.get_ifd(0x8825)
            if gps and 2 in gps and 4 in gps:
                lat, lon = _dms(gps[2], gps.get(1)), _dms(gps[4], gps.get(3))
                if lat is not None and lon is not None and not (abs(lat) < 1e-6 and abs(lon) < 1e-6):
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        info["lat"], info["lon"] = round(lat, 6), round(lon, 6)
    except Exception:
        return None
    dt = dt or _date_from_name(os.path.basename(path))
    if dt:
        info["time"] = dt.isoformat(timespec="seconds")
    return info


def list_photos(folder):
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.startswith(".") and os.path.splitext(f)[1].lower() in PHOTO_EXTS:
                yield os.path.join(root, f)


def scan_folder(folder, cache_path, date_from=None, date_to=None, progress=None):
    """Read metadata for every photo under folder. Results are cached by path, size and mtime.

    date_from/date_to are datetime.date; photos outside the range (or with no date) are dropped.
    """
    cache = load_json(cache_path, {})
    paths = list(list_photos(folder))
    todo, results = [], []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = f"{st.st_size}:{int(st.st_mtime)}"
        hit = cache.get(p)
        if hit and hit.get("key") == key:
            results.append(hit["info"])
        else:
            todo.append((p, key))

    done = len(results)
    total = len(paths)
    if progress:
        progress(done, total, "Reading photo dates and locations")
    with ThreadPoolExecutor(max_workers=8) as pool:
        for (p, key), info in zip(todo, pool.map(lambda t: read_photo(t[0]), todo)):
            done += 1
            if info is not None:
                cache[p] = {"key": key, "info": info}
                results.append(info)
            if progress and done % 50 == 0:
                progress(done, total, "Reading photo dates and locations")
            if done % 2000 == 0:
                save_json(cache_path, cache)
    save_json(cache_path, cache)

    out = []
    for info in results:
        if not info.get("time"):
            continue
        d = datetime.fromisoformat(info["time"]).date()
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        out.append(info)
    out.sort(key=lambda i: i["time"])
    return out
