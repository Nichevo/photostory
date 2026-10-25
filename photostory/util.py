import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("PHOTOSTORY_HOME", Path.home() / ".photostory"))
USER_AGENT = "photostory-evidence/0.1 (personal photo report tool)"


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


class Store:
    """A small key-value store in SQLite, for caches too big to rewrite as JSON on every change."""

    def __init__(self, path):
        import sqlite3

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")

    def get(self, key, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))

    def keys(self, prefix):
        return [r[0] for r in self.db.execute("SELECT k FROM kv WHERE k >= ? AND k < ?", (prefix, prefix + "￿"))]

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()
