"""A small local web app: choose folders and dates, review the trips and photos, make the PDF.

It only listens on this computer (127.0.0.1); nothing is uploaded anywhere.
"""
import hashlib
import json
import os
import queue
import threading
import traceback
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, geo, maps, pipeline, report, trips
from .images import jpeg_bytes, open_upright
from .util import load_json, save_json

STATIC = Path(__file__).with_name("static")
DEFAULTS = {"photos": "", "people_dir": "", "timeline": "", "date_from": "", "date_to": "", "home": "", "min_km": 50,
            "max_gap_hours": 36, "min_nights": 1, "photos_per_trip": 4, "title": ""}


class App:
    """Each story (a set of trips for one document) has its own folder under <out>/stories.
    Photo, face and thumbnail caches are shared by all stories, in <out>/cache."""

    def __init__(self, out_dir):
        self.root = out_dir
        self.cache_dir = os.path.join(out_dir, "cache")
        self.stories_dir = os.path.join(out_dir, "stories")
        self.state_path = os.path.join(out_dir, "app.json")
        self.job = {"running": False, "pct": 0, "msg": "", "error": ""}
        self.lock = threading.Lock()
        self.dialogs = queue.Queue()  # folder pickers must run on the main thread (macOS)
        self._migrate()
        current = load_json(self.state_path, {}).get("current")
        if not current or not os.path.isdir(os.path.join(self.stories_dir, current)):
            existing = self.stories()
            current = existing[0]["id"] if existing else self.new_story()
        self.open_story(current)

    def _migrate(self):
        """Before stories existed, the one project lived straight in the output folder."""
        if os.path.exists(os.path.join(self.root, "project.json")) and not os.path.isdir(self.stories_dir):
            dest = os.path.join(self.stories_dir, "story-1")
            os.makedirs(dest)
            for f in os.listdir(self.root):
                if f in ("project.json", "settings.json") or f.endswith(".pdf"):
                    os.replace(os.path.join(self.root, f), os.path.join(dest, f))

    @property
    def out_dir(self):
        return os.path.join(self.stories_dir, self.current)

    @property
    def settings_path(self):
        return os.path.join(self.out_dir, "settings.json")

    def open_story(self, story_id):
        self.current = story_id
        save_json(self.state_path, {"current": story_id})

    def new_story(self):
        os.makedirs(self.stories_dir, exist_ok=True)
        story_id = "story-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        os.makedirs(os.path.join(self.stories_dir, story_id), exist_ok=True)
        # Start from the last story's folders and home, which rarely change.
        last = getattr(self, "current", None)
        if last:
            prev = self.settings()
            save_json(os.path.join(self.stories_dir, story_id, "settings.json"),
                      {k: prev[k] for k in ("photos", "people_dir", "timeline", "home", "min_km", "min_nights",
                                            "photos_per_trip")})
        return story_id

    def stories(self):
        out = []
        if os.path.isdir(self.stories_dir):
            for sid in os.listdir(self.stories_dir):
                d = os.path.join(self.stories_dir, sid)
                if not os.path.isdir(d):
                    continue
                proj = load_json(os.path.join(d, "project.json"), None) or {}
                st = load_json(os.path.join(d, "settings.json"), {})
                ts = [t for t in proj.get("trips", []) if t.get("include", True)]
                out.append({"id": sid, "title": proj.get("title") or "", "trips": len(ts),
                            "from": st.get("date_from", ""), "to": st.get("date_to", ""),
                            "updated": os.path.getmtime(os.path.join(d, "project.json")) if proj else os.path.getmtime(d)})
        return sorted(out, key=lambda s: -s["updated"])

    def settings(self):
        return {**DEFAULTS, **load_json(self.settings_path, {})}

    def project(self):
        return pipeline.load_project(self.out_dir)

    def start_job(self, fn):
        with self.lock:
            if self.job["running"]:
                return False
            self.job = {"running": True, "pct": 0, "msg": "Starting", "error": ""}

        def work():
            try:
                fn()
            except Exception as e:
                traceback.print_exc()
                self.job["error"] = f"{type(e).__name__}: {e}"
            finally:
                self.job["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return True

    def start_scan(self, settings):
        save_json(self.settings_path, settings)
        out = self.out_dir
        return self.start_job(lambda: pipeline.run(settings, out, self._progress, self.cache_dir))

    def start_choose(self):
        out = self.out_dir
        return self.start_job(lambda: pipeline.choose_photos(out, self._progress, self.cache_dir))

    def _progress(self, pct, msg):
        self.job["pct"], self.job["msg"] = pct, msg

    def save_edits(self, edits):
        with pipeline.LOCK:
            return self._save_edits(edits)

    def _save_edits(self, edits):
        proj = self.project()
        if not proj:
            return None
        for k in ("title", "people", "cover_note", "cover_photo", "cover_map_view"):
            if k in edits:
                proj[k] = edits[k]
        by_id = {t["id"]: t for t in proj["trips"]}
        for e in edits.get("trips", []):
            t = by_id.get(e.get("id"))
            if not t:
                continue
            paths = {p["path"] for p in t["photos"]}
            if "title" in e and e["title"] != t.get("title"):
                t["title_edited"] = True
            for k in ("title", "notes", "include", "map_view", "hidden_stops", "closeups_off"):
                if k in e:
                    t[k] = e[k]
            if "selected" in e:
                t["selected"] = [s for s in e["selected"] if s in paths]
            if "events" in e:
                t["events"] = {k: v.strip()[:80] for k, v in e["events"].items() if k in paths and v.strip()}
            if "captions" in e:
                t["captions"] = {k: v.strip()[:100] for k, v in e["captions"].items() if k in paths and v.strip()}
            if "stars" in e:
                t["stars"] = [s for s in e["stars"] if s in paths]
            t["edited"] = True
        pipeline.save_project(self.out_dir, proj)
        return proj

    def build(self):
        proj = self.project()
        name = "Travel together " + datetime.now().strftime("%Y-%m-%d %H%M") + ".pdf"
        out = os.path.join(self.out_dir, name)
        report.build_pdf(proj, out)
        return out

    def known_photo(self, path):
        proj = self.project()
        return bool(proj) and any(p["path"] == path for t in proj["trips"] for p in t["photos"])

    def thumb(self, path, size):
        key = hashlib.sha1(f"{path}|{size}".encode()).hexdigest()
        cached = Path(self.cache_dir, "thumbs", key + ".jpg")
        if cached.exists():
            return cached.read_bytes()
        data = jpeg_bytes(open_upright(path, size), 80)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data

    def face_crop(self, path, box):
        from .faces import ANALYSE_SIDE

        img = open_upright(path, ANALYSE_SIDE)
        w, h = img.size
        x, y, bw, bh = box
        pad = 0.35
        crop = img.crop((max(0, int((x - bw * pad) * w)), max(0, int((y - bh * pad) * h)),
                         min(w, int((x + bw * (1 + pad)) * w)), min(h, int((y + bh * (1 + pad)) * h))))
        crop.thumbnail((200, 200))
        return jpeg_bytes(crop, 85)

    def pick_folder(self, start=""):
        reply = queue.Queue()
        self.dialogs.put((start, reply))
        return reply.get()


def _open_folder(folder):
    import subprocess
    import sys

    if sys.platform.startswith("win"):
        os.startfile(folder)  # noqa: S606 (opens Explorer)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


def _ask_directory(start):
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(initialdir=start or str(Path.home()), title="Choose a folder")
        root.destroy()
        return path or ""
    except Exception:
        return None  # no folder picker available; the page asks for the path to be typed


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            cache = "max-age=3600" if ctype == "image/jpeg" else "no-store"
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                return self.send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self.send(200, {"version": __version__, "settings": app.settings(), "job": app.job,
                                       "project": app.project(),
                                       "out_dir": app.out_dir, "story": app.current, "stories": app.stories()})
            if u.path == "/api/mapinfo":
                # A trip's places (which can be unticked) and its close-up maps, for the map panel.
                proj = app.project()
                t = next((t for t in (proj or {}).get("trips", []) if t["id"] == q.get("trip", [""])[0]), None)
                if not t:
                    return self.send(404, {"error": "unknown trip"})
                hidden = set(t.get("hidden_stops") or [])
                off = set(t.get("closeups_off") or [])
                stops = sorted(t["stops"], key=lambda s: s["first"])
                return self.send(200, {
                    "stops": [{"key": trips.stop_key(s), "count": s["count"], "first": s["first"], "last": s["last"],
                               "label": geo.place_label(s.get("place") or {}) or f"{s['lat']:.4f}, {s['lon']:.4f}",
                               "shown": trips.stop_key(s) not in hidden} for s in stops],
                    "closeups": [{"key": c["key"], "count": c["count"], "first": c["first"], "last": c["last"],
                                  "name": report.closeup_name(t, c), "include": c["key"] not in off}
                                 for c in trips.closeups(t)]})
            if u.path == "/api/job":
                return self.send(200, app.job)
            if u.path == "/thumb":
                path = q.get("p", [""])[0]
                size = min(int(q.get("s", ["360"])[0]), 1600)
                if not app.known_photo(path):
                    return self.send(404, {"error": "unknown photo"})
                try:
                    return self.send(200, app.thumb(path, size), "image/jpeg")
                except Exception as e:
                    return self.send(500, {"error": str(e)})
            if u.path in ("/map", "/api/mapview"):
                proj = app.project()
                ts = [t for t in proj["trips"] if t.get("include", True)]
                which = q.get("trip", [""])[0]
                if which == "cover":
                    spec, box = report.cover_map(ts), report.COVER_MAP_BOX
                else:
                    t = next((t for t in proj["trips"] if t["id"] == which), None)
                    if not t:
                        return self.send(404, {"error": "unknown trip"})
                    spec, box = report.trip_map(t), report.TRIP_MAP_BOX
                    key = q.get("closeup", [""])[0]
                    if key:
                        c = next((c for c in trips.closeups(t) if c["key"] == key), None)
                        if not c:
                            return self.send(404, {"error": "unknown close-up"})
                        spec, box = report.closeup_map(c), report.CLOSEUP_BOX
                if not spec["points"]:
                    return self.send(404, {"error": "no locations to map"})
                if u.path == "/api/mapview":
                    return self.send(200, {**maps.fit(spec["points"], report.map_px(box), spec["max_zoom"]),
                                           "width": report.map_px(box)[0], "height": report.map_px(box)[1]})
                view = None
                if q.get("z"):
                    view = {"zoom": int(q["z"][0]), "lat": float(q["lat"][0]), "lon": float(q["lon"][0])}
                img = report.render_map(spec, box, view)
                return self.send(200, jpeg_bytes(img, 85), "image/jpeg")
            if u.path == "/face":
                path = q.get("p", [""])[0]
                if not app.known_photo(path):
                    return self.send(404, {"error": "unknown photo"})
                try:
                    return self.send(200, app.face_crop(path, [float(v) for v in q.get("b", [""])[0].split(",")]),
                                     "image/jpeg")
                except Exception as e:
                    return self.send(500, {"error": str(e)})
            if u.path == "/pdf":
                path = q.get("p", [""])[0]
                if os.path.dirname(os.path.abspath(path)) != os.path.abspath(app.out_dir) or not path.endswith(".pdf"):
                    return self.send(404, {"error": "not found"})
                return self.send(200, Path(path).read_bytes(), "application/pdf")
            self.send(404, {"error": "not found"})

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            try:
                data = self.body()
                if u.path == "/api/scan":
                    s = {**DEFAULTS, **data}
                    if not os.path.isdir(s["photos"]):
                        return self.send(400, {"error": "That photo folder doesn't exist."})
                    if s["people_dir"] and not os.path.isdir(s["people_dir"]):
                        return self.send(400, {"error": "That reference photo folder doesn't exist."})
                    ok = app.start_scan(s)
                    return self.send(200 if ok else 409, app.job)
                if u.path == "/api/choose":
                    app.save_edits(data)
                    ok = app.start_choose()
                    return self.send(200 if ok else 409, app.job)
                if u.path == "/api/project":
                    return self.send(200, app.save_edits(data) or {})
                if u.path == "/api/build":
                    app.save_edits(data)
                    out = app.build()
                    return self.send(200, {"path": out, "url": "/pdf?p=" + urllib.parse.quote(out)})
                if u.path in ("/api/story/new", "/api/story/open"):
                    if app.job["running"]:
                        return self.send(409, {"error": "Please wait until the current step has finished."})
                    sid = app.new_story() if u.path.endswith("new") else data.get("id", "")
                    if not os.path.isdir(os.path.join(app.stories_dir, os.path.basename(sid))):
                        return self.send(404, {"error": "No such story"})
                    app.open_story(os.path.basename(sid))
                    return self.send(200, {"story": app.current})
                if u.path == "/api/write":
                    app.save_edits(data.get("edits", {}))
                    out, ids, over = app.out_dir, data.get("ids", []), bool(data.get("overwrite"))
                    ok = app.start_job(lambda: pipeline.write_stories(out, ids, over, app._progress))
                    return self.send(200 if ok else 409, app.job)
                if u.path == "/api/settings":
                    # Remember what's typed in the settings as it's typed, not only when trips are found.
                    s = {**app.settings(), **{k: v for k, v in data.items() if k in DEFAULTS}}
                    save_json(app.settings_path, s)
                    return self.send(200, {"ok": True})
                if u.path == "/api/place":
                    app.save_edits(data.get("edits", {}))
                    ok = pipeline.set_place(app.out_dir, data["id"], data.get("text", ""))
                    if not ok:
                        return self.send(400, {"error": f"Couldn't find “{data.get('text', '')}”. Try adding the country."})
                    return self.send(200, {"project": app.project()})
                if u.path == "/api/add-trip":
                    app.save_edits(data.get("edits", {}))
                    err = pipeline.add_trip(app.out_dir, app.cache_dir, data["from"], data["to"], data.get("place", ""))
                    return self.send(200, {"project": app.project(), "warning": err} if not err or err.startswith("Added")
                                     else {"error": err})
                if u.path == "/api/remove-trip":
                    pipeline.remove_added_trip(app.out_dir, data["id"])
                    return self.send(200, {"project": app.project()})
                if u.path == "/api/face":
                    ok = pipeline.name_face(app.out_dir, app.cache_dir, data["path"], data["box"], data.get("name"),
                                            bool(data.get("family")))
                    return self.send(200 if ok else 400, {"ok": ok, "project": app.project()})
                if u.path == "/api/forget":
                    from . import faces

                    app.save_edits(data.get("edits", {}))
                    faces.forget(data.get("name", ""))
                    pipeline.recheck_faces(app.out_dir, app.cache_dir)
                    return self.send(200, {"project": app.project()})
                if u.path == "/api/recheck":
                    app.save_edits(data.get("edits", {}))
                    pipeline.recheck_faces(app.out_dir, app.cache_dir)
                    return self.send(200, {"project": app.project()})
                if u.path == "/api/open-people":
                    folder = app.settings().get("people_dir")
                    if not folder or not os.path.isdir(folder):
                        return self.send(400, {"error": "Set the reference photos folder first."})
                    _open_folder(folder)
                    return self.send(200, {"ok": True})
                if u.path in ("/api/split", "/api/merge"):
                    app.save_edits(data.get("edits", {}))
                    fn = pipeline.split if u.path.endswith("split") else pipeline.merge
                    ok = fn(app.out_dir, data.get("id", ""))
                    return self.send(200 if ok else 400, {"ok": ok, "project": app.project()})
                if u.path == "/api/pick-folder":
                    path = app.pick_folder(data.get("start", ""))
                    return self.send(200, {"path": path, "available": path is not None})
            except Exception as e:
                traceback.print_exc()
                return self.send(500, {"error": f"{type(e).__name__}: {e}"})
            self.send(404, {"error": "not found"})

    return Handler


def serve(out_dir, port=8765, open_browser=True):
    os.makedirs(out_dir, exist_ok=True)
    app = App(out_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/"
    print(f"PhotoStory is running at {url}")
    print(f"Files are saved in {out_dir}")
    print("Press Ctrl+C here to stop it.")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        while True:
            try:
                start, reply = app.dialogs.get(timeout=0.5)
            except queue.Empty:
                continue
            reply.put(_ask_directory(start))
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()
