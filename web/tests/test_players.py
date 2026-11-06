import base64
import json

import pytest

from app import skins, stats


def profile(url, model=None):
    skin = {"url": url}
    if model:
        skin["metadata"] = {"model": model}
    value = base64.b64encode(json.dumps({"textures": {"SKIN": skin}}).encode()).decode()
    return {"properties": [{"name": "textures", "value": value}]}


def test_texture_info_only_trusts_mojang():
    assert skins.texture_info(profile("http://textures.minecraft.net/texture/abc", "slim")) == ("https://textures.minecraft.net/texture/abc", True)
    with pytest.raises(ValueError):
        skins.texture_info(profile("http://evil.example/x.png"))


def test_png_size_rejects_other_files():
    assert skins.png_size(b"<html>") is None


def test_merge_lists_everyone_online_first():
    wl = [{"uuid": "a", "name": "Quiet"}, {"uuid": "b", "name": "Busy"}, {"uuid": "c", "name": "New"}]
    played = [{"uuid": "a", "name": "Quiet", "hours": 9.0}, {"uuid": "b", "name": "Busy", "hours": 1.0}]
    out = stats.merge_players(wl, [{"uuid": "b", "name": "Busy"}], played, {"a"})
    assert [p["name"] for p in out] == ["Busy", "Quiet", "New"]
    assert out[1]["op"] and not out[2]["played"]


def test_awards_skip_zero_scores():
    played = [{"uuid": "a", "name": "A", "hours": 2.0, "mined": 0, "km": 0, "mob_kills": 0, "deaths": 1, "player_kills": 0}]
    assert {x["title"] for x in stats.awards(played)} == {"Never logs off", "Professional faller"}
