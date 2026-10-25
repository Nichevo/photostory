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

from . import pipeline, report
from .images import jpeg_bytes, open_upright
from .util import load_json, save_json

STATIC = Path(__file__).with_name("static")
DEFAULTS = {"photos": "", "people_dir": "", "date_from": "", "date_to": "", "home": "", "min_km": 50,
            "max_gap_hours": 36, "min_nights": 1, "photos_per_trip": 4, "title": ""}


class App:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.settings_path = os.path.join(out_dir, "settings.json")
        self.job = {"running": False, "pct": 0, "msg": "", "error": ""}
        self.lock = threading.Lock()
        self.dialogs = queue.Queue()  # folder pickers must run on the main thread (macOS)

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
        return self.start_job(lambda: pipeline.run(settings, self.out_dir, self._progress))

    def start_choose(self):
        return self.start_job(lambda: pipeline.choose_photos(self.out_dir, self._progress))

    def _progress(self, pct, msg):
        self.job["pct"], self.job["msg"] = pct, msg

    def save_edits(self, edits):
        with pipeline.LOCK:
            return self._save_edits(edits)

    def _save_edits(self, edits):
        proj = self.project()
        if not proj:
            return None
        for k in ("title", "people", "cover_note"):
            if k in edits:
                proj[k] = edits[k]
        by_id = {t["id"]: t for t in proj["trips"]}
        for e in edits.get("trips", []):
            t = by_id.get(e.get("id"))
            if not t:
                continue
            paths = {p["path"] for p in t["photos"]}
            for k in ("title", "notes", "include"):
                if k in e:
                    t[k] = e[k]
            if "selected" in e:
                t["selected"] = [s for s in e["selected"] if s in paths]
            if "events" in e:
                t["events"] = {k: v.strip()[:80] for k, v in e["events"].items() if k in paths and v.strip()}
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
        cached = Path(self.out_dir, "cache", "thumbs", key + ".jpg")
        if cached.exists():
            return cached.read_bytes()
        data = jpeg_bytes(open_upright(path, size), 80)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data

    def pick_folder(self, start=""):
        reply = queue.Queue()
        self.dialogs.put((start, reply))
        return reply.get()


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
            self.send_header("Cache-Control", "no-store" if ctype == "application/json" else "max-age=3600")
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
                return self.send(200, {"settings": app.settings(), "job": app.job, "project": app.project(),
                                       "out_dir": app.out_dir})
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
