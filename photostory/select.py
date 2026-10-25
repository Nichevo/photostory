"""Score each trip's photos and pick a few that show both people, spread across the trip."""
from datetime import datetime

from .images import open_upright, sharpness
from .util import load_json, save_json

MAX_ANALYSED_PER_TRIP = 800


def _even_sample(items, n):
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def analyse_trip(trip, matcher, cache_path, progress=None):
    """Fill in "score", "people" and "faces" for a sample of the trip's photos."""
    cache = load_json(cache_path, {})
    sig = ",".join(sorted(matcher.people)) if matcher else "-"
    # Skip burst shots (several photos within a few seconds) so the time goes on different moments.
    distinct, last = [], None
    for p in trip["photos"]:
        t = datetime.fromisoformat(p["time"])
        if last is None or (t - last).total_seconds() > 3:
            distinct.append(p)
        last = t
    sample = _even_sample(distinct, MAX_ANALYSED_PER_TRIP)
    for n, p in enumerate(sample, 1):
        key = f"{sig}|{p['path']}|{p.get('w')}x{p.get('h')}"
        res = cache.get(key)
        if res is None:
            res = {"people": {}, "faces": 0, "sharp": 0.0}
            try:
                res["sharp"] = round(sharpness(open_upright(p["path"], 1024)), 1)
            except Exception:
                pass
            if matcher:
                res.update(matcher.identify(p["path"]))
            cache[key] = res
            if n % 20 == 0:
                save_json(cache_path, cache)
        p["people"] = sorted(res["people"])
        p["faces"] = res["faces"]
        p["score"] = round(_score(p, res, matcher), 1)
        if progress:
            progress(n, len(sample))
    save_json(cache_path, cache)
    for p in trip["photos"]:
        p.setdefault("score", -1.0)


def _score(p, res, matcher):
    score = 0.0
    if matcher:
        wanted = len(matcher.people)
        present = len(res["people"])
        if wanted and present == wanted:
            score += 100
        elif present:
            score += 35 * present / max(wanted, 1)
        # Bigger faces read better on a printed page.
        score += 20 * min(1.0, sum(res["people"].values()) * 12)
    score += min(10.0, res.get("sharp", 0) / 60)
    if p.get("lat") is not None:
        score += 5
    if p.get("w", 0) >= p.get("h", 0):
        score += 2
    return score


def pick(trip, k=4):
    """Choose k photos: highest scores, but avoid several from the same hour or day."""
    cands = [p for p in trip["photos"] if p.get("score", -1) >= 0]
    chosen = []
    while cands and len(chosen) < k:
        def adjusted(p):
            t = datetime.fromisoformat(p["time"])
            pen = 0.0
            for c in chosen:
                ct = datetime.fromisoformat(c["time"])
                if abs((t - ct).total_seconds()) < 3600:
                    pen += 60
                elif t.date() == ct.date():
                    pen += 15
                if p.get("stop") is not None and p.get("stop") == c.get("stop"):
                    pen += 8
            return p["score"] - pen

        best = max(cands, key=adjusted)
        chosen.append(best)
        cands.remove(best)
    chosen.sort(key=lambda p: p["time"])
    trip["selected"] = [p["path"] for p in chosen]
