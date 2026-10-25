"""Suggest a caption for each chosen photo and a short written story for a trip, from where the
photos were taken. Everything suggested is ordinary text the user can edit."""
from datetime import datetime

from . import geo

MAX_LOOKUPS = 30  # street-level lookups per trip (about one second each)


def _d(s):
    return datetime.fromisoformat(s)


def _day(d):
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _join(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def spot_name(info, with_city=True):
    name = info.get("poi") or info.get("area") or ""
    city = info.get("city") or ""
    if name and city and with_city and name != city:
        return f"{name}, {city}"
    return name or city


def _groups(photos, km=0.8):
    """Photos taken close together on the same day: [(day, [photos])] in time order."""
    groups = []
    for p in photos:
        if p.get("lat") is None:
            continue
        g = groups[-1] if groups else None
        if g and g[0] == p["time"][:10] and geo.haversine_km((p["lat"], p["lon"]), (g[1][0]["lat"], g[1][0]["lon"])) < km:
            g[1].append(p)
        else:
            groups.append((p["time"][:10], [p]))
    return groups


def suggest(trip, progress=lambda done, total: None):
    """Fill in trip["captions"] for chosen photos that have none, and return a story text."""
    photos = sorted(trip["photos"], key=lambda p: p["time"])
    by_path = {p["path"]: p for p in photos}
    chosen = [by_path[s] for s in trip.get("selected", []) if s in by_path and by_path[s].get("lat") is not None]
    groups = sorted(_groups(photos), key=lambda g: -len(g[1]))
    # Look up the chosen photos first, then the busiest spots of the trip.
    points = [(p["lat"], p["lon"]) for p in chosen] + [(g[1][0]["lat"], g[1][0]["lon"]) for g in groups if len(g[1]) >= 2]
    points = points[:MAX_LOOKUPS]
    info = {}
    for n, pt in enumerate(points, 1):
        info[pt] = geo.detail(*pt)
        progress(n, len(points))

    captions = trip.setdefault("captions", {})
    for p in chosen:
        text = spot_name(info.get((p["lat"], p["lon"]), {}))
        if text and not captions.get(p["path"]):
            captions[p["path"]] = text

    # Places of interest per stop and day, for the story.
    spots = {}  # stop index -> day -> Counter-like dict of spot name -> photos
    for day, g in groups:
        here = info.get((g[0]["lat"], g[0]["lon"]))
        if not here or g[0].get("stop") is None:
            continue
        name = here.get("poi") or here.get("area")
        if name:
            spots.setdefault(g[0]["stop"], {}).setdefault(day, {})
            spots[g[0]["stop"]][day][name] = spots[g[0]["stop"]][day].get(name, 0) + len(g)
    return write_story(trip, spots)


def write_story(trip, spots):
    stops = trip["stops"]
    order = sorted(range(len(stops)), key=lambda i: stops[i]["first"])
    cities = []
    for i in order:
        c = (stops[i].get("place") or {}).get("city")
        if c and c not in cities and stops[i]["count"] >= 2:
            cities.append(c)
    a, b = _d(trip["start"]), _d(trip["end"])
    nights = trip["nights"]
    length = "a day" if nights == 0 else f"{nights} night" + ("" if nights == 1 else "s")
    parts = [f"We spent {length} together in {_join(cities) or trip.get('title', 'this trip')}, "
             f"from {_day(a)} to {_day(b)} {b.year}."]
    shown = [i for i in order if stops[i]["count"] >= 2] or order
    for k, i in enumerate(shown):
        s = stops[i]
        city = geo.place_label(s.get("place") or {}, with_country=False, with_region=False) or "the next stop"
        days = spots.get(i, {})
        bits = []
        for day in sorted(days)[:4]:
            names = sorted(days[day], key=lambda n: -days[day][n])[:3]
            bits.append(f"{_join(names)} on {_day(_d(day + 'T00:00:00'))}")
        stay = f" for {s['nights']} night" + ("" if s["nights"] == 1 else "s") if s.get("nights") else ""
        lead = f"In {city}" if k == 0 else f"Then in {city}"
        if bits:
            text = f"{lead}{stay}, we visited " + "; ".join(bits)
            if len(days) > 4:
                text += ", and more over the following days"
            parts.append(text + ".")
        elif len(shown) > 1:
            parts.append(f"{lead}{stay}." if stay else f"{lead}, we explored together.")
    return " ".join(parts)
