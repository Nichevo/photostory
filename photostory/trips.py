"""Group photos into trips away from home, and trips into stops (places visited)."""
from collections import Counter
from datetime import datetime, timedelta

from . import geo


def _t(p):
    return datetime.fromisoformat(p["time"])


def guess_home(photos):
    """The place with the most photos, on a grid of roughly 20 km cells."""
    cells = Counter((round(p["lat"] * 5) / 5, round(p["lon"] * 5) / 5) for p in photos if p["lat"] is not None)
    if not cells:
        return None
    return cells.most_common(1)[0][0]


def find_trips(photos, homes, min_km=50, max_gap_hours=36, min_nights=1, min_photos=3, stop_km=30,
               same_area_km=150, same_area_gap_days=6):
    gap = timedelta(hours=max_gap_hours)
    long_gap = timedelta(days=same_area_gap_days)
    home_tz = home_timezones(photos, homes, min_km)

    def continues(cur, p):
        """Does photo p carry on the trip in cur? Days without photos are normal on a trip, so a
        longer gap is fine while you're still in the same area and no photo was taken at home."""
        dt = _t(p) - _t(cur[-1])
        if dt <= gap:
            return True
        q = cur[-1]
        if p["lat"] is not None and q["lat"] is not None:
            near = geo.haversine_km((p["lat"], p["lon"]), (q["lat"], q["lon"])) <= same_area_km
        else:
            near = bool(p.get("tz")) and p.get("tz") == q.get("tz")
        return near and dt <= long_gap

    def away(p):
        """True if away from home, False if at home, None if we can't tell.
        Without GPS, a photo taken in another time zone than home's must have been taken away."""
        if p["lat"] is not None:
            return all(geo.haversine_km((p["lat"], p["lon"]), h) > min_km for h in homes)
        if p.get("tz") and home_tz and p["tz"] not in home_tz:
            return True
        return None

    raw, cur = [], None
    for p in photos:
        where = away(p)
        if where is None:
            continue
        if where:
            if cur and continues(cur, p):
                cur.append(p)
            else:
                if cur:
                    raw.append(cur)
                cur = [p]
        elif cur:
            raw.append(cur)
            cur = None
    if cur:
        raw.append(cur)

    # A single stray photo tagged at home shouldn't split a trip in two.
    merged = []
    for group in raw:
        if merged and _t(group[0]) - _t(merged[-1][-1]) <= timedelta(hours=6):
            merged[-1].extend(group)
        else:
            merged.append(group)

    trips = []
    for group in merged:
        start, end = _t(group[0]), _t(group[-1])
        nights = (end.date() - start.date()).days
        if nights < min_nights or len(group) < min_photos:
            continue
        # Photos without GPS (e.g. edited copies, some cameras) taken during the trip belong to it too.
        members = [p for p in photos if start <= _t(p) <= end]
        trip = _describe([p for p in group if p["lat"] is not None], members, start, end, nights, stop_km)
        zones = Counter(p["tz"] for p in group if p.get("tz"))
        trip["tz"] = zones.most_common(1)[0][0] if zones else ""
        trips.append(trip)
    for i, t in enumerate(trips, 1):
        t["id"] = f"trip{i}"
    return trips


def home_timezones(photos, homes, min_km):
    """Time zones (e.g. "+10:00") of photos taken at home. Daylight saving gives a home two zones."""
    at_home = Counter(p["tz"] for p in photos if p.get("tz") and p["lat"] is not None and homes
                      and any(geo.haversine_km((p["lat"], p["lon"]), h) <= min_km for h in homes))
    if not at_home:
        # No photos with a location at home: assume home is the time zone most photos were taken in.
        zones = Counter(p["tz"] for p in photos if p.get("tz"))
        return {zones.most_common(1)[0][0]} if zones else set()
    total = sum(at_home.values())
    return {tz for tz, n in at_home.items() if n >= total * 0.05}


def _describe(gps_photos, members, start, end, nights, stop_km):
    stops = []
    route = []
    for p in gps_photos:
        pt = (p["lat"], p["lon"])
        best = None
        for i, s in enumerate(stops):
            d = geo.haversine_km(pt, (s["lat"], s["lon"]))
            if d < stop_km and (best is None or d < best[0]):
                best = (d, i)
        if best is None:
            stops.append({"lat": p["lat"], "lon": p["lon"], "count": 0, "first": p["time"], "last": p["time"],
                          "_sum": [0.0, 0.0], "nights": 0})
            idx = len(stops) - 1
        else:
            idx = best[1]
        s = stops[idx]
        s["count"] += 1
        s["_sum"][0] += p["lat"]
        s["_sum"][1] += p["lon"]
        s["lat"], s["lon"] = s["_sum"][0] / s["count"], s["_sum"][1] / s["count"]
        s["last"] = p["time"]
        p["_stop"] = idx
        if not route or route[-1] != idx:
            route.append(idx)

    # Where the last photo of each day was taken is a good guess for where that night was spent.
    last_of_day = {}
    for p in gps_photos:
        last_of_day[_t(p).date()] = p["_stop"]
    for d, idx in last_of_day.items():
        if d != end.date():
            stops[idx]["nights"] += 1
    for s in stops:
        del s["_sum"]

    photos = []
    for p in members:
        q = {k: v for k, v in p.items() if not k.startswith("_")}
        if "_stop" in p:
            q["stop"] = p["_stop"]
        photos.append(q)
    for p in gps_photos:
        p.pop("_stop", None)

    return {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
            "nights": nights, "photo_count": len(members), "stops": stops, "route": route, "photos": photos}


def name_places(trip, max_lookups=8):
    """Look up place names for a trip's biggest stops and build a title."""
    order = sorted(range(len(trip["stops"])), key=lambda i: -trip["stops"][i]["count"])
    for rank, i in enumerate(order):
        s = trip["stops"][i]
        if "place" in s:
            continue
        s["place"] = geo.reverse(s["lat"], s["lon"]) if rank < max_lookups else {"city": "", "region": "", "country": ""}
    trip["title"] = trip_title(trip)


def trip_title(trip):
    stops = sorted(trip["stops"], key=lambda s: -s["count"])
    cities, countries = [], []
    for s in stops:
        p = s.get("place") or {}
        c = p.get("city") or p.get("region")
        if c and c not in cities and len(cities) < 3 and s["count"] >= max(2, stops[0]["count"] * 0.1):
            cities.append(c)
        if p.get("country") and p["country"] not in countries:
            countries.append(p["country"])
    if not cities and not countries:
        when = "Trip " + datetime.fromisoformat(trip["start"]).strftime("%b %Y")
        if not trip["stops"] and trip.get("tz"):
            return f"{when}, place unknown (UTC{trip['tz']})"
        return when
    if len(countries) > 2:
        return _join(countries[:3]) + (" and more" if len(countries) > 3 else "")
    if len(countries) == 2:
        return f"{_join(cities)} ({_join(countries)})" if cities else _join(countries)
    return f"{_join(cities)}, {countries[0]}" if cities and countries else _join(cities or countries)


def _join(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " & " + items[-1]


def _areas(trip, area_km=60):
    """Group a trip's stops into areas (stops within area_km of each other): {stop index: area id}."""
    stops = trip["stops"]
    area = list(range(len(stops)))

    def root(i):
        while area[i] != i:
            i = area[i]
        return i

    for i in range(len(stops)):
        for j in range(i):
            if geo.haversine_km((stops[i]["lat"], stops[i]["lon"]), (stops[j]["lat"], stops[j]["lon"])) < area_km:
                area[root(i)] = root(j)
    return {i: root(i) for i in range(len(stops))}


def segments(trip):
    """Split points for a trip: lists of dates, one list per run of days spent in the same area."""
    area = _areas(trip)
    by_day = {}
    for p in trip["photos"]:
        if p.get("stop") is not None and p["stop"] < len(trip["stops"]):
            by_day.setdefault(p["time"][:10], Counter())[area[p["stop"]]] += 1
    segs = []
    for day in sorted(by_day):
        a = by_day[day].most_common(1)[0][0]
        if segs and segs[-1][0] == a:
            segs[-1][1].append(day)
        else:
            segs.append((a, [day]))
    return [days for _, days in segs]


def split_trip(trip, stop_km=30):
    """Split a trip into one part per area visited, in time order. Returns [] if it can't be split."""
    segs = segments(trip)
    if len(segs) < 2:
        return []
    parts = []
    for k, days in enumerate(segs):
        later = segs[k + 1][0] if k + 1 < len(segs) else None
        members = [{key: v for key, v in p.items() if key != "stop"} for p in trip["photos"]
                   if p["time"][:10] >= days[0] and (later is None or p["time"][:10] < later)]
        gps = [p for p in members if p["lat"] is not None]
        if not gps:
            continue
        start, end = _t(members[0]), _t(members[-1])
        # Count the night before moving on to the next part as part of this one.
        nights = (datetime.fromisoformat(later).date() - start.date()).days if later else (end.date() - start.date()).days
        part = _describe(gps, members, start, end, nights, stop_km)
        part["id"] = f"{trip['id']}-{k + 1}"
        part["parent"] = trip["id"]
        parts.append(part)
    return parts if len(parts) > 1 else []


def manual_stop(trip, lat, lon, place):
    """Give a trip whose photos have no location a single place the user typed in."""
    trip["stops"] = [{"lat": lat, "lon": lon, "count": len(trip["photos"]), "first": trip["start"],
                      "last": trip["end"], "nights": trip["nights"], "place": place}]
    trip["route"] = [0]
    for p in trip["photos"]:
        p["stop"] = 0


def manual_trip(photos, start_day, end_day, stop_km=30):
    """A trip the user added by dates: every photo from start_day to end_day (YYYY-MM-DD)."""
    members = [p for p in photos if start_day <= p["time"][:10] <= end_day]
    if not members:
        return None
    start, end = _t(members[0]), _t(members[-1])
    gps = [p for p in members if p["lat"] is not None]
    trip = _describe(gps, members, start, end, (end.date() - start.date()).days, stop_km)
    trip["tz"] = Counter(p["tz"] for p in members if p.get("tz")).most_common(1)[0][0] if any(
        p.get("tz") for p in members) else ""
    return trip


def stop_key(s):
    """A stop's identity that survives refreshing trips: its position, to about 100 m."""
    return f"{s['lat']:.3f},{s['lon']:.3f}"


def shown_stops(trip):
    """Indexes of the stops to show on maps; the user can untick wrong ones."""
    hidden = set(trip.get("hidden_stops") or [])
    return [i for i, s in enumerate(trip["stops"]) if stop_key(s) not in hidden]


CLOSEUP_KM = 0.6  # photos this close together count as one spot
CLOSEUP_MIN_TRIP_KM = 5  # a trip smaller than this already has a close-up map


def closeups(trip, limit=4):
    """Small areas where lots of photos were taken, which may be special places worth a
    close-up map: [{"key", "lat", "lon", "count", "points", "first", "last"}], busiest first."""
    keep = set(shown_stops(trip))
    pts = [p for p in trip["photos"] if p.get("lat") is not None and (p.get("stop") is None or p["stop"] in keep)]
    if len(pts) < 6:
        return []
    lats, lons = [p["lat"] for p in pts], [p["lon"] for p in pts]
    if geo.haversine_km((min(lats), min(lons)), (max(lats), max(lons))) < CLOSEUP_MIN_TRIP_KM:
        return []
    groups = []
    for p in sorted(pts, key=lambda p: p["time"]):
        for g in groups:
            if geo.haversine_km((p["lat"], p["lon"]), (g["lat"], g["lon"])) < CLOSEUP_KM:
                g["ps"].append(p)
                n = len(g["ps"])
                g["lat"] += (p["lat"] - g["lat"]) / n
                g["lon"] += (p["lon"] - g["lon"]) / n
                break
        else:
            groups.append({"lat": p["lat"], "lon": p["lon"], "ps": [p]})
    need = max(6, int(len(pts) * 0.1))
    busy = sorted((g for g in groups if len(g["ps"]) >= need), key=lambda g: -len(g["ps"]))[:limit]
    return [{"key": f"{g['lat']:.3f},{g['lon']:.3f}", "lat": g["lat"], "lon": g["lon"], "count": len(g["ps"]),
             "points": [(p["lat"], p["lon"]) for p in g["ps"]][:300],
             "first": g["ps"][0]["time"], "last": g["ps"][-1]["time"]} for g in busy]
