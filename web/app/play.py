"""Player-facing challenge routes: /api/challenges (what's on, everyone's progress, my rewards) and /api/claim."""
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import progress as pg
from .admin import same_origin
from .rewards import ClaimError
from .tnv_access import AccessUser, current_user


class Claim(BaseModel):
    key: str


def build(engine, claims, clock=time.time) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])
    cache = {"at": 0.0, "value": None}
    cache_lock = threading.Lock()

    def shared() -> dict:
        """Everything that's the same for every viewer, rebuilt at most every 30 s."""
        now = clock()
        with cache_lock:
            if cache["value"] is not None and now - cache["at"] < 30:
                return cache["value"]
        totals = engine.totals()
        names = engine.names()

        def card(inst: dict) -> dict:
            prog = engine.instance_progress(inst, totals)
            done = pg.completed(engine.store, inst["iid"])
            first = pg.first_finisher(engine.store, inst["iid"])
            rows = [{"uuid": u, "name": names.get(u, u[:8]), "value": pg.shown(inst["metric"], v), "done": u in done}
                    for u, v in prog.items() if v > 0 or u in done]
            rows.sort(key=lambda r: (not r["done"], -r["value"], r["name"].lower()))
            out = {"iid": inst["iid"], "text": pg.describe(inst), "target": pg.shown(inst["metric"], inst["target"]),
                   "reward": inst["reward"], "progress": rows, "raw": prog}
            if inst["kind"] == "weekly":
                out["bonus"] = inst.get("bonus", [])
                out["bonus_winner"] = names.get(first, first[:8]) if first else None
            return out

        week, day = engine.store.get("week"), engine.store.get("day")
        value = {
            "daily": [card(i) for i in engine.store.current("daily", day)] if day else [],
            "weekly": [card(i) for i in engine.store.current("weekly", week)] if week else [],
            "goal": engine.goals_state(totals)["current"],
            "goals_done": len(engine.goals_state(totals)["done"]),
            "boards": engine.week_boards(totals),
            "hall": engine.hall(),
            "ends": {"day": pg.next_day(now), "week": pg.next_week(now)},
        }
        with cache_lock:
            cache.update(at=now, value=value)
        return value

    @router.get("/challenges")
    def challenges(user: AccessUser = Depends(current_user)):
        base = shared()
        player = claims.links.player_for(user.email)
        me = {"linked": bool(player), "name": player["name"] if player else None, "claimable": []}
        if player:
            me["claimable"] = claims.claimable(player["uuid"], clock())
        mine = {c["key"] for c in me["claimable"]}

        def strip(card: dict) -> dict:
            card = {k: v for k, v in card.items() if k != "raw"}
            if player:
                value = next((r["value"] for r in card["progress"] if r["uuid"] == player["uuid"]), 0)
                done = any(r["uuid"] == player["uuid"] and r["done"] for r in card["progress"])
                card["mine"] = {"value": value, "done": done, "claimable": [k for k in mine if k.startswith(f"{card['iid']}:")]}
            else:
                card["mine"] = None
            return card

        return {**base, "daily": [strip(c) for c in base["daily"]], "weekly": [strip(c) for c in base["weekly"]], "me": me}

    @router.post("/claim", dependencies=[Depends(same_origin)])
    def claim(body: Claim, user: AccessUser = Depends(current_user)):
        try:
            result = claims.claim(user.email, body.key, clock())
        except ClaimError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True, **result}

    def invalidate():
        with cache_lock:
            cache["value"] = None

    router.invalidate = invalidate
    return router
