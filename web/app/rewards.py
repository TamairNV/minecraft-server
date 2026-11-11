"""Challenge rewards: checking them, and giving them when a player presses Claim on the site.

A reward is given with console `give <name> <item> <count>` and only counts as given when the game logs
"Gave ...". The player must be online. Each reward can be claimed once (a unique row in `claims`).
"""
import json
import re
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from . import challenges_default
from .links import NAME_RE, LinkError
from .progress import LONDON, Store, completed, describe, first_finisher

ITEM_RE = re.compile(r"^minecraft:[a-z0-9_]+(\[[A-Za-z0-9_:{}\[\]=\",. '-]{1,200}\])?$")
MAX_ITEMS = 5
DAILY_DAYS, WEEKLY_DAYS = 8, 21  # claimable until 7 days after the day ends / 14 days after the week ends
RATE_SECONDS = 3
LIST_RE = re.compile(r"players online:[ ]?(.*)$", re.M)


class RewardError(Exception):
    pass


class ClaimError(Exception):
    pass


_items_cache: dict = {}


def item_ids(server: Path) -> set | None:
    """Every item id the game knows, from its language file (blocks and items). None if it can't be read."""
    if "ids" in _items_cache:
        return _items_cache["ids"]
    ids = None
    for jar in sorted(server.glob("versions/*/*.jar")):
        try:
            with zipfile.ZipFile(jar) as z:
                lang = json.loads(z.read("assets/minecraft/lang/en_us.json"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
        ids = {"minecraft:" + k.split(".")[2] for k in lang
               if k.startswith(("item.minecraft.", "block.minecraft.")) and k.count(".") == 2}
    _items_cache["ids"] = ids
    return ids


def validate_reward(items, known: set | None) -> list:
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise RewardError(f"A reward is 1 to {MAX_ITEMS} items.")
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise RewardError("Each reward item needs an item and a count.")
        item, count = it.get("item"), it.get("count")
        if not isinstance(item, str) or not ITEM_RE.fullmatch(item):
            raise RewardError(f"'{str(item)[:60]}' isn't an item id like minecraft:diamond.")
        if known is not None and item.split("[", 1)[0] not in known:
            raise RewardError(f"The game doesn't have an item called {item.split('[', 1)[0]}.")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 256:
            raise RewardError(f"{item}: the count must be from 1 to 256.")
        out.append({"item": item, "count": count})
    return out


def use_registry(server: Path) -> None:
    """Make challenge pool saves check reward items against the game's item list."""
    challenges_default.known_items = lambda: item_ids(server)


def _period_start(period: str) -> float:
    d = date.fromisoformat(period)
    return datetime(d.year, d.month, d.day, tzinfo=LONDON).timestamp()


def expires(kind: str, period: str) -> float:
    d = date.fromisoformat(period) + timedelta(days=DAILY_DAYS if kind == "daily" else WEEKLY_DAYS)
    return datetime(d.year, d.month, d.day, tzinfo=LONDON).timestamp()


class Claims:
    def __init__(self, store: Store, query, links, sleep=time.sleep):
        self.store, self.query, self.links, self.sleep = store, query, links, sleep
        self._recent: dict = {}
        self._lock = threading.Lock()

    def _claimed(self, uuid: str) -> set:
        with self.store.conn() as c:
            return {r["key"] for r in c.execute("SELECT key FROM claims WHERE uuid=?", (uuid,))}

    def _rewards_for(self, inst: dict, uuid: str) -> list:
        out = []
        if uuid in completed(self.store, inst["iid"]):
            out.append(("reward", inst["reward"]))
            if inst.get("bonus") and first_finisher(self.store, inst["iid"]) == uuid:
                out.append(("bonus", inst["bonus"]))
        return out

    def claimable(self, uuid: str, now: float) -> list:
        claimed = self._claimed(uuid)
        with self.store.conn() as c:
            rows = c.execute("SELECT id FROM instances WHERE active=1 ORDER BY id").fetchall()
        out = []
        for r in rows:
            inst = self.store.instance(r["id"])
            until = expires(inst["kind"], inst["period"])
            if now >= until:
                continue
            for tier, items in self._rewards_for(inst, uuid):
                key = f"{inst['iid']}:{tier}"
                if key not in claimed:
                    title = describe(inst)
                    out.append({"key": key, "kind": inst["kind"], "tier": tier, "title": title, "items": items, "expires": until})
        return out

    def claim(self, email: str, key: str, now: float) -> dict:
        player = self.links.player_for(email)
        if not player:
            raise ClaimError("Your login isn't linked to a Minecraft name yet. Ask Tamair to link it.")
        name, uuid = player["name"], player["uuid"]
        if not NAME_RE.fullmatch(name):
            raise ClaimError("Your Minecraft name looks wrong. Ask Tamair.")
        with self._lock:
            last = self._recent.get(uuid)
            if last is not None and abs(now - last) < RATE_SECONDS:
                raise ClaimError("Give it a moment, then try again.")
            self._recent[uuid] = now
        wanted = next((c for c in self.claimable(uuid, now) if c["key"] == key), None)
        if wanted is None:
            raise ClaimError("There's nothing to claim there (already claimed, expired or not finished).")
        try:
            with self.store.conn() as c:
                c.execute("INSERT INTO claims (key, uuid, state, at, name) VALUES (?,?,?,?,?)", (key, uuid, "giving", now, name))
        except Exception:
            raise ClaimError("That reward is already being claimed.") from None
        try:
            m = self.query("list", LIST_RE)
            if m is None:
                raise ClaimError("The server didn't answer. Is it running?")
            online = {n.strip().lower() for n in m[1].split(",") if n.strip()}
            if name.lower() not in online:
                raise ClaimError(f"Join the server as {name}, then press Claim.")
            given = self._give(name, wanted["items"])
        except ClaimError:
            self._forget(key, uuid)
            raise
        state = "done" if len(given) == len(wanted["items"]) else "partial"
        with self.store.conn() as c:
            c.execute("UPDATE claims SET state=? WHERE key=? AND uuid=?", (state, key, uuid))
        if state == "partial":
            raise ClaimError("Only part of the reward could be given. Tamair can give the rest from the admin page.")
        return {"given": given, "name": name}

    def _give(self, name: str, items: list) -> list:
        given = []
        for it in items:
            pattern = re.compile(rf"Gave {it['count']} \[.*?\] to {re.escape(name)}\b|No player was found|Unknown item|Expected|Incorrect")
            m = self.query(f"give {name} {it['item']} {it['count']}", pattern)
            if m is None or not m[0].startswith("Gave"):
                if not given:
                    raise ClaimError("The game didn't give the reward. Try again in a moment.")
                break
            given.append(it)
        return given

    def _forget(self, key: str, uuid: str) -> None:
        with self.store.conn() as c:
            c.execute("DELETE FROM claims WHERE key=? AND uuid=? AND state='giving'", (key, uuid))

    def give_to(self, name: str, items: list) -> list:
        """Admin: give items now (Give again / Test reward). The player must be online."""
        if not NAME_RE.fullmatch(name or ""):
            raise ClaimError("That isn't a Minecraft name.")
        m = self.query("list", LIST_RE)
        if m is None or name.lower() not in {n.strip().lower() for n in m[1].split(",")}:
            raise ClaimError(f"{name} isn't online.")
        return self._give(name, items)

    def log(self, limit: int = 200) -> list:
        with self.store.conn() as c:
            rows = c.execute("SELECT key, uuid, state, at, name FROM claims ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


__all__ = ["Claims", "ClaimError", "RewardError", "LinkError", "validate_reward", "item_ids", "use_registry", "expires"]
