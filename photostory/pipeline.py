"""Turn a folder of photos into a project file of trips with suggested photos."""
import os
import threading
from datetime import date, datetime

from . import geo, select, trips
from .scan import VIDEO_EXTS, scan_folder
from .util import Store, load_json, save_json


# Held while reading-changing-writing the project file, since photo picking and the user's edits overlap.
LOCK = threading.RLock()


def project_path(out_dir):
    return os.path.join(out_dir, "project.json")


def load_project(out_dir):
    return load_json(project_path(out_dir), None)


def save_project(out_dir, project):
    save_json(project_path(out_dir), project)


def run(settings, out_dir, progress=lambda pct, msg: None, cache_dir=None):
    """settings: photos, people_dir, date_from, date_to (YYYY-MM-DD), home (text, one place per line),
    min_km, max_gap_hours, min_nights, photos_per_trip, title."""
    os.makedirs(out_dir, exist_ok=True)
    cache_dir = cache_dir or os.path.join(out_dir, "cache")
    old = load_project(out_dir)
    d_from = date.fromisoformat(settings["date_from"]) if settings.get("date_from") else None
    d_to = date.fromisoformat(settings["date_to"]) if settings.get("date_to") else None

    def scan_progress(done, total, msg):
        progress(int(40 * done / max(total, 1)), f"{msg}: {done} of {total}")

    stats = {}
    photos = scan_folder(settings["photos"], os.path.join(cache_dir, "scan.json"), d_from, d_to, scan_progress, stats)
    homes, notes = [], []
    from_timeline = add_timeline(photos, settings, cache_dir, notes, progress)
    with_gps = sum(1 for p in photos if p["lat"] is not None)
    progress(40, f"Found {len(photos)} photos in the date range ({with_gps} with a location)")

    for line in (settings.get("home") or "").splitlines():
        if line.strip():
            h = geo.search(line)
            if h:
                homes.append(h)
            else:
                notes.append(f"Couldn't find the home location “{line.strip()}”, so it was ignored.")
    if not homes:
        h = trips.guess_home(photos)
        if h:
            homes.append(h)
            place = geo.place_label(geo.reverse(*h))
            notes.append(f"Home was guessed from where most photos were taken: {place or h}.")

    found = trips.find_trips(photos, homes, min_km=float(settings.get("min_km", 50)),
                             max_gap_hours=float(settings.get("max_gap_hours", 36)),
                             min_nights=int(settings.get("min_nights", 1)))
    progress(45, f"Found {len(found)} trips; looking up place names")
    for t in found:
        trips.name_places(t)

    if stats.get("unreadable"):
        notes.append(f"{stats['unreadable']} photo files couldn't be read, e.g. {stats['unreadable_examples'][0]}."
                     + ("" if stats.get("heic_support") else " HEIC support (pillow-heif) isn't installed."))
    videos = sum(n for ext, n in stats.get("skipped", {}).items() if ext in VIDEO_EXTS)
    others = {ext: n for ext, n in stats.get("skipped", {}).items() if ext not in VIDEO_EXTS}
    if videos:
        notes.append(f"{videos} videos were skipped; trips are found from photos only.")
    if others:
        top = ", ".join(f"{n} {ext}" for ext, n in sorted(others.items(), key=lambda kv: -kv[1])[:5])
        notes.append(f"Other files that aren't photos were skipped: {top}.")
    if stats.get("undated"):
        notes.append(f"{stats['undated']} photos have no date in them (often screenshots or images saved from "
                     "messages), so they were left out.")
    stats["months"] = month_summary(photos, homes, found, float(settings.get("min_km", 50)))

    # Trips split into parts last time are split the same way again.
    splits = (old or {}).get("splits", [])
    split_parents, kept = {}, []
    for t in found:
        parts = _resplit(t, splits)
        if parts:
            t.update(include=True, notes="", selected=[], analysed=False, parts=len(parts))
            split_parents[t["id"]] = t
        kept.extend(parts or [t])
    found = kept

    # Trips the user added by hand, and places typed in for trips whose photos have no location.
    manual = (old or {}).get("manual_trips", [])
    places = (old or {}).get("places", [])
    for m in manual:
        t = trips.manual_trip(photos, m["from"], m["to"])
        if t:
            t["id"], t["manual"] = m["id"], True
            found = [f for f in found if not _overlap(f, t)] + [t]
    found.sort(key=lambda t: t["start"])
    for t in found:
        _apply_place(t, places)
    for t in found:
        t["include"] = True
        t["notes"] = ""
        t["selected"] = []
        t["analysed"] = False
        t["parts"] = len(trips.segments(t))
    if old:
        carry_over(old, found)

    project = {
        "version": 1,
        "settings": settings,
        "title": settings.get("title") or "",
        "people": [],
        "homes": homes,
        "notes": notes,
        "photo_total": len(photos),
        "timeline_used": from_timeline > 0,
        "stats": stats,
        "trips": found,
        "splits": splits,
        "split_parents": split_parents,
        "manual_trips": manual,
        "places": places,
    }
    if old:
        for k in ("title", "people", "family", "cover_note", "cover_photo", "cover_map_view"):
            if old.get(k):
                project[k] = old[k]
    save_project(out_dir, project)
    progress(100, "Done")
    return project


def _apply_place(t, places):
    """Use a place the user typed in for this trip, if its photos have no location of their own."""
    if t["stops"]:
        return
    for pl in places:
        if _overlap(t, pl):
            trips.manual_stop(t, pl["lat"], pl["lon"], pl["place"])
            trips.name_places(t)
            return


def load_photos(settings, cache_dir):
    """All photos in the story's date range (from the cache, so it's quick after a scan)."""
    d_from = date.fromisoformat(settings["date_from"]) if settings.get("date_from") else None
    d_to = date.fromisoformat(settings["date_to"]) if settings.get("date_to") else None
    photos = scan_folder(settings["photos"], os.path.join(cache_dir, "scan.json"), d_from, d_to)
    add_timeline(photos, settings, cache_dir, [])
    return photos


def add_timeline(photos, settings, cache_dir, notes, progress=lambda pct, msg: None):
    """Fill in locations from Google Maps Timeline for photos without GPS. Returns how many."""
    paths = [line for line in (settings.get("timeline") or "").splitlines() if line.strip()]
    if not paths:
        return 0
    from collections import Counter

    from . import timeline

    progress(38, "Reading your Google Timeline")
    for path in paths:
        if not os.path.exists(path.strip().strip('"')):
            notes.append(f"Couldn't find the Google Timeline file “{path.strip()}”. Check the path, including the drive letter.")
    tl = timeline.load(paths, cache_dir)
    if not len(tl):
        if any(os.path.exists(p.strip().strip('"')) for p in paths):
            notes.append("No locations were found in the Google Timeline file. Check it's the exported Timeline.json "
                         "(or Records.json / Semantic Location History from Google Takeout).")
        return 0
    zones = Counter(p["tz"] for p in photos if p.get("tz"))
    without = sum(1 for p in photos if p["lat"] is None)
    n = timeline.fill_locations(photos, tl, zones.most_common(1)[0][0] if zones else None)
    first, last = tl.span()
    notes.append(f"Google Timeline has {len(tl)} places from {first:%d %b %Y} to {last:%d %b %Y}. It gave a location "
                 f"to {n} of the {without} photos without GPS"
                 + (". Photos outside those dates, or when Timeline was off, can't be matched." if without - n else "."))
    return n


def set_place(out_dir, trip_id, text):
    """The user says where a trip was (its photos have no GPS). Returns False if the place isn't found."""
    pt = geo.search(text)
    if not pt:
        return False
    place = geo.reverse(*pt)
    if not any(place.values()):
        place = {"city": text.split(",")[0].strip(), "region": "", "country": ""}
    with LOCK:
        project = load_project(out_dir)
        t = next(t for t in project["trips"] if t["id"] == trip_id)
        trips.manual_stop(t, pt[0], pt[1], place)
        if not t.get("title_edited"):
            trips.name_places(t)
        project["places"] = [p for p in project.get("places", []) if not _overlap(p, t)] + [
            {"start": t["start"], "end": t["end"], "lat": pt[0], "lon": pt[1], "place": place, "text": text}]
        save_project(out_dir, project)
    return True


def add_trip(out_dir, cache_dir, day_from, day_to, place_text=""):
    """Add a trip by dates, for one PhotoStory didn't find. Returns an error message or None."""
    with LOCK:
        project = load_project(out_dir)
        photos = load_photos(project["settings"], cache_dir)
        t = trips.manual_trip(photos, day_from, day_to)
        if not t:
            return "There are no photos between those dates."
        n = 1 + sum(1 for m in project.get("manual_trips", []))
        t.update(id=f"added{n}", manual=True, include=True, notes="", selected=[], analysed=False, parts=0)
        trips.name_places(t)
        project.setdefault("manual_trips", []).append({"id": t["id"], "from": day_from, "to": day_to})
        project["trips"] = sorted([x for x in project["trips"] if not _overlap(x, t)] + [t], key=lambda x: x["start"])
        save_project(out_dir, project)
    if place_text and not t["stops"]:
        if not set_place(out_dir, t["id"], place_text):
            return f"Added the trip, but couldn't find the place “{place_text}”. Type it again under the trip."
    return None


def remove_added_trip(out_dir, trip_id):
    with LOCK:
        project = load_project(out_dir)
        project["manual_trips"] = [m for m in project.get("manual_trips", []) if m["id"] != trip_id]
        project["trips"] = [t for t in project["trips"] if t["id"] != trip_id]
        save_project(out_dir, project)


def _overlap(a, b):
    s, e = max(a["start"], b["start"]), min(a["end"], b["end"])
    if s > e:
        return 0
    return (datetime.fromisoformat(e) - datetime.fromisoformat(s)).total_seconds() + 1


def _resplit(t, splits):
    if not any(_overlap(t, {"start": s, "end": e}) for s, e in splits):
        return None
    parts = trips.split_trip(t)
    for p in parts:
        trips.name_places(p)
    return parts


EDIT_KEYS = ("title", "notes", "include", "selected", "events", "captions", "stars", "map_view", "edited")


def carry_over(old, found):
    """Keep what the user did last time (names, ticks, photos, highlights) for the same trips."""
    olds = old.get("trips", [])
    for t in found:
        best = max(olds, key=lambda o: _overlap(o, t), default=None)
        if not best or not _overlap(best, t):
            continue
        paths = {p["path"] for p in t["photos"]}
        if best.get("edited"):
            for k in EDIT_KEYS:
                if k in best:
                    t[k] = best[k]
        else:
            t["include"] = best.get("include", True)
        t["selected"] = [s for s in t.get("selected", []) if s in paths]
        t["events"] = {k: v for k, v in (t.get("events") or {}).items() if k in paths}
        t["captions"] = {k: v for k, v in (t.get("captions") or {}).items() if k in paths}
        t["stars"] = [s for s in t.get("stars", []) if s in paths]
        # Reuse photo scores so photos don't need choosing again.
        scored = {p["path"]: p for p in best.get("photos", []) if p.get("score", -1) >= 0}
        for p in t["photos"]:
            if p["path"] in scored:
                for k in ("score", "people", "faces", "unsure"):
                    if k in scored[p["path"]]:
                        p[k] = scored[p["path"]][k]
        t["analysed"] = bool(best.get("analysed")) and paths <= {p["path"] for p in best.get("photos", [])}


def split(out_dir, trip_id):
    """Replace a trip by its parts (one per area visited). Returns False if it can't be split."""
    with LOCK:
        project = load_project(out_dir)
        idx = next(i for i, t in enumerate(project["trips"]) if t["id"] == trip_id)
        t = project["trips"][idx]
        parts = trips.split_trip(t)
        if not parts:
            return False
        for p in parts:
            trips.name_places(p)
            paths = {ph["path"] for ph in p["photos"]}
            p.update(include=t.get("include", True), notes="", analysed=t.get("analysed", False), edited=True,
                     selected=[s for s in t.get("selected", []) if s in paths],
                     events={k: v for k, v in (t.get("events") or {}).items() if k in paths},
                     captions={k: v for k, v in (t.get("captions") or {}).items() if k in paths},
                     stars=[s for s in t.get("stars", []) if s in paths],
                     parts=len(trips.segments(p)))
        project.setdefault("split_parents", {})[trip_id] = t
        project.setdefault("splits", []).append([t["start"], t["end"]])
        project["trips"][idx:idx + 1] = parts
        save_project(out_dir, project)
        return True


def merge(out_dir, parent_id):
    """Undo a split: put the whole trip back."""
    with LOCK:
        project = load_project(out_dir)
        parent = project.get("split_parents", {}).pop(parent_id, None)
        if parent is None:
            return False
        idx = [i for i, t in enumerate(project["trips"]) if t.get("parent") == parent_id]
        # Keep the work done on the parts: chosen photos, highlights, notes and photo scores.
        children = [project["trips"][i] for i in idx]
        scored = {p["path"]: p for c in children for p in c["photos"] if p.get("score", -1) >= 0}
        for p in parent["photos"]:
            for k in ("score", "people", "faces", "unsure"):
                if p["path"] in scored and k in scored[p["path"]]:
                    p[k] = scored[p["path"]][k]
        parent.update(include=any(c.get("include", True) for c in children),
                      analysed=all(c.get("analysed") for c in children), edited=True,
                      selected=[s for c in children for s in c.get("selected", [])],
                      events={k: v for c in children for k, v in (c.get("events") or {}).items()},
                      captions={k: v for c in children for k, v in (c.get("captions") or {}).items()},
                      stars=[s for c in children for s in c.get("stars", [])],
                      notes="\n".join(c["notes"] for c in children if c.get("notes")))
        project["trips"][idx[0]:idx[-1] + 1] = [parent]
        project["splits"] = [s for s in project.get("splits", []) if s != [parent["start"], parent["end"]]]
        save_project(out_dir, project)
        return True


def month_summary(photos, homes, found, min_km):
    """Photos per month, with and without a location, to help spot a trip that wasn't found."""
    months = {}
    for p in photos:
        m = months.setdefault(p["time"][:7], {"month": p["time"][:7], "photos": 0, "gps": 0, "away": 0, "trips": 0})
        m["photos"] += 1
        if p["lat"] is not None:
            m["gps"] += 1
            if homes and all(geo.haversine_km((p["lat"], p["lon"]), h) > min_km for h in homes):
                m["away"] += 1
    for t in found:
        if t["start"][:7] in months:
            months[t["start"][:7]]["trips"] += 1
    return sorted(months.values(), key=lambda m: m["month"], reverse=True)


def choose_photos(out_dir, progress=lambda pct, msg: None, cache_dir=None):
    """Score photos and pick a few for every ticked trip that hasn't been done yet."""
    project = load_project(out_dir)
    settings = project["settings"]
    cache_dir = cache_dir or os.path.join(out_dir, "cache")
    todo = [t for t in project["trips"] if t.get("include", True) and not t.get("analysed")]

    matcher = None
    if settings.get("people_dir") and todo:
        progress(2, "Learning faces from your reference photos")
        from .faces import FaceMatcher

        matcher = FaceMatcher(settings["people_dir"])
        project["people"], project["family"] = sorted(matcher.main), sorted(matcher.family)
        project["notes"] = [n for n in project.get("notes", []) if not n.startswith("No ")] + matcher.reference_problems

    store = Store(os.path.join(cache_dir, "photos.db"))
    for i, t in enumerate(todo):
        def trip_progress(done, total, i=i, t=t):
            pct = 5 + 93 * (i + done / max(total, 1)) / len(todo)
            progress(int(pct), f"Choosing photos for trip {i + 1} of {len(todo)}: {t['title']}")

        select.analyse_trip(t, matcher, store, trip_progress)
        if not t.get("selected"):
            select.pick(t, int(settings.get("photos_per_trip", 4)))
        t["analysed"] = True
        # Save as we go: the user may be editing other trips at the same time.
        with LOCK:
            latest = load_project(out_dir) or project
            for lt in latest["trips"]:
                if lt["id"] == t["id"]:
                    lt["photos"], lt["analysed"] = t["photos"], True
                    if not lt.get("selected"):
                        lt["selected"] = t["selected"]
            latest["people"], latest["notes"] = project["people"], project.get("notes", [])
            latest["family"] = project.get("family", [])
            save_project(out_dir, latest)
    progress(100, "Done")
    return load_project(out_dir)


def name_face(out_dir, cache_dir, path, box, name, family=False):
    """The user says a face PhotoStory wasn't sure about is `name` (or None: neither of you).
    Remember it, then re-check every analysed photo with what was learned."""
    from . import faces

    store = Store(os.path.join(cache_dir, "photos.db"))
    with LOCK:
        project = load_project(out_dir)
        photo = next((p for t in project["trips"] for p in t["photos"] if p["path"] == path), None)
        res = store.get(f"v2|{path}|{photo.get('w')}x{photo.get('h')}") if photo else None
        face = next((f for f in (res or {}).get("faces", []) if faces.box_key(path, f["box"]) == faces.box_key(path, box)), None)
        if face is None:
            return False
        faces.learn(path, face, name, family)
        matcher = faces.FaceMatcher(project["settings"]["people_dir"])
        project["people"], project["family"] = sorted(matcher.main), sorted(matcher.family)
        for t in project["trips"]:
            if t.get("analysed"):
                select.rescore(t, matcher, store)
        save_project(out_dir, project)
    return True


def recheck_faces(out_dir, cache_dir):
    """Re-check who is in each analysed photo, e.g. after adding reference photos."""
    from .faces import FaceMatcher

    store = Store(os.path.join(cache_dir, "photos.db"))
    with LOCK:
        project = load_project(out_dir)
        matcher = FaceMatcher(project["settings"]["people_dir"])
        project["people"], project["family"] = sorted(matcher.main), sorted(matcher.family)
        for t in project["trips"]:
            if t.get("analysed"):
                select.rescore(t, matcher, store)
        save_project(out_dir, project)


def write_stories(out_dir, trip_ids, overwrite, progress=lambda pct, msg: None):
    """Suggest captions for chosen photos and a story for each trip, from where photos were taken."""
    from . import story

    project = load_project(out_dir)
    todo = [t for t in project["trips"] if t["id"] in trip_ids]
    for i, t in enumerate(todo):
        def step(done, total, i=i, t=t):
            progress(int(100 * (i + done / max(total, 1)) / len(todo)), f"Looking up places for {t['title']}")

        text = story.suggest(t, step)
        with LOCK:
            latest = load_project(out_dir)
            for lt in latest["trips"]:
                if lt["id"] == t["id"]:
                    caps = lt.setdefault("captions", {})
                    for k, v in t.get("captions", {}).items():
                        caps.setdefault(k, v)
                    if overwrite or not (lt.get("notes") or "").strip():
                        lt["notes"] = text
                    lt["edited"] = True
            save_project(out_dir, latest)
    progress(100, "Done")
