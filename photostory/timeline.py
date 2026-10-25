"""Use Google Maps Timeline as a backup location for photos that have no GPS of their own.

Reads any of Google's location history exports:
  * Timeline.json, exported on the phone (Android: Settings > Location > Location services >
    Timeline > Export Timeline data; iPhone: Google Maps > Your Timeline > Settings > Export)
  * Records.json from Google Takeout (older)
  * the "Semantic Location History" folder from Google Takeout (older, one file per month)
A photo is matched to where you were at the moment it was taken.
"""
import bisect
import json
import os
import re
from datetime import datetime, timedelta, timezone

from .util import load_json, save_json

MAX_GAP = timedelta(minutes=60)  # how far a location fix may be from the photo's time

_LATLNG = re.compile(r"(-?\d+(?:\.\d+)?)°?,\s*(-?\d+(?:\.\d+)?)°?")


def _ll(value):
    """A position in any of the formats Google uses."""
    if isinstance(value, dict):
        if "latitudeE7" in value:
            return value["latitudeE7"] / 1e7, value["longitudeE7"] / 1e7
        for k in ("latLng", "LatLng", "point", "placeLocation"):
            if k in value:
                return _ll(value[k])
        return None
    if isinstance(value, str):
        m = _LATLNG.search(value.replace("geo:", ""))
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def _time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc)
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


class Timeline:
    def __init__(self):
        self.visits = []  # (start, end, lat, lon), UTC datetimes
        self.points = []  # (time, lat, lon)

    def add_visit(self, start, end, ll):
        start, end = _time(start), _time(end)
        if start and end and ll:
            self.visits.append((start, end, *ll))

    def add_point(self, t, ll):
        t = _time(t)
        if t and ll:
            self.points.append((t, *ll))

    def read(self, data):
        if isinstance(data, dict) and "semanticSegments" in data:  # Timeline.json from an Android phone
            data = data["semanticSegments"] + [{"rawSignal": s} for s in data.get("rawSignals", [])]
        if isinstance(data, dict) and "locations" in data:  # Takeout Records.json
            for loc in data["locations"]:
                self.add_point(loc.get("timestamp") or loc.get("timestampMs"), _ll(loc))
            return
        if isinstance(data, dict) and "timelineObjects" in data:  # Takeout Semantic Location History
            for obj in data["timelineObjects"]:
                if "placeVisit" in obj:
                    v = obj["placeVisit"]
                    d = v.get("duration", {})
                    self.add_visit(d.get("startTimestamp") or d.get("startTimestampMs"),
                                   d.get("endTimestamp") or d.get("endTimestampMs"), _ll(v.get("location", {})))
                elif "activitySegment" in obj:
                    a = obj["activitySegment"]
                    d = a.get("duration", {})
                    self.add_point(d.get("startTimestamp") or d.get("startTimestampMs"), _ll(a.get("startLocation", {})))
                    self.add_point(d.get("endTimestamp") or d.get("endTimestampMs"), _ll(a.get("endLocation", {})))
            return
        if isinstance(data, list):  # segments: Timeline.json (Android) or location-history.json (iPhone)
            for seg in data:
                if not isinstance(seg, dict):
                    continue
                start, end = seg.get("startTime"), seg.get("endTime")
                if "visit" in seg:
                    self.add_visit(start, end, _ll(seg["visit"].get("topCandidate", {})))
                if "activity" in seg:
                    self.add_point(start, _ll(seg["activity"].get("start")))
                    self.add_point(end, _ll(seg["activity"].get("end")))
                for p in seg.get("timelinePath", []) or []:
                    t = p.get("time")
                    if t is None and "durationMinutesOffsetFromStartTime" in p and _time(start):
                        t = (_time(start) + timedelta(minutes=int(p["durationMinutesOffsetFromStartTime"]))).isoformat()
                    self.add_point(t, _ll(p.get("point")))
                if "rawSignal" in seg:
                    pos = seg["rawSignal"].get("position") or {}
                    self.add_point(pos.get("timestamp"), _ll(pos))

    def finish(self):
        self.visits.sort()
        self.points.sort()
        self._vstarts = [v[0] for v in self.visits]
        self._ptimes = [p[0] for p in self.points]

    def __len__(self):
        return len(self.visits) + len(self.points)

    def where(self, t):
        """(lat, lon) at UTC time t, or None if Timeline doesn't know."""
        i = bisect.bisect_right(self._vstarts, t) - 1
        for j in (i, i - 1):
            if 0 <= j < len(self.visits) and self.visits[j][0] <= t <= self.visits[j][1]:
                return self.visits[j][2], self.visits[j][3]
        best = None
        k = bisect.bisect_left(self._ptimes, t)
        for j in (k - 1, k):
            if 0 <= j < len(self.points):
                gap = abs(self.points[j][0] - t)
                if gap <= MAX_GAP and (best is None or gap < best[0]):
                    best = (gap, self.points[j][1], self.points[j][2])
        if best is None:
            # A visit that ended or started just before or after the photo.
            for j in (i, i + 1):
                if 0 <= j < len(self.visits):
                    s, e, lat, lon = self.visits[j]
                    if s - MAX_GAP <= t <= e + MAX_GAP:
                        return lat, lon
            return None
        return best[1], best[2]


def _files(paths):
    for path in paths:
        path = path.strip().strip('"')
        if not path:
            continue
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for f in sorted(files):
                    if f.lower().endswith(".json"):
                        yield os.path.join(root, f)
        elif os.path.isfile(path):
            yield path


def load(paths, cache_dir):
    """Read Timeline exports (files or folders). Parsed positions are cached, as exports can be large."""
    tl = Timeline()
    cache_path = os.path.join(cache_dir, "timeline.json")
    cache = load_json(cache_path, {})
    fresh = {}
    for f in _files(paths):
        st = os.stat(f)
        key = f"{f}|{st.st_size}|{int(st.st_mtime)}"
        if key in cache:
            entry = cache[key]
        else:
            part = Timeline()
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    part.read(json.load(fh))
            except (OSError, ValueError):
                continue
            entry = {"v": [(s.isoformat(), e.isoformat(), a, b) for s, e, a, b in part.visits],
                     "p": [(t.isoformat(), a, b) for t, a, b in part.points]}
        fresh[key] = entry
        for s, e, a, b in entry["v"]:
            tl.visits.append((datetime.fromisoformat(s), datetime.fromisoformat(e), a, b))
        for t, a, b in entry["p"]:
            tl.points.append((datetime.fromisoformat(t), a, b))
    save_json(cache_path, fresh)
    tl.finish()
    return tl


def photo_utc(p, fallback_tz):
    """When a photo was taken, in UTC. Uses the photo's time zone, else the given fallback (e.g. home's)."""
    tz = p.get("tz") or fallback_tz
    if not tz:
        return None
    m = re.match(r"([+-])(\d\d):?(\d\d)", tz)
    if not m:
        return None
    offset = timedelta(hours=int(m.group(2)), minutes=int(m.group(3))) * (1 if m.group(1) == "+" else -1)
    return datetime.fromisoformat(p["time"]).replace(tzinfo=timezone(offset)).astimezone(timezone.utc)


def fill_locations(photos, tl, fallback_tz=None):
    """Give photos without GPS the location Timeline has for that moment. Returns how many were filled."""
    n = 0
    for p in photos:
        if p["lat"] is not None:
            continue
        t = photo_utc(p, fallback_tz)
        ll = tl.where(t) if t else None
        if ll:
            p["lat"], p["lon"] = round(ll[0], 6), round(ll[1], 6)
            p["loc_src"] = "timeline"
            n += 1
    return n
