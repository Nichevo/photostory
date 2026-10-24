"""Recognise who is in a photo, using OpenCV's YuNet face detector and SFace face recogniser.

Reference photos live in a folder with one subfolder per person, e.g.
    people/Alex/*.jpg
    people/Sam/*.jpg
Five to ten clear photos of each person, from different years and angles, work best.
"""
import os
import urllib.request

import numpy as np

from .images import open_upright
from .scan import PHOTO_EXTS
from .util import APP_DIR, USER_AGENT

MODELS = {
    "detector": ("face_detection_yunet_2023mar.onnx",
                 "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    "recognizer": ("face_recognition_sface_2021dec.onnx",
                   "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"),
}
MATCH_THRESHOLD = 0.363  # cosine similarity suggested by the SFace authors
ANALYSE_SIDE = 1280


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
        self.detector = cv2.FaceDetectorYN.create(_model_path("detector"), "", (320, 320), 0.8, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(_model_path("recognizer"), "")
        self.people = {}  # name -> list of feature vectors
        self.reference_problems = []
        for name in sorted(os.listdir(people_dir)):
            d = os.path.join(people_dir, name)
            if not os.path.isdir(d) or name.startswith("."):
                continue
            feats = []
            for f in sorted(os.listdir(d)):
                if os.path.splitext(f)[1].lower() not in PHOTO_EXTS:
                    continue
                faces = self.faces(os.path.join(d, f))
                if not faces:
                    self.reference_problems.append(f"No face found in {name}/{f}")
                    continue
                # A reference photo should show that person on their own, so use the biggest face.
                feats.append(max(faces, key=lambda x: x["area"])["feature"])
            if feats:
                self.people[name] = feats
            else:
                self.reference_problems.append(f"No usable reference photos for {name}")

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
            out.append({"box": [float(x / w), float(y / h), float(fw / w), float(fh / h)],
                        "area": float(fw * fh / (w * h)), "score": float(row[-1]), "feature": feat})
        return out

    def identify(self, path):
        """Return {"faces": n, "people": {name: face_area}} for one photo."""
        faces = self.faces(path)
        found = {}
        for f in faces:
            best_name, best_sim = None, MATCH_THRESHOLD
            for name, feats in self.people.items():
                sim = max(float(np.dot(f["feature"], r)) for r in feats)
                if sim >= best_sim:
                    best_name, best_sim = name, sim
            if best_name and f["area"] > found.get(best_name, 0):
                found[best_name] = f["area"]
        return {"faces": len(faces), "people": found}
