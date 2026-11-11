"""The background side of challenges: marks at day/week starts, picks, completions, goals, hall of fame, reset.

`tick()` runs every 60 s from a daemon thread (main.py). It never raises: errors are logged.
"""
import json
import logging
import random
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from . import challenges_default as cd
from . import progress as pg
from .crafty import CraftyError
from .stats import names as player_names

log = logging.getLogger("mc.engine")
SAVED_RE = re.compile(r"Saved the game")
DAILY_PICKS, WEEKLY_PICKS = 2, 3
DIST = ["minecraft:walk_one_cm", "minecraft:sprint_one_cm", "minecraft:crouch_one_cm", "minecraft:swim_one_cm",
        "minecraft:boat_one_cm", "minecraft:horse_one_cm", "minecraft:minecart_one_cm", "minecraft:pig_one_cm",
        "minecraft:aviate_one_cm", "minecraft:fly_one_cm", "minecraft:climb_one_cm", "minecraft:strider_one_cm",
        "minecraft:happy_ghast_one_cm", "minecraft:walk_on_water_one_cm", "minecraft:walk_under_water_one_cm",
        "minecraft:nautilus_one_cm"]
# Weekly leaderboards: metric and the divisor that turns the raw stat into the unit shown.
BOARDS = {
    "hours": ({"cat": "custom", "keys": ["minecraft:play_time"]}, 72000),
    "mined": ({"cat": "mined", "keys": ["*"]}, 1),
    "km": ({"cat": "custom", "keys": DIST}, 100000),
    "mob_kills": ({"cat": "custom", "keys": ["minecraft:mob_kills"]}, 1),
}
BOARD_LABELS = {"hours": "hours played", "mined": "blocks mined", "km": "km travelled", "mob_kills": "mobs defeated"}


def fmt(n: float) -> str:
    return f"{n:,.1f}" if isinstance(n, float) and not n.is_integer() else f"{int(n):,}"


class Engine:
    def __init__(self, server: Path, data: Path, crafty, query, store: pg.Store, clock=time.time, rng=None):
        self.server, self.data, self.crafty, self.query, self.store = server, data, crafty, query, store
        self.clock, self.rng = clock, rng or random.Random()
        self._lock = threading.RLock()

    # ---- helpers
    def names(self) -> dict:
        return player_names(self.server)

    def totals(self) -> dict:
        return pg.read_totals(self.server)

    def take_mark(self, name: str) -> dict:
        self.query("save-all", SAVED_RE, timeout=10)  # None if the server is down: the files are then exact
        totals = self.totals()
        self.store.save_mark(name, totals, at=self.clock())
        return totals

    def announce(self, text: str) -> None:
        text = " ".join(str(text).split())[:250]
        for command in ("tellraw @a " + json.dumps({"text": text, "color": "gold"}), "discord broadcast " + text):
            try:
                self.crafty.command(command)
            except CraftyError:
                pass

    @staticmethod
    def _text(inst: dict) -> str:
        return pg.describe(inst)

    # ---- the loop
    def tick(self) -> None:
        with self._lock:
            now = self.clock()
            if self.store.mark("season") is None:
                self.take_mark("season")
            self.check_completions()
            week = pg.week_name(now)
            if self.store.mark("week:" + week) is None:
                self.rollover_week(week)
            day = pg.day_name(now)
            if self.store.mark("day:" + day) is None:
                self.rollover_day(day)
            self.check_completions()
            self.check_goals()

    def _pick(self, kind: str, period: str, count: int, previous: str | None) -> None:
        avoid = {i["id"] for i in self.store.current(kind, previous)} if previous else set()
        for entry in pg.pick(cd.load_pools(self.data)[kind], count, avoid, self.rng):
            self.store.add_instance(kind, period, entry)

    def rollover_day(self, day: str) -> None:
        previous = self.store.get("day")
        self.take_mark("day:" + day)
        if not self.store.current("daily", day):
            self._pick("daily", day, DAILY_PICKS, previous)
        self.store.put("day", day)

    def rollover_week(self, week: str) -> None:
        previous = self.store.get("week")
        totals = self.take_mark("week:" + week)
        if previous and previous != week:
            self._write_hall(previous, totals)
        if not self.store.current("weekly", week):
            self._pick("weekly", week, WEEKLY_PICKS, previous)
        self.store.put("week", week)

    def _board(self, totals: dict, mark: dict, board: str) -> list:
        metric, div = BOARDS[board]
        names = self.names()
        rows = []
        for u, flat in totals.items():
            v = pg.metric_value(pg.delta(flat, (mark or {}).get(u)), metric)
            if v > 0:
                value = round(v / div, 1) if div > 1 else v
                rows.append({"name": names.get(u, u[:8]), "uuid": u, "value": value})
        rows.sort(key=lambda r: (-r["value"], r["name"].lower()))
        return rows

    def _write_hall(self, week: str, totals: dict) -> None:
        mark = self.store.mark("week:" + week) or {}
        lines = []
        with self.store.conn() as c:
            for board in BOARDS:
                top = self._board(totals, mark, board)[:3]
                for rank, row in enumerate(top, 1):
                    c.execute("INSERT OR REPLACE INTO hall (week, board, rank, uuid, value) VALUES (?,?,?,?,?)",
                              (week, board, rank, row["uuid"], row["value"]))
                if top:
                    lines.append(f"{top[0]['name']} ({fmt(top[0]['value'])} {BOARD_LABELS[board]})")
        if lines:
            self.announce("Last week's winners: " + ", ".join(lines) + ". New weekly challenges are up!")

    def current_instances(self) -> list:
        out = []
        week, day = self.store.get("week"), self.store.get("day")
        if week:
            out += self.store.current("weekly", week)
        if day:
            out += self.store.current("daily", day)
        return out

    def instance_progress(self, inst: dict, totals: dict | None = None) -> dict:
        totals = self.totals() if totals is None else totals
        mark = ("week:" if inst["kind"] == "weekly" else "day:") + inst["period"]
        return pg.progress(totals, self.store, mark, inst["metric"])

    def check_completions(self) -> None:
        insts = self.current_instances()
        if not insts:
            return
        totals, names, now = self.totals(), self.names(), self.clock()
        for inst in insts:
            first_before = pg.first_finisher(self.store, inst["iid"])
            new = pg.record_completions(self.store, inst["iid"], self.instance_progress(inst, totals), inst["target"], now, names)
            if inst["kind"] == "weekly" and new and first_before is None:
                who = names.get(new[0], "Someone")
                self.announce(f"{who} was first to finish this week's challenge \"{self._text(inst)}\" and wins the bonus! Claim it on the website.")

    # ---- community goals
    def goals_state(self, totals: dict | None = None) -> dict:
        totals = self.totals() if totals is None else totals
        season = self.store.mark("season") or {}
        done = self.store.get("goals_done", [])
        current = None
        for g in cd.load_goals(self.data):
            if g["id"] in done:
                continue
            value = sum(pg.metric_value(pg.delta(flat, season.get(u)), g["metric"]) for u, flat in totals.items())
            current = {"id": g["id"], "text": pg.describe(g), "value": pg.shown(g["metric"], value),
                       "target": pg.shown(g["metric"], g["target"]), "raw": value, "raw_target": g["target"]}
            break
        return {"current": current, "done": done}

    def check_goals(self) -> None:
        for _ in range(20):
            cur = self.goals_state()["current"]
            if not cur or cur["raw"] < cur["raw_target"]:
                return
            self.store.put("goals_done", self.store.get("goals_done", []) + [cur["id"]])
            self.announce(f"Community goal reached: {cur['text']}! Thanks everyone.")

    # ---- what the pages show
    def week_boards(self, totals: dict | None = None) -> dict:
        totals = self.totals() if totals is None else totals
        week = self.store.get("week")
        mark = self.store.mark("week:" + week) if week else {}
        return {b: self._board(totals, mark or {}, b)[:3] for b in BOARDS}

    def hall(self, weeks: int = 8) -> list:
        names = self.names()
        with self.store.conn() as c:
            rows = c.execute("SELECT * FROM hall ORDER BY week DESC, board, rank").fetchall()
        out = {}
        for r in rows:
            w = out.setdefault(r["week"], {"week": r["week"], "boards": {}})
            w["boards"].setdefault(r["board"], []).append({"name": names.get(r["uuid"], r["uuid"][:8]), "uuid": r["uuid"], "value": r["value"]})
        return list(out.values())[:weeks]

    # ---- admin
    def reset(self, by: str) -> None:
        with self._lock:
            backups = self.data / "progress-backups"
            backups.mkdir(parents=True, exist_ok=True)
            target = backups / f"progress-{time.strftime('%Y%m%d-%H%M%S')}.db"
            src = sqlite3.connect(self.store.path)
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
            for old in sorted(backups.glob("progress-*.db"))[:-20]:
                old.unlink(missing_ok=True)
            with self.store.conn() as c:
                for table in ("marks", "mark_names", "meta", "instances", "completions", "claims", "hall"):
                    c.execute(f"DELETE FROM {table}")
            log.info("progress reset by %s", by)
            self.take_mark("season")
            self.tick()

    def swap(self, iid: int, pool_id: str) -> dict:
        inst = self.store.instance(iid)
        if inst is None:
            raise cd.PoolError("That challenge doesn't exist.")
        entry = next((e for e in cd.load_pools(self.data)[inst["kind"]] if e["id"] == pool_id), None)
        if entry is None:
            raise cd.PoolError("That pool entry doesn't exist.")
        self.store.replace_instance(iid, entry)
        return entry

    def reroll(self, kind: str) -> None:
        with self._lock:
            period = self.store.get("week" if kind == "weekly" else "day")
            if not period:
                raise cd.PoolError("Nothing has been picked yet.")
            current = self.store.current(kind, period)
            with self.store.conn() as c:
                c.execute("UPDATE instances SET active=0 WHERE kind=? AND period=?", (kind, period))
            avoid = {i["id"] for i in current}
            for entry in pg.pick(cd.load_pools(self.data)[kind], WEEKLY_PICKS if kind == "weekly" else DAILY_PICKS, avoid, self.rng):
                self.store.add_instance(kind, period, entry)
