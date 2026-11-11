"""Challenge pools and community goals: the defaults, and loading/saving the admin-edited copies.

Pools live in web-data/challenges.json, goals in web-data/goals.json. Nether and End challenges are left out
of the defaults while those dimensions are locked (DimLock); add them on the admin page once unlocked.
"""
import json
import os
import re
from pathlib import Path

from .progress import CATEGORIES

ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
TARGET_MAX = 10 ** 9


class PoolError(Exception):
    pass


def c(cat, *keys):
    return {"cat": cat, "keys": list(keys)}


def r(*pairs):
    return [{"item": f"minecraft:{item}", "count": n} for item, n in pairs]


CM = 100  # distance stats are in centimetres
MENDING = 'enchanted_book[stored_enchantments={"minecraft:mending":1}]'
UNBREAKING = 'enchanted_book[stored_enchantments={"minecraft:unbreaking":3}]'
EFFICIENCY = 'enchanted_book[stored_enchantments={"minecraft:efficiency":4}]'


def d(id, text, metric, target, reward):
    return {"id": id, "text": text, "metric": metric, "target": target, "reward": reward, "enabled": True}


def w(id, text, metric, target, reward, bonus):
    return {**d(id, text, metric, target, reward), "bonus": bonus}


DAILY = [
    d("d-stone", "Mine {n} stone", c("mined", "minecraft:stone", "minecraft:deepslate", "minecraft:cobblestone"), 128, r(("oak_log", 32))),
    d("d-logs", "Chop {n} logs", c("mined", "minecraft:*_log", "minecraft:*_stem"), 48, r(("iron_ingot", 10))),
    d("d-zombies", "Defeat {n} zombies", c("killed", "minecraft:zombie", "minecraft:husk", "minecraft:drowned"), 10, r(("iron_ingot", 10))),
    d("d-skeletons", "Defeat {n} skeletons", c("killed", "minecraft:skeleton", "minecraft:stray", "minecraft:bogged"), 8, r(("arrow", 32))),
    d("d-creepers", "Defeat {n} creepers", c("killed", "minecraft:creeper"), 5, r(("gunpowder", 16))),
    d("d-spiders", "Defeat {n} spiders", c("killed", "minecraft:spider", "minecraft:cave_spider"), 6, r(("string", 24))),
    d("d-fish", "Catch {n} fish", c("custom", "minecraft:fish_caught"), 8, r(("cooked_salmon", 16))),
    d("d-walk", "Walk or sprint {n} blocks", c("custom", "minecraft:walk_one_cm", "minecraft:sprint_one_cm"), 3000 * CM, r(("cooked_beef", 16))),
    d("d-swim", "Swim {n} blocks", c("custom", "minecraft:swim_one_cm"), 300 * CM, r(("prismarine_shard", 8))),
    d("d-boat", "Row a boat {n} blocks", c("custom", "minecraft:boat_one_cm"), 800 * CM, r(("oak_log", 32))),
    d("d-breed", "Breed {n} animals", c("custom", "minecraft:animals_bred"), 6, r(("wheat", 32))),
    d("d-trade", "Trade with villagers {n} times", c("custom", "minecraft:traded_with_villager"), 5, r(("emerald", 6))),
    d("d-crops", "Harvest {n} crops", c("mined", "minecraft:wheat", "minecraft:carrots", "minecraft:potatoes", "minecraft:beetroots"), 48, r(("bone_meal", 32))),
    d("d-coal", "Mine {n} coal ore", c("mined", "minecraft:coal_ore", "minecraft:deepslate_coal_ore"), 16, r(("torch", 64))),
    d("d-iron", "Mine {n} iron ore", c("mined", "minecraft:iron_ore", "minecraft:deepslate_iron_ore"), 12, r(("iron_ingot", 10))),
    d("d-copper", "Mine {n} copper ore", c("mined", "minecraft:copper_ore", "minecraft:deepslate_copper_ore"), 16, r(("iron_ingot", 6))),
    d("d-bread", "Bake {n} bread", c("crafted", "minecraft:bread"), 12, r(("cooked_porkchop", 12))),
    d("d-torches", "Craft {n} torches", c("crafted", "minecraft:torch"), 64, r(("coal", 16))),
    d("d-jump", "Jump {n} times", c("custom", "minecraft:jump"), 300, r(("rabbit_stew", 2))),
    d("d-sleep", "Sleep through {n} nights", c("custom", "minecraft:sleep_in_bed", "minecraft:sleep_in_straw_bed"), 2, r(("white_wool", 8))),
    d("d-sand", "Dig {n} sand or gravel", c("mined", "minecraft:sand", "minecraft:red_sand", "minecraft:gravel"), 64, r(("glass", 32))),
    d("d-cake", "Eat {n} slices of cake", c("custom", "minecraft:eat_cake_slice"), 3, r(("sugar", 16), ("egg", 4))),
]

WEEKLY = [
    w("w-diamonds", "Mine {n} diamond ore", c("mined", "minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"), 8, r(("diamond", 3)), r(("diamond", 8))),
    w("w-miner", "Mine {n} blocks", c("mined", "*"), 5000, r(("iron_ingot", 32)), r((EFFICIENCY, 1))),
    w("w-lumber", "Chop {n} logs", c("mined", "minecraft:*_log", "minecraft:*_stem"), 640, r(("iron_ingot", 24)), r(("diamond_axe", 1))),
    w("w-explorer", "Travel {n} blocks on foot", c("custom", "minecraft:walk_one_cm", "minecraft:sprint_one_cm"), 30000 * CM, r(("golden_carrot", 32)), r(("saddle", 1), ("name_tag", 2))),
    w("w-sailor", "Sail {n} blocks by boat", c("custom", "minecraft:boat_one_cm"), 10000 * CM, r(("emerald", 16)), r(("heart_of_the_sea", 1))),
    w("w-rider", "Ride a horse {n} blocks", c("custom", "minecraft:horse_one_cm"), 5000 * CM, r(("golden_carrot", 32)), r(("diamond_horse_armor", 1))),
    w("w-angler", "Catch {n} fish", c("custom", "minecraft:fish_caught"), 64, r(("emerald", 16)), r((MENDING, 1))),
    w("w-hunter", "Defeat {n} hostile mobs", c("custom", "minecraft:mob_kills"), 200, r(("iron_ingot", 32)), r(("diamond_sword", 1))),
    w("w-creepers", "Defeat {n} creepers", c("killed", "minecraft:creeper"), 40, r(("tnt", 8)), r(("diamond", 6))),
    w("w-endermen", "Defeat {n} endermen", c("killed", "minecraft:enderman"), 15, r(("ender_pearl", 8)), r(("ender_pearl", 16), ("diamond", 3))),
    w("w-witch", "Defeat {n} witches", c("killed", "minecraft:witch"), 3, r(("glass_bottle", 16), ("redstone", 32)), r(("brewing_stand", 1), ("blaze_powder", 8))),
    w("w-pillager", "Defeat {n} pillagers", c("killed", "minecraft:pillager", "minecraft:vindicator"), 10, r(("emerald", 20)), r(("totem_of_undying", 1))),
    w("w-breeder", "Breed {n} animals", c("custom", "minecraft:animals_bred"), 60, r(("lead", 4), ("hay_block", 8)), r(("name_tag", 3))),
    w("w-trader", "Trade with villagers {n} times", c("custom", "minecraft:traded_with_villager"), 50, r(("emerald", 32)), r((MENDING, 1))),
    w("w-farmer", "Harvest {n} crops", c("mined", "minecraft:wheat", "minecraft:carrots", "minecraft:potatoes", "minecraft:beetroots", "minecraft:melon", "minecraft:pumpkin"), 600, r(("bone_meal", 64)), r(("golden_apple", 2))),
    w("w-enchanter", "Enchant {n} items", c("custom", "minecraft:enchant_item"), 10, r(("lapis_lazuli", 32)), r(("experience_bottle", 32))),
    w("w-smith", "Craft {n} iron tools or armour", c("crafted", "minecraft:iron_*"), 12, r(("iron_ingot", 24)), r((UNBREAKING, 1))),
    w("w-builder", "Place {n} blocks of any kind", c("used", "minecraft:*stone*", "minecraft:*planks", "minecraft:*brick*", "minecraft:glass*", "minecraft:*_log"), 3000, r(("scaffolding", 64)), r(("diamond_pickaxe", 1))),
    w("w-bells", "Ring a bell {n} times", c("custom", "minecraft:bell_ring"), 20, r(("emerald", 12)), r(("bell", 1), ("emerald", 12))),
    w("w-cake", "Eat {n} slices of cake", c("custom", "minecraft:eat_cake_slice"), 25, r(("cake", 2)), r(("golden_apple", 2))),
    w("w-music", "Play {n} music discs", c("custom", "minecraft:play_record"), 5, r(("note_block", 4)), r(("jukebox", 1), ("emerald", 16))),
    w("w-swimmer", "Swim {n} blocks", c("custom", "minecraft:swim_one_cm"), 3000 * CM, r(("turtle_scute", 3)), r(("trident", 1))),
    w("w-climber", "Climb {n} blocks of ladders and vines", c("custom", "minecraft:climb_one_cm"), 500 * CM, r(("ladder", 64)), r(("diamond", 4))),
    w("w-gold", "Mine {n} gold ore", c("mined", "minecraft:gold_ore", "minecraft:deepslate_gold_ore"), 40, r(("gold_ingot", 16)), r(("golden_apple", 3))),
    w("w-lapis", "Mine {n} lapis ore", c("mined", "minecraft:lapis_ore", "minecraft:deepslate_lapis_ore"), 20, r(("experience_bottle", 16)), r((EFFICIENCY, 1))),
    w("w-redstone", "Mine {n} redstone ore", c("mined", "minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"), 30, r(("redstone_block", 8)), r(("observer", 8), ("piston", 8))),
    w("w-emerald", "Mine {n} emerald ore", c("mined", "minecraft:emerald_ore", "minecraft:deepslate_emerald_ore"), 3, r(("emerald", 16)), r(("emerald_block", 4))),
    w("w-raid", "Win {n} raid(s)", c("custom", "minecraft:raid_win"), 1, r(("emerald_block", 3)), r(("totem_of_undying", 1), ("emerald_block", 3))),
    w("w-shield", "Block {n} damage with a shield", c("custom", "minecraft:damage_blocked_by_shield"), 2000, r(("iron_ingot", 16)), r(("diamond_chestplate", 1))),
    w("w-playtime", "Play for {n} hours", c("custom", "minecraft:play_time"), 10 * 72000, r(("golden_carrot", 32)), r(("diamond", 5))),
    w("w-hoarder", "Open {n} chests or barrels", c("custom", "minecraft:open_chest", "minecraft:open_barrel"), 300, r(("chest", 8)), r(("shulker_shell", 2))),
    w("w-crafter", "Use a crafting table {n} times", c("custom", "minecraft:interact_with_crafting_table"), 150, r(("oak_log", 64)), r(("diamond", 3))),
]

DEFAULT_POOLS = {"daily": DAILY, "weekly": WEEKLY}

DEFAULT_GOALS = [
    {"id": "g-mine-100k", "text": "Mine {n} blocks together", "metric": c("mined", "*"), "target": 100_000},
    {"id": "g-walk-1000k", "text": "Walk {n} km together", "metric": c("custom", "minecraft:walk_one_cm", "minecraft:sprint_one_cm"), "target": 1000 * 1000 * CM},
    {"id": "g-mobs-5k", "text": "Defeat {n} hostile mobs together", "metric": c("custom", "minecraft:mob_kills"), "target": 5_000},
    {"id": "g-fish-1k", "text": "Catch {n} fish together", "metric": c("custom", "minecraft:fish_caught"), "target": 1_000},
    {"id": "g-diamonds-500", "text": "Mine {n} diamond ore together", "metric": c("mined", "minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"), "target": 500},
    {"id": "g-mine-1m", "text": "Mine {n} blocks together", "metric": c("mined", "*"), "target": 1_000_000},
]

# Rewards are checked by rewards.validate_reward (wired in by rewards.py at import time).
reward_check = None


def _check_metric(m) -> None:
    if not isinstance(m, dict) or m.get("cat") not in CATEGORIES:
        raise PoolError("A challenge's stat type isn't one the game keeps.")
    keys = m.get("keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and 0 < len(k) <= 80 for k in keys):
        raise PoolError("A challenge needs at least one stat name.")


def _check_reward(items, label) -> list:
    if not isinstance(items, list) or not items:
        raise PoolError(f"Every challenge needs a {label}.")
    if reward_check:
        try:
            return reward_check(items)
        except Exception as e:  # RewardError: keep its message
            raise PoolError(str(e)) from None
    return items


def check_entry(e: dict, weekly: bool) -> dict:
    if not isinstance(e, dict) or not ID_RE.fullmatch(str(e.get("id", ""))):
        raise PoolError("Challenge ids are 1-40 lowercase letters, digits, - or _.")
    text = e.get("text")
    if not isinstance(text, str) or "{n}" not in text or len(text) > 120 or "\n" in text:
        raise PoolError(f"{e['id']}: the text must include {{n}} (the target) and fit on one line.")
    _check_metric(e.get("metric"))
    t = e.get("target")
    if not isinstance(t, int) or isinstance(t, bool) or not 1 <= t <= TARGET_MAX:
        raise PoolError(f"{e['id']}: the target must be a whole number from 1 to {TARGET_MAX:,}.")
    out = {"id": e["id"], "text": text, "metric": {"cat": e["metric"]["cat"], "keys": list(e["metric"]["keys"])},
           "target": t, "reward": _check_reward(e.get("reward"), "reward"), "enabled": bool(e.get("enabled", True))}
    if weekly:
        out["bonus"] = _check_reward(e.get("bonus"), "first-to-finish bonus")
    return out


def _write(path: Path, value) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1))
    os.replace(tmp, path)


def save_pools(data: Path, pools: dict) -> dict:
    clean = {}
    for kind in ("daily", "weekly"):
        entries = pools.get(kind)
        if not isinstance(entries, list):
            raise PoolError(f"The {kind} pool is missing.")
        clean[kind] = [check_entry(e, kind == "weekly") for e in entries]
    ids = [e["id"] for e in clean["daily"] + clean["weekly"]]
    if len(ids) != len(set(ids)):
        raise PoolError("Two challenges have the same id.")
    data.mkdir(parents=True, exist_ok=True)
    _write(data / "challenges.json", clean)
    return clean


def load_pools(data: Path) -> dict:
    try:
        return json.loads((data / "challenges.json").read_text())
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_POOLS))


def save_goals(data: Path, goals: list) -> list:
    if not isinstance(goals, list):
        raise PoolError("Goals must be a list.")
    clean = []
    for g in goals:
        if not isinstance(g, dict) or not ID_RE.fullmatch(str(g.get("id", ""))):
            raise PoolError("Goal ids are 1-40 lowercase letters, digits, - or _.")
        if not isinstance(g.get("text"), str) or "{n}" not in g["text"] or len(g["text"]) > 120:
            raise PoolError(f"{g['id']}: the text must include {{n}}.")
        _check_metric(g.get("metric"))
        t = g.get("target")
        if not isinstance(t, int) or isinstance(t, bool) or not 1 <= t <= 10 ** 12:
            raise PoolError(f"{g['id']}: the target must be a positive whole number.")
        clean.append({"id": g["id"], "text": g["text"], "metric": {"cat": g["metric"]["cat"], "keys": list(g["metric"]["keys"])}, "target": t})
    if len({g["id"] for g in clean}) != len(clean):
        raise PoolError("Two goals have the same id.")
    data.mkdir(parents=True, exist_ok=True)
    _write(data / "goals.json", clean)
    return clean


def load_goals(data: Path) -> list:
    try:
        return json.loads((data / "goals.json").read_text())
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_GOALS))
