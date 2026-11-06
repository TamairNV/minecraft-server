"""Turn the game's per-player stats files into leaderboards."""
import json
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


def player(path: Path, known: dict) -> dict:
    stats = json.loads(path.read_text()).get("stats", {})
    custom = stats.get("minecraft:custom", {})
    get = lambda k: int(custom.get("minecraft:" + k, 0))
    uuid = path.stem
    return {
        "uuid": uuid,
        "name": known.get(uuid, uuid[:8]),
        "hours": round(get("play_time") / 72000, 1),
        "deaths": get("deaths"),
        "mob_kills": get("mob_kills"),
        "player_kills": get("player_kills"),
        "mined": sum(int(v) for v in stats.get("minecraft:mined", {}).values()),
        "km": round(sum(get(k + "_one_cm") for k in DISTANCE) / 100000, 1),
        "last_seen": int(path.stat().st_mtime),
    }


def collect(server: Path, world: str = "world") -> list:
    known = names(server)
    players = []
    for path in sorted((server / world / "players" / "stats").glob("*.json")):
        try:
            players.append(player(path, known))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
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


def awards(played: list) -> list:
    out = []
    for key, title, text in AWARDS:
        best = max(played, key=lambda p: p[key], default=None)
        if best and best[key] > 0:
            out.append({"title": title, "text": text.format(f"{best[key]:,}"), "name": best["name"], "uuid": best["uuid"]})
    return out
