"""Progress since a moment in time: the game's stats files compared with saved copies ("marks").

Marks are taken at the season start (the admin Reset), every day start and every week start (Monday),
all at local midnight in Europe/London. Progress for a period = current totals minus the period's mark.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
CATEGORIES = ("custom", "mined", "crafted", "used", "broken", "picked_up", "dropped", "killed", "killed_by")

SCHEMA = """
CREATE TABLE IF NOT EXISTS marks (name TEXT NOT NULL, uuid TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (name, uuid));
CREATE TABLE IF NOT EXISTS mark_names (name TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS instances (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, period TEXT NOT NULL,
  data TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS completions (iid INTEGER NOT NULL, uuid TEXT NOT NULL, at REAL NOT NULL, seq INTEGER NOT NULL,
  PRIMARY KEY (iid, uuid));
CREATE TABLE IF NOT EXISTS claims (key TEXT NOT NULL, uuid TEXT NOT NULL, state TEXT NOT NULL, at REAL NOT NULL,
  name TEXT NOT NULL DEFAULT '', PRIMARY KEY (key, uuid));
CREATE TABLE IF NOT EXISTS hall (week TEXT NOT NULL, board TEXT NOT NULL, rank INTEGER NOT NULL, uuid TEXT NOT NULL,
  value REAL NOT NULL, PRIMARY KEY (week, board, rank));
"""


def flatten(raw: dict) -> dict:
    out = {}
    for cat, values in (raw.get("stats") or {}).items():
        short = cat.removeprefix("minecraft:")
        for key, n in (values or {}).items():
            try:
                out[f"{short}:{key}"] = int(n)
            except (TypeError, ValueError):
                continue
    return out


def read_totals(server: Path, world: str = "world") -> dict:
    out = {}
    for path in sorted((server / world / "players" / "stats").glob("*.json")):
        try:
            out[path.stem] = flatten(json.loads(path.read_text()))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return out


def metric_value(flat: dict, metric: dict) -> int:
    prefix = metric["cat"] + ":"
    patterns = metric["keys"]
    return sum(n for k, n in flat.items()
               if k.startswith(prefix) and any(fnmatchcase(k[len(prefix):], p) for p in patterns))


def delta(now: dict, mark: dict | None) -> dict:
    mark = mark or {}
    return {k: max(0, n - mark.get(k, 0)) for k, n in now.items()}


# ---- periods (local midnight in London)
def _local(t: float) -> datetime:
    return datetime.fromtimestamp(t, LONDON)


def _midnight(d) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=LONDON).timestamp()


def day_start(t: float) -> float:
    return _midnight(_local(t).date())


def next_day(t: float) -> float:
    return _midnight(_local(t).date() + timedelta(days=1))


def week_start(t: float) -> float:
    d = _local(t).date()
    return _midnight(d - timedelta(days=d.weekday()))


def next_week(t: float) -> float:
    d = _local(t).date()
    return _midnight(d + timedelta(days=7 - d.weekday()))


def day_name(t: float) -> str:
    return _local(t).date().isoformat()


def week_name(t: float) -> str:
    return _local(week_start(t)).date().isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        with self._lock:
            c = sqlite3.connect(self.path, timeout=10)
            c.row_factory = sqlite3.Row
            try:
                with c:
                    yield c
            finally:
                c.close()

    def save_mark(self, name: str, totals: dict, at: float = 0.0) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM marks WHERE name=?", (name,))
            c.executemany("INSERT INTO marks (name, uuid, data) VALUES (?,?,?)",
                          [(name, u, json.dumps(flat)) for u, flat in totals.items()])
            c.execute("INSERT OR REPLACE INTO mark_names (name, at) VALUES (?,?)", (name, at))

    def mark(self, name: str) -> dict | None:
        with self.conn() as c:
            if c.execute("SELECT 1 FROM mark_names WHERE name=?", (name,)).fetchone() is None:
                return None
            return {r["uuid"]: json.loads(r["data"]) for r in c.execute("SELECT uuid, data FROM marks WHERE name=?", (name,))}

    def get(self, key: str, default=None):
        with self.conn() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if r is None else json.loads(r["value"])

    def put(self, key: str, value) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, json.dumps(value)))


def progress(totals: dict, store: Store, mark_name: str, metric: dict) -> dict:
    mark = store.mark(mark_name) or {}
    return {u: metric_value(delta(flat, mark.get(u)), metric) for u, flat in totals.items()}
