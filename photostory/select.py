"""Score each trip's photos and pick a few that show both people, spread across the trip."""
from datetime import datetime

from .images import open_upright, sharpness

MAX_ANALYSED_PER_TRIP = 800


def _even_sample(items, n):
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def analyse_trip(trip, matcher, store, progress=None):
    """Fill in "score", "people", "faces" and "unsure" for a sample of the trip's photos.

    store is a util.Store; each photo's sharpness and face data are cached there, so naming a face
    later only needs a quick re-score, not another look at every photo."""
    # Skip burst shots (several photos within a few seconds) so the time goes on different moments.
    distinct, last = [], None
    for p in trip["photos"]:
        t = datetime.fromisoformat(p["time"])
        if last is None or (t - last).total_seconds() > 3:
            distinct.append(p)
        last = t
    sample = _even_sample(distinct, MAX_ANALYSED_PER_TRIP)
    for n, p in enumerate(sample, 1):
        key = f"v2|{p['path']}|{p.get('w')}x{p.get('h')}"
        res = store.get(key)
        if res is None or (matcher and not res.get("detected")):
            res = res or {"sharp": 0.0, "faces": []}
            if not res.get("sharp"):
                try:
                    res["sharp"] = round(sharpness(open_upright(p["path"], 1024)), 1)
                except Exception:
                    pass
            if matcher:
                res["faces"] = matcher.detect(p["path"])
                res["detected"] = True
            store.put(key, res)
            if n % 25 == 0:
                store.commit()
        apply(p, res, matcher)
        if progress:
            progress(n, len(sample))
    store.commit()
    for p in trip["photos"]:
        p.setdefault("score", -1.0)


def apply(p, res, matcher):
    ident = matcher.identify(p["path"], res.get("faces", [])) if matcher else {"people": {}, "unsure": []}
    p["people"] = sorted(ident["people"])
    p["faces"] = len(res.get("faces", []))
    p["unsure"] = ident["unsure"]
    p["score"] = round(_score(p, ident["people"], res.get("sharp", 0), matcher), 1)


def rescore(trip, matcher, store):
    """Re-check who is in each analysed photo, e.g. after a face was named."""
    for p in trip["photos"]:
        if p.get("score", -1) >= 0:
            res = store.get(f"v2|{p['path']}|{p.get('w')}x{p.get('h')}")
            if res is not None:
                apply(p, res, matcher)


def _score(p, people, sharp, matcher):
    score = 0.0
    if matcher:
        wanted = len(matcher.main)
        present = sum(1 for n in people if n in matcher.main)
        family = sum(1 for n in people if n in matcher.family)
        if wanted and present == wanted:
            score += 100
            # Together with family (e.g. a family dinner) is especially good evidence.
            score += 25 if family else 0
        elif present:
            score += 35 * present / max(wanted, 1) + (10 if family else 0)
        # Bigger faces read better on a printed page.
        score += 20 * min(1.0, sum(a for n, a in people.items() if n in matcher.main) * 12)
    score += min(10.0, sharp / 60)
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
