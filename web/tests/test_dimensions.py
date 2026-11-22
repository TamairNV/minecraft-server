import os
import re

import pytest

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app.dimensions import DimensionError, Dimensions  # noqa: E402

PLUGIN_CONFIG = "# Written by /dimlock\nlocked:\n  nether: true\n  end: false\n"


class Crafty:
    def __init__(self):
        self.sent = []

    def command(self, text):
        self.sent.append(text)


def make(tmp_path, config=PLUGIN_CONFIG, jar=True, answer=None):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    if jar:
        (plugins / "DimLock-1.0.0.jar").write_bytes(b"")
    if config is not None:
        (plugins / "DimLock").mkdir()
        (plugins / "DimLock" / "config.yml").write_text(config)
    crafty = Crafty()

    def query(command, pattern):
        crafty.command(command)
        return pattern.search(answer) if answer else None

    return Dimensions(tmp_path, crafty, query), crafty


def states(d):
    return {x["dim"]: x["locked"] for x in d.view()["dims"]}


def test_reads_plugin_config(tmp_path):
    d, _ = make(tmp_path)
    assert d.view()["installed"] is True
    assert states(d) == {"nether": True, "end": False}


def test_fresh_jar_without_config_is_all_locked(tmp_path):
    d, _ = make(tmp_path, config=None)
    assert d.view()["installed"] is True
    assert states(d) == {"nether": True, "end": True}


def test_not_installed(tmp_path):
    d, _ = make(tmp_path, config=None, jar=False)
    assert d.view()["installed"] is False
    with pytest.raises(DimensionError):
        d.set("nether", False, running=False)


def test_stopped_edits_file_and_keeps_comments(tmp_path):
    d, crafty = make(tmp_path)
    assert d.set("nether", False, running=False) is False
    assert crafty.sent == []
    text = (tmp_path / "plugins" / "DimLock" / "config.yml").read_text()
    assert text == PLUGIN_CONFIG.replace("nether: true", "nether: false")


def test_stopped_writes_config_when_plugin_never_ran(tmp_path):
    d, _ = make(tmp_path, config=None)
    d.set("end", False, running=False)
    assert states(d) == {"nether": True, "end": False}


def test_running_sends_command_and_trusts_reply(tmp_path):
    d, crafty = make(tmp_path, answer="[13:00:00 INFO]: DimLock: end is locked")
    assert d.set("end", True, running=True) is True
    assert crafty.sent == ["dimlock end lock"]
    # The file is the plugin's to write while it runs.
    assert (tmp_path / "plugins" / "DimLock" / "config.yml").read_text() == PLUGIN_CONFIG


def test_running_no_reply_is_an_error(tmp_path):
    d, _ = make(tmp_path)
    with pytest.raises(DimensionError):
        d.set("nether", False, running=True)


@pytest.mark.parametrize("dim", ["overworld", "the_end", "end; op me", ""])
def test_rejects_unknown_dimension(tmp_path, dim):
    d, crafty = make(tmp_path)
    with pytest.raises(DimensionError):
        d.set(dim, False, running=True)
    assert crafty.sent == []
