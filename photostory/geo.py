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


def place_label(p, with_country=True, with_region=True):
    """e.g. "Pudong, Shanghai, China". The region is left out when it repeats the city."""
    parts = []
    for part in (p.get("city", ""), p.get("region", "") if with_region else "", p.get("country", "") if with_country else ""):
        part = (part or "").strip()
        if part and not any(part.lower() in q.lower() or q.lower() in part.lower() for q in parts):
            parts.append(part)
    return ", ".join(parts)


def detail(lat, lon):
    """What's at a point, at street level: {"poi", "area", "city", "region", "country"}.
    poi is a named place such as a park, museum or restaurant, when there is one."""
    key = f"d{lat:.3f},{lon:.3f}"
    with _lock:
        cache = _load_cache()
        if key in cache:
            return cache[key]
        try:
            q = urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.5f}", "lon": f"{lon:.5f}",
                                        "zoom": 18, "accept-language": "en"})
            data = _get("https://nominatim.openstreetmap.org/reverse?" + q)
        except Exception:
            return {}
        if data.get("error"):
            # Nothing on the map here: usually open water, e.g. on a boat.
            result = {"poi": "", "area": "", "city": "", "region": "", "country": "", "sea": True}
            cache[key] = result
            save_json(_CACHE_PATH, cache)
            return result
        addr = data.get("address", {})
        poi = ""
        if data.get("name") and data.get("category") in ("tourism", "amenity", "leisure", "historic", "natural",
                                                           "building", "shop", "railway", "aeroway", "place"):
            poi = data["name"]
        result = {"poi": poi,
                  "area": addr.get("neighbourhood") or addr.get("quarter") or addr.get("suburb")
                          or addr.get("city_district") or "",
                  "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or "",
                  "region": addr.get("state") or "", "country": addr.get("country") or ""}
        cache[key] = result
        save_json(_CACHE_PATH, cache)
        return result


# Landmarks and travel clues from OpenStreetMap (Overpass API), e.g. the Great Wall or Sky Tower.
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
LANDMARK_KM = 0.4
_SKIP_TOURISM = {"hotel", "guest_house", "hostel", "apartment", "motel", "information", "camp_site",
                 "caravan_site", "chalet", "picnic_site", "artwork"}
_NATURAL = {"peak", "volcano", "waterfall", "beach", "bay", "cliff", "glacier", "arch", "cave_entrance", "strait"}
_MAN_MADE = {"tower", "lighthouse", "bridge", "pier", "observatory", "dam"}
_WATER = {"water", "bay", "strait"}


def _overpass(query):
    global _last_call
    data = urllib.parse.urlencode({"data": query}).encode()
    for url in OVERPASS:
        wait = 1.1 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
    return None


def _landmark_score(tags, dist_km):
    """How much a mapped place looks like a famous landmark, or None if it isn't one."""
    t = tags.get("tourism")
    score = 0.0
    if t in _SKIP_TOURISM:
        return None
    if t:
        score += 2 if t in ("attraction", "viewpoint", "theme_park", "zoo") else 1
    if tags.get("historic"):
        score += 1.5
    if tags.get("man_made") in _MAN_MADE:
        score += 2
    if tags.get("natural") in _NATURAL:
        score += 1.5
    if tags.get("heritage") or tags.get("heritage:operator"):
        score += 1.5
    if tags.get("amenity") == "place_of_worship" or tags.get("building") in ("cathedral", "temple", "mosque"):
        score += 1
    if not score and not tags.get("building"):
        return None
    if tags.get("wikipedia"):
        score += 2
    return score - min(dist_km, LANDMARK_KM) * 2


def surroundings(lat, lon):
    """Landmarks and travel clues near a point:
    {"landmark": name or "", "rail": near a railway line, "ferry": near a ferry route, "water": on water}.
    Empty when offline. Cached."""
    key = f"o{lat:.3f},{lon:.3f}"
    with _lock:
        cache = _load_cache()
        if key in cache:
            return cache[key]
        r = int(LANDMARK_KM * 1000)
        q = f"""[out:json][timeout:25];
(
  nwr(around:{r},{lat:.5f},{lon:.5f})[name][wikidata];
  way(around:40,{lat:.5f},{lon:.5f})[railway~"^(rail|subway|light_rail|monorail|narrow_gauge)$"];
  way(around:300,{lat:.5f},{lon:.5f})[route=ferry];
);
out tags center 60;
is_in({lat:.5f},{lon:.5f})->.a;
area.a[~"^(natural|water|place)$"~"^(water|bay|strait|sea|ocean|harbour|river|lake|reservoir)$"];
out tags;"""
        data = _overpass(q)
        if data is None:
            return {}
        best, rail, ferry, water = None, False, False, False
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            if el["type"] == "area":
                if tags.get("natural") in _WATER or tags.get("place") in ("sea", "ocean") or tags.get("water"):
                    water = True
                continue
            if tags.get("railway"):
                rail = True
                continue
            if tags.get("route") == "ferry":
                ferry = True
                continue
            c = el.get("center") or el
            if "lat" not in c:
                continue
            if tags.get("place") or tags.get("boundary") or tags.get("highway") or tags.get("public_transport"):
                continue
            s = _landmark_score(tags, haversine_km((lat, lon), (c["lat"], c["lon"])))
            if s is not None and s >= 2 and (best is None or s > best[0]):
                best = (s, tags.get("name:en") or tags["name"])
        result = {"landmark": best[1] if best else "", "rail": rail, "ferry": ferry, "water": water}
        cache[key] = result
        save_json(_CACHE_PATH, cache)
        return result
