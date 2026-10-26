"""Recognise who is in a photo, using OpenCV's YuNet face detector and SFace face recogniser.

Reference photos live in a folder with one subfolder per person, e.g.
    people/Alex/*.jpg
    people/Sam/*.jpg
Five to ten clear photos of each person, from different years and angles, work best.

What has been learned is remembered in ~/.photostory/faces: the reference photos' face data (so they
aren't analysed again), and faces you've confirmed or rejected when PhotoStory wasn't sure.
"""
import base64
import os
import urllib.request

import numpy as np

from .images import open_upright
from .scan import PHOTO_EXTS
from .util import APP_DIR, USER_AGENT, load_json, save_json

MODELS = {
    "detector": ("face_detection_yunet_2023mar.onnx",
                 "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    "recognizer": ("face_recognition_sface_2021dec.onnx",
                   "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"),
}
MATCH_THRESHOLD = 0.363  # cosine similarity suggested by the SFace authors
UNSURE_FROM = 0.26  # below the threshold but close enough to ask about
ANALYSE_SIDE = 1280
FACES_DIR = APP_DIR / "faces"


def encode(feat):
    return base64.b64encode(np.asarray(feat, np.float16).tobytes()).decode()


def decode(s):
    return np.frombuffer(base64.b64decode(s), np.float16).astype(np.float32)


def box_key(path, box):
    return path + "|" + ",".join(f"{v:.3f}" for v in box)


def _model_path(name):
    fname, url = MODELS[name]
    path = APP_DIR / "models" / fname
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r, open(str(path) + ".part", "wb") as f:
            f.write(r.read())
        os.replace(str(path) + ".part", path)
    return str(path)


class FaceMatcher:
    def __init__(self, people_dir):
        import cv2

        self.cv2 = cv2
        self.people_dir = people_dir
        self.detector = cv2.FaceDetectorYN.create(_model_path("detector"), "", (320, 320), 0.8, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(_model_path("recognizer"), "")
        self.people = {}  # name -> list of feature vectors
        self.main = []  # the couple: one reference folder each
        self.family = []  # other important people, named while answering "Is this ...?"
        self.reference_problems = []
        self.learned = load_json(FACES_DIR / "learned.json", {"people": {}, "rejected": []})
        self.rejected = set(self.learned.get("rejected", []))

        ref_cache = load_json(FACES_DIR / "references.json", {})
        for name in sorted(os.listdir(people_dir)):
            d = os.path.join(people_dir, name)
            if not os.path.isdir(d) or name.startswith("."):
                continue
            feats = []
            for f in sorted(os.listdir(d)):
                if os.path.splitext(f)[1].lower() not in PHOTO_EXTS:
                    continue
                path = os.path.join(d, f)
                st = os.stat(path)
                key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
                if key not in ref_cache:
                    faces = self.faces(path)
                    # A reference photo should show that person on their own, so use the biggest face.
                    ref_cache[key] = encode(max(faces, key=lambda x: x["area"])["feature"]) if faces else ""
                if ref_cache[key]:
                    feats.append(decode(ref_cache[key]))
                else:
                    self.reference_problems.append(f"No face found in reference photo {name}/{f}")
            feats += [decode(x["f"]) for x in self.learned["people"].get(name, [])]
            if feats:
                self.people[name] = feats
                self.main.append(name)
            else:
                self.reference_problems.append(f"No usable reference photos for {name}")
        save_json(FACES_DIR / "references.json", ref_cache)
        for name in self.learned.get("family", []):
            feats = [decode(x["f"]) for x in self.learned["people"].get(name, [])]
            if feats and name not in self.people:
                self.people[name] = feats
                self.family.append(name)

    def faces(self, path):
        """Detect faces; return [{"box", "area" (fraction of image), "score", "feature"}]."""
        cv2 = self.cv2
        try:
            img = open_upright(path, ANALYSE_SIDE)
        except Exception:
            return []
        bgr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        self.detector.setInputSize((w, h))
        _, found = self.detector.detect(bgr)
        out = []
        if found is None:
            return out
        for row in found:
            x, y, fw, fh = row[:4]
            if fw * fh < 0.0004 * w * h:  # ignore tiny faces in the background
                continue
            aligned = self.recognizer.alignCrop(bgr, row)
            feat = self.recognizer.feature(aligned).flatten()
            feat = feat / (np.linalg.norm(feat) or 1.0)
            out.append({"box": [round(float(v), 4) for v in (x / w, y / h, fw / w, fh / h)],
                        "area": float(fw * fh / (w * h)), "score": float(row[-1]), "feature": feat})
        return out

    def detect(self, path):
        """Faces in a photo, in the form kept in the cache."""
        return [{"box": f["box"], "area": round(f["area"], 5), "f": encode(f["feature"])} for f in self.faces(path)]

    def identify(self, path, faces):
        """From cached faces: {"people": {name: face_area}, "unsure": [{"box", "name", "sim"}]}."""
        found, unsure = {}, []
        for face in faces:
            feat = decode(face["f"])
            sims = {name: max(float(np.dot(feat, r)) for r in refs) for name, refs in self.people.items()}
            if not sims:
                continue
            name, sim = max(sims.items(), key=lambda kv: kv[1])
            if sim >= MATCH_THRESHOLD:
                if face["area"] > found.get(name, 0):
                    found[name] = face["area"]
            elif sim >= UNSURE_FROM and face["area"] > 0.003 and box_key(path, face["box"]) not in self.rejected:
                unsure.append({"box": face["box"], "name": name, "sim": round(sim, 3)})
        return {"people": found, "unsure": unsure}


def learn(path, face, name, family=False):
    """Remember a face the user has named (name=None: nobody to recognise).
    family=True adds someone important besides the two of you, e.g. a partner's children."""
    data = load_json(FACES_DIR / "learned.json", {"people": {}, "rejected": []})
    key = box_key(path, face["box"])
    if name:
        data["people"].setdefault(name, []).append({"f": face["f"], "from": key})
        if family and name not in data.setdefault("family", []):
            data["family"].append(name)
    if key not in data["rejected"]:
        data["rejected"].append(key)  # either way, don't ask about this face again
    save_json(FACES_DIR / "learned.json", data)


def forget(name):
    """Stop recognising someone added as family."""
    data = load_json(FACES_DIR / "learned.json", {"people": {}, "rejected": []})
    data["people"].pop(name, None)
    data["family"] = [n for n in data.get("family", []) if n != name]
    save_json(FACES_DIR / "learned.json", data)
