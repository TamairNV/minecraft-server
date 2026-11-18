"""Smart network tuning: when players' traffic fills the friend's upload, send less.

Every byte to players leaves through the proxy's home upload (~20 Mbit/s, measured with iperf3).
When that fills, each player's movement waits behind chunk data and the game lags. So this
watches netmon's measured tunnel traffic and steps down two things that can change live:

- view distance, with DimLock's /viewdist (Paper changes it per world, nobody is kicked)
- Distant Horizons' request limits, with DHSupport's /dhs config set

It goes up a level when the upload stays busy and back down once it has been quiet for a while
(different thresholds and wait times, so it doesn't flap). Neither change is saved by the game: a
restart goes back to server.properties and DHSupport's config.yml, so those files are "Normal"
and this re-applies its level after a restart. State and the change log: web-data/autotune.json.
"""
import json
import logging
import re
import threading
import time
from pathlib import Path

LEVELS = [
    {"name": "Normal", "view_drop": 0, "dh_conc": None, "dh_rc": None},
    {"name": "Busy", "view_drop": 2, "dh_conc": 8, "dh_rc": 20},
    {"name": "Heavy", "view_drop": 4, "dh_conc": 4, "dh_rc": 10},
    {"name": "Full", "view_drop": 6, "dh_conc": 2, "dh_rc": 5},
]
MIN_VIEW = 6
UP_WINDOW, UP_HOLD = 15, 20        # s of busy upload to go up a level; min s between steps up
DOWN_WINDOW = 120                  # s of quiet upload to go down a level (also min s since the last change)
STALE = 30                         # netmon data older than this: hold, change nothing
CHECK_EVERY = 600                  # s between checks that the game still has our level (restarts)
READ_CACHE = 60                    # s a live read-back stays fresh for the page
MAX_EVENTS = 60
DEFAULTS = {"enabled": True, "uplink_mbit": 20.0, "up_pct": 75, "down_pct": 40}

VIEW_RE = re.compile(r"ViewDist: view (\d+) sim (\d+)")
DH_RE = {k: re.compile(rf"Value of {k} is (\d+)\.") for k in ("full_data_request_concurrency_limit", "login_data_sync_rc_limit")}
DH_KEYS = {"dh_conc": "full_data_request_concurrency_limit", "dh_rc": "login_data_sync_rc_limit"}
log = logging.getLogger("mc")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def base_values(server: Path) -> dict:
    """The "Normal" values: what the game uses after a restart."""
    out = {"view": 10, "dh_conc": 20, "dh_rc": 50}
    try:
        m = re.search(r"^view-distance=(\d+)", (server / "server.properties").read_text(), re.M)
        if m:
            out["view"] = int(m.group(1))
    except OSError:
        pass
    try:
        text = (server / "plugins" / "DHSupport" / "config.yml").read_text()
        for key, name in DH_KEYS.items():
            m = re.search(rf"^{name}:\s*(\d+)", text, re.M)
            if m:
                out[key] = int(m.group(1))
    except OSError:
        pass
    return out


def targets(level: int, base: dict) -> dict:
    lv = LEVELS[level]
    return {
        "view": max(MIN_VIEW, base["view"] - lv["view_drop"]) if lv["view_drop"] else base["view"],
        "dh_conc": min(base["dh_conc"], lv["dh_conc"]) if lv["dh_conc"] else base["dh_conc"],
        "dh_rc": min(base["dh_rc"], lv["dh_rc"]) if lv["dh_rc"] else base["dh_rc"],
    }


def mbit_series(live: dict, seconds: float, now: float) -> list[float]:
    return [s["wg_tx"] * 8 / 1e6 for s in (live or {}).get("recent", [])
            if s.get("wg_tx") is not None and now - s.get("t", 0) <= seconds]


class AutoTune:
    def __init__(self, server: Path, data: Path, netmon_live: Path, crafty, query, clock=time.time):
        self.server, self.file, self.netmon = server, data / "autotune.json", netmon_live
        self.crafty, self.query, self.clock = crafty, query, clock
        self._lock = threading.Lock()
        saved = read_json(self.file, {}) or {}
        self.cfg = {**DEFAULTS, **{k: saved[k] for k in DEFAULTS if k in saved}}
        self.level = int(saved.get("level", 0))
        self.changed_at = float(saved.get("changed_at", 0))
        self.events = saved.get("events", [])[-MAX_EVENTS:]
        self.applied = saved.get("applied")  # what we last sent
        self.live = saved.get("live")        # what the game last said
        self.checked_at = 0.0

    # ---- persistence
    def _save(self) -> None:
        data = {**self.cfg, "level": self.level, "changed_at": self.changed_at, "events": self.events[-MAX_EVENTS:],
                "applied": self.applied, "live": self.live}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.file)

    def _event(self, text: str, **extra) -> None:
        self.events.append({"t": int(self.clock()), "text": text, **extra})
        self.events = self.events[-MAX_EVENTS:]

    # ---- talking to the game
    def _running(self) -> bool:
        try:
            return bool(self.crafty.running())
        except Exception:
            return False

    def _apply(self, values: dict) -> None:
        self.crafty.command(f"viewdist set {values['view']}")
        for key, name in DH_KEYS.items():
            self.crafty.command(f"dhs config set {name} {values[key]}")
        self.applied = {**values, "t": int(self.clock())}

    def read_live(self) -> dict | None:
        """Ask the game what it is using now (three console commands)."""
        if not self._running():
            return None
        out = {"t": int(self.clock())}
        m = self.query("viewdist status", VIEW_RE)
        out["view"] = int(m.group(1)) if m else None
        out["sim"] = int(m.group(2)) if m else None
        for key, name in DH_KEYS.items():
            m = self.query(f"dhs config get {name}", DH_RE[name])
            out[key] = int(m.group(1)) if m else None
        self.live = out
        return out

    # ---- the loop
    def usage(self, now: float) -> dict:
        live = read_json(self.netmon, None)
        if not live or now - live.get("t", 0) > STALE:
            return {"fresh": False}
        up = self.cfg["uplink_mbit"] or 1
        recent = mbit_series(live, UP_WINDOW, now)
        long = mbit_series(live, DOWN_WINDOW, now)
        last = mbit_series(live, 6, now)
        return {
            "fresh": True,
            "now_mbit": round(last[-1], 2) if last else 0.0,
            "short_pct": round(100 * sum(recent) / len(recent) / up, 1) if recent else 0.0,
            "long_max_pct": round(100 * max(long) / up, 1) if long else 0.0,
            "long_samples": len(long),
        }

    def tick(self) -> None:
        with self._lock:
            now = self.clock()
            if not self.cfg["enabled"]:
                return
            if not self._running():
                return
            u = self.usage(now)
            base = base_values(self.server)
            want = self.level
            if u["fresh"]:
                since = now - self.changed_at
                if u["short_pct"] >= self.cfg["up_pct"] and self.level < len(LEVELS) - 1 and since >= UP_HOLD:
                    want = self.level + 1
                    why = f"upload {u['short_pct']:.0f}% busy for {UP_WINDOW} s"
                elif (self.level > 0 and since >= DOWN_WINDOW and u["long_samples"] >= DOWN_WINDOW // 6
                      and u["long_max_pct"] < self.cfg["down_pct"]):
                    want = self.level - 1
                    why = f"upload under {self.cfg['down_pct']}% for {DOWN_WINDOW // 60} min"
            if want != self.level:
                before, after = targets(self.level, base), targets(want, base)
                self._apply(after)
                self._event(f"{LEVELS[self.level]['name']} → {LEVELS[want]['name']}: {why}",
                            level=want, before=before, after=after)
                log.info("autotune: %s", self.events[-1]["text"])
                self.level, self.changed_at = want, now
                self.read_live()
                self.checked_at = now
                self._save()
                return
            # After a restart the game is back on Normal; put our level back.
            if self.level > 0 and now - self.checked_at >= CHECK_EVERY:
                self.checked_at = now
                got = self.read_live()
                want_v = targets(self.level, base)
                if got and got.get("view") is not None and any(got.get(k) != want_v[k] for k in want_v):
                    self._apply(want_v)
                    self._event(f"Re-applied {LEVELS[self.level]['name']} (the server had reset, probably a restart)",
                                level=self.level, after=want_v)
                    self.read_live()
                self._save()

    def loop(self, every: float = 5.0) -> None:
        time.sleep(30)
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("autotune tick failed")
            time.sleep(every)

    # ---- the admin page
    def status(self) -> dict:
        with self._lock:
            now = self.clock()
            base = base_values(self.server)
            return {
                **self.cfg, "level": self.level, "level_name": LEVELS[self.level]["name"],
                "levels": [{"name": lv["name"], **targets(i, base)} for i, lv in enumerate(LEVELS)],
                "base": base, "target": targets(self.level, base), "applied": self.applied, "live": self.live,
                "usage": self.usage(now), "changed_at": self.changed_at or None,
                "events": list(reversed(self.events[-20:])), "running": self._running(),
                "windows": {"up": UP_WINDOW, "down": DOWN_WINDOW},
            }

    def refresh(self) -> dict:
        with self._lock:
            if not self.live or self.clock() - self.live.get("t", 0) >= 5:
                self.read_live()
                self._save()
        return self.status()

    def configure(self, enabled: bool, uplink_mbit: float, up_pct: int, down_pct: int) -> dict:
        if not 1 <= uplink_mbit <= 10000:
            raise ValueError("Upload must be 1 to 10000 Mbit/s.")
        if not 10 <= down_pct < up_pct <= 100:
            raise ValueError("Step down must be below step up, both 10 to 100%.")
        with self._lock:
            was = self.cfg["enabled"]
            self.cfg.update(enabled=bool(enabled), uplink_mbit=float(uplink_mbit), up_pct=int(up_pct), down_pct=int(down_pct))
            if was and not enabled and self.level > 0:
                base = base_values(self.server)
                before = targets(self.level, base)
                if self._running():
                    self._apply(base)
                self._event(f"Turned off: {LEVELS[self.level]['name']} → Normal", level=0, before=before, after=base)
                self.level, self.changed_at = 0, self.clock()
                if self._running():
                    self.read_live()
            elif was != enabled:
                self._event("Turned on" if enabled else "Turned off")
            self._save()
        return self.status()
