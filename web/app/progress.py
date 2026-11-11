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


def read_totals(server: Path, world: str = "world", failed: list | None = None) -> dict:
    """uuid -> flat totals. Unreadable files (e.g. half written by a save) are skipped and listed in `failed`."""
    out = {}
    for path in sorted((server / world / "players" / "stats").glob("*.json")):
        try:
            out[path.stem] = flatten(json.loads(path.read_text()))
        except (OSError, ValueError, AttributeError, TypeError):
            if failed is not None:
                failed.append(path.stem)
    return out


def metric_value(flat: dict, metric: dict) -> int:
    prefix = metric["cat"] + ":"
    patterns = metric["keys"]
    return sum(n for k, n in flat.items()
               if k.startswith(prefix) and any(fnmatchcase(k[len(prefix):], p) for p in patterns))


def scale(metric: dict) -> int:
    """Raw stat units per shown unit: distance is stored in cm (shown in blocks), play time in ticks (hours)."""
    keys = metric.get("keys", [])
    if metric.get("cat") == "custom" and keys and all(k.endswith("_one_cm") for k in keys):
        return 100
    if metric.get("cat") == "custom" and keys and all(k.endswith(("play_time", "sneak_time")) for k in keys):
        return 72000
    return 1


def shown(metric: dict, value: int):
    """A raw stat value in the unit people read: whole blocks, hours to 0.1, else as is."""
    div = scale(metric)
    if div == 100:
        return value // 100
    if div > 1:
        return round(value / div, 1)
    return value


def describe(entry: dict) -> str:
    n = shown(entry["metric"], entry["target"])
    return entry["text"].replace("{n}", f"{n:,}" if isinstance(n, int) else f"{n:,.1f}".rstrip("0").rstrip("."))


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


def _instance(row) -> dict:
    return {**json.loads(row["data"]), "iid": row["id"], "kind": row["kind"], "period": row["period"]}


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


    # ---- challenge instances (frozen copies of pool entries for one day or week)
    def add_instance(self, kind: str, period: str, entry: dict) -> int:
        with self.conn() as c:
            return c.execute("INSERT INTO instances (kind, period, data) VALUES (?,?,?)",
                             (kind, period, json.dumps(entry))).lastrowid

    def current(self, kind: str, period: str) -> list:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM instances WHERE kind=? AND period=? AND active=1 ORDER BY id",
                             (kind, period)).fetchall()
        return [_instance(r) for r in rows]

    def instance(self, iid: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM instances WHERE id=?", (iid,)).fetchone()
        return _instance(r) if r else None

    def replace_instance(self, iid: int, entry: dict) -> None:
        with self.conn() as c:
            c.execute("UPDATE instances SET data=? WHERE id=?", (json.dumps(entry), iid))


def progress(totals: dict, store: Store, mark_name: str, metric: dict) -> dict:
    mark = store.mark(mark_name) or {}
    return {u: metric_value(delta(flat, mark.get(u)), metric) for u, flat in totals.items()}


# ---- challenge instances and completions
def pick(pool: list, count: int, avoid: set, rng) -> list:
    """`count` enabled entries, preferring ones not in `avoid` (the previous period's picks)."""
    enabled = [e for e in pool if e.get("enabled", True)]
    fresh = [e for e in enabled if e["id"] not in avoid]
    rng.shuffle(fresh)
    chosen = fresh[:count]
    if len(chosen) < count:
        rest = [e for e in enabled if e["id"] in avoid]
        rng.shuffle(rest)
        chosen += rest[:count - len(chosen)]
    return [json.loads(json.dumps(e)) for e in chosen]


def record_completions(store: Store, iid: int, prog: dict, target: int, now: float, names: dict) -> list:
    """Records players who reached the target. Returns the new ones, most progress first, then by name."""
    with store.conn() as c:
        done = {r["uuid"] for r in c.execute("SELECT uuid FROM completions WHERE iid=?", (iid,))}
        new = [u for u, v in prog.items() if v >= target and u not in done]
        new.sort(key=lambda u: (-prog[u], names.get(u, u).lower()))
        seq = c.execute("SELECT COALESCE(MAX(seq), 0) FROM completions").fetchone()[0]
        for i, u in enumerate(new, 1):
            c.execute("INSERT INTO completions (iid, uuid, at, seq) VALUES (?,?,?,?)", (iid, u, now, seq + i))
    return new


def first_finisher(store: Store, iid: int) -> str | None:
    with store.conn() as c:
        r = c.execute("SELECT uuid FROM completions WHERE iid=? ORDER BY at, seq LIMIT 1", (iid,)).fetchone()
    return r["uuid"] if r else None


def completed(store: Store, iid: int) -> set:
    with store.conn() as c:
        return {r["uuid"] for r in c.execute("SELECT uuid FROM completions WHERE iid=?", (iid,))}
