"""Turn the game's per-player stats files into leaderboards."""
import json
from fnmatch import fnmatchcase
from pathlib import Path

DISTANCE = ("walk", "sprint", "crouch", "swim", "fly", "aviate", "boat", "horse", "minecart", "pig", "strider", "climb", "fall", "walk_on_water", "walk_under_water", "happy_ghast")


def names(server: Path) -> dict:
    out = {}
    for f in ("usercache.json", "whitelist.json"):
        try:
            out.update({e["uuid"]: e["name"] for e in json.loads((server / f).read_text()) if e.get("uuid")})
        except (OSError, ValueError, TypeError, KeyError):
            pass
    return out


def _flat(path: Path) -> dict:
    out = {}
    for cat, values in (json.loads(path.read_text()).get("stats") or {}).items():
        for key, n in (values or {}).items():
            out[f"{cat.removeprefix('minecraft:')}:{key}"] = int(n)
    return out


def player(path: Path, known: dict, mark: dict | None = None) -> dict:
    """One player's numbers, counted since `mark` (their flat totals at the season start) when given."""
    flat = _flat(path)
    if mark:
        flat = {k: max(0, n - mark.get(k, 0)) for k, n in flat.items()}
    get = lambda k: flat.get("custom:minecraft:" + k, 0)
    uuid = path.stem
    return {
        "uuid": uuid,
        "name": known.get(uuid, uuid[:8]),
        "hours": round(get("play_time") / 72000, 1),
        "deaths": get("deaths"),
        "mob_kills": get("mob_kills"),
        "player_kills": get("player_kills"),
        "mined": sum(n for k, n in flat.items() if k.startswith("mined:")),
        "km": round(sum(get(k + "_one_cm") for k in DISTANCE) / 100000, 1),
        "last_seen": int(path.stat().st_mtime),
        "flat": flat,
    }


def collect(server: Path, world: str = "world", mark: dict | None = None) -> list:
    """Everyone's stats, since the season mark if given. Also fills in fun facts and season awards' raw numbers."""
    known = names(server)
    players = []
    for path in sorted((server / world / "players" / "stats").glob("*.json")):
        try:
            players.append(player(path, known, (mark or {}).get(path.stem) if mark is not None else None))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    for p in players:
        flat = p.pop("flat")
        p["facts"] = fun_facts(flat)
        p["extra"] = {key: _metric(flat, cat, pats) for key, _, _, cat, pats in EXTRA_AWARDS}
    return sorted(players, key=lambda p: p["hours"], reverse=True)


def world_bytes(server: Path, world: str = "world") -> int:
    total = 0
    for f in (server / world).rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


AWARDS = (
    ("hours", "Never logs off", "{} hours in the world"),
    ("mined", "Human excavator", "{} blocks mined"),
    ("km", "Has seen things", "{} km travelled"),
    ("mob_kills", "Mobs fear them", "{} mobs defeated"),
    ("deaths", "Professional faller", "{} deaths and counting"),
    ("player_kills", "Trust issues", "{} players defeated"),
)


def merge_players(whitelist: list, online: list, played: list, ops: set) -> list:
    """Everyone who belongs on the player list, with what we know about them."""
    online_ids = {p["uuid"] for p in online}
    by_id = {p["uuid"]: p for p in played}
    people = {p["uuid"]: p["name"] for p in whitelist if p.get("uuid")}
    people.update({p["uuid"]: p["name"] for p in online})
    out = []
    for uuid, name in people.items():
        s = by_id.get(uuid)
        out.append({"uuid": uuid, "name": name, "online": uuid in online_ids, "op": uuid in ops,
                    "played": s is not None, "stats": s})
    return sorted(out, key=lambda p: (not p["online"], -(p["stats"] or {}).get("hours", 0), p["name"].lower()))


def _metric(flat: dict, cat: str, patterns) -> int:
    prefix = cat + ":"
    return sum(n for k, n in flat.items() if k.startswith(prefix) and any(fnmatchcase(k[len(prefix):], p) for p in patterns))


# Season awards beyond the six above: (key, title, text, stat category, key patterns).
EXTRA_AWARDS = (
    ("jumps", "Bouncy", "{} jumps", "custom", ["minecraft:jump"]),
    ("fish", "Gone fishing", "{} fish caught", "custom", ["minecraft:fish_caught"]),
    ("cake", "Has their cake", "{} slices of cake eaten", "custom", ["minecraft:eat_cake_slice"]),
    ("bells", "Ding dong", "{} bells rung", "custom", ["minecraft:bell_ring"]),
    ("trades", "Villager's best friend", "{} trades with villagers", "custom", ["minecraft:traded_with_villager"]),
    ("bred", "Matchmaker", "{} animals bred", "custom", ["minecraft:animals_bred"]),
    ("diamonds", "Shiny!", "{} diamond ore mined", "mined", ["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"]),
    ("logs", "Lumberjack", "{} logs chopped", "mined", ["minecraft:*_log", "minecraft:*_stem"]),
    ("creeper_deaths", "Creeper magnet", "blown up by creepers {} times", "killed_by", ["minecraft:creeper"]),
    ("fall_deaths", "Gravity tester", "{} blocks fallen", "custom", ["minecraft:fall_one_cm"]),
    ("swim", "Basically a fish", "{} blocks swum", "custom", ["minecraft:swim_one_cm"]),
    ("boat", "Captain", "{} blocks by boat", "custom", ["minecraft:boat_one_cm"]),
    ("horse", "Cowboy", "{} blocks on horseback", "custom", ["minecraft:horse_one_cm"]),
    ("enchant", "Wizard", "{} items enchanted", "custom", ["minecraft:enchant_item"]),
    ("sleep", "Well rested", "{} nights slept", "custom", ["minecraft:sleep_in_bed", "minecraft:sleep_in_straw_bed"]),
    ("chests", "Hoarder", "{} chests opened", "custom", ["minecraft:open_chest", "minecraft:open_barrel"]),
    ("music", "DJ", "{} records played", "custom", ["minecraft:play_record"]),
    ("crafted", "Crafty", "{} things crafted", "crafted", ["*"]),
    ("damage", "Glass cannon", "{} damage dealt", "custom", ["minecraft:damage_dealt"]),
    ("sneak", "Sneaky", "{} blocks crouched", "custom", ["minecraft:crouch_one_cm"]),
)
CM_AWARDS = {"fall_deaths", "swim", "boat", "horse", "sneak"}  # distance stats are in cm: shown as blocks
ALL_AWARDS = [a[1] for a in AWARDS] + [a[1] for a in EXTRA_AWARDS]
GB_KM = 1407  # Land's End to John o' Groats by road


def _pretty(key: str) -> str:
    return key.split(":")[-1].replace("_", " ").capitalize()


def _top(flat: dict, cat: str):
    items = [(k[len(cat) + 1:], n) for k, n in flat.items() if k.startswith(cat + ":") and n > 0]
    return max(items, key=lambda kv: (kv[1], kv[0]), default=None)


def fun_facts(flat: dict) -> list:
    facts = []
    km = round(sum(n for k, n in flat.items() if k.startswith("custom:") and k.endswith("_one_cm")
                   and "fall" not in k) / 100000, 1)
    if km >= 1:
        r = km / GB_KM
        if 0.45 <= r < 0.55:
            how = "half the length of Great Britain"
        elif r < 0.1:
            how = f"{r * 100:.0f}% of the way from Land's End to John o' Groats"
        else:
            how = f"{r:.1f} times the length of Great Britain"
        facts.append(f"Travelled {km:,} km: {how}")
    for cat, label, unit in (("mined", "Favourite block to mine", None), ("killed_by", "Nemesis", "deaths"),
                             ("used", "Most used", "times"), ("killed", "Favourite target", None)):
        top = _top(flat, cat)
        if top:
            n = f"{top[1]:,} {unit}" if unit else f"{top[1]:,}"
            facts.append(f"{label}: {_pretty(top[0])} ({n})")
    return facts


def awards(played: list) -> list:
    out = []
    for key, title, text in AWARDS:
        best = max(played, key=lambda p: p[key], default=None)
        if best and best[key] > 0:
            out.append({"title": title, "text": text.format(f"{best[key]:,}"), "name": best["name"], "uuid": best["uuid"]})
    for key, title, text, _, _ in EXTRA_AWARDS:
        best = max(played, key=lambda p: (p.get("extra") or {}).get(key, 0), default=None)
        value = (best.get("extra") or {}).get(key, 0) if best else 0
        if value > 0:
            shown = value // 100 if key in CM_AWARDS else value
            if shown > 0:
                out.append({"title": title, "text": text.format(f"{shown:,}"), "name": best["name"], "uuid": best["uuid"]})
    return out
