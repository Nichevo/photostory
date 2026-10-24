"""Distances and place names (OpenStreetMap Nominatim, cached on disk)."""
import json
import math
import threading
import time
import urllib.parse
import urllib.request

from .util import APP_DIR, USER_AGENT, load_json, save_json

_CACHE_PATH = APP_DIR / "geocode_cache.json"
_cache = None
_lock = threading.Lock()
_last_call = 0.0


def haversine_km(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(d))


def _get(url):
    global _last_call
    # Nominatim's usage policy allows at most one request per second.
    wait = 1.1 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _load_cache():
    global _cache
    if _cache is None:
        _cache = load_json(_CACHE_PATH, {})
    return _cache


def reverse(lat, lon):
    """Return {"city", "region", "country"} for a point. Empty strings when offline."""
    key = f"{lat:.2f},{lon:.2f}"
    with _lock:
        cache = _load_cache()
        if key in cache:
            return cache[key]
        try:
            q = urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.5f}", "lon": f"{lon:.5f}",
                                        "zoom": 10, "accept-language": "en"})
            data = _get("https://nominatim.openstreetmap.org/reverse?" + q)
            addr = data.get("address", {})
            city = (addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality")
                    or addr.get("county") or addr.get("state_district") or "")
            result = {"city": city, "region": addr.get("state") or addr.get("region") or "",
                      "country": addr.get("country") or ""}
        except Exception:
            return {"city": "", "region": "", "country": ""}
        cache[key] = result
        save_json(_CACHE_PATH, cache)
        return result


def search(text):
    """Turn "lat,lon" or a place name into (lat, lon), or None."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.replace(";", ",").split(",")
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    try:
        q = urllib.parse.urlencode({"format": "jsonv2", "q": text, "limit": 1})
        data = _get("https://nominatim.openstreetmap.org/search?" + q)
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None


def place_label(p, with_country=True):
    city, country = p.get("city", ""), p.get("country", "")
    if city and country and with_country:
        return f"{city}, {country}"
    return city or p.get("region", "") or country
