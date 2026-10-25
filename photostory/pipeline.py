"""Turn a folder of photos into a project file of trips with suggested photos."""
import os
import threading
from datetime import date

from . import geo, select, trips
from .scan import scan_folder
from .util import load_json, save_json


# Held while reading-changing-writing the project file, since photo picking and the user's edits overlap.
LOCK = threading.RLock()


def project_path(out_dir):
    return os.path.join(out_dir, "project.json")


def load_project(out_dir):
    return load_json(project_path(out_dir), None)


def save_project(out_dir, project):
    save_json(project_path(out_dir), project)


def run(settings, out_dir, progress=lambda pct, msg: None):
    """settings: photos, people_dir, date_from, date_to (YYYY-MM-DD), home (text, one place per line),
    min_km, max_gap_hours, min_nights, photos_per_trip, title."""
    os.makedirs(out_dir, exist_ok=True)
    cache_dir = os.path.join(out_dir, "cache")
    d_from = date.fromisoformat(settings["date_from"]) if settings.get("date_from") else None
    d_to = date.fromisoformat(settings["date_to"]) if settings.get("date_to") else None

    def scan_progress(done, total, msg):
        progress(int(40 * done / max(total, 1)), f"{msg}: {done} of {total}")

    photos = scan_folder(settings["photos"], os.path.join(cache_dir, "scan.json"), d_from, d_to, scan_progress)
    with_gps = sum(1 for p in photos if p["lat"] is not None)
    progress(40, f"Found {len(photos)} photos in the date range ({with_gps} with a location)")

    homes, notes = [], []
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

    for t in found:
        t["include"] = True
        t["notes"] = ""
        t["selected"] = []
        t["analysed"] = False

    # Keep edits from an earlier run of the same trips (same start time), so re-scanning is safe.
    old = load_project(out_dir)
    if old:
        prev = {t["start"]: t for t in old.get("trips", [])}
        for t in found:
            o = prev.get(t["start"])
            if o and o.get("edited"):
                for k in ("title", "notes", "include", "selected", "edited"):
                    if k in o:
                        t[k] = o[k]

    project = {
        "version": 1,
        "settings": settings,
        "title": settings.get("title") or "",
        "people": [],
        "homes": homes,
        "notes": notes,
        "photo_total": len(photos),
        "trips": found,
    }
    save_project(out_dir, project)
    progress(100, "Done")
    return project


def choose_photos(out_dir, progress=lambda pct, msg: None):
    """Score photos and pick a few for every ticked trip that hasn't been done yet."""
    project = load_project(out_dir)
    settings = project["settings"]
    cache_dir = os.path.join(out_dir, "cache")
    todo = [t for t in project["trips"] if t.get("include", True) and not t.get("analysed")]

    matcher = None
    if settings.get("people_dir") and todo:
        progress(2, "Learning faces from your reference photos")
        from .faces import FaceMatcher

        matcher = FaceMatcher(settings["people_dir"])
        project["people"] = sorted(matcher.people)
        project["notes"] = [n for n in project.get("notes", []) if not n.startswith("No ")] + matcher.reference_problems

    for i, t in enumerate(todo):
        def trip_progress(done, total, i=i, t=t):
            pct = 5 + 93 * (i + done / max(total, 1)) / len(todo)
            progress(int(pct), f"Choosing photos for trip {i + 1} of {len(todo)}: {t['title']}")

        select.analyse_trip(t, matcher, os.path.join(cache_dir, "faces.json"), trip_progress)
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
            save_project(out_dir, latest)
    progress(100, "Done")
    return load_project(out_dir)
