import json
import zipfile
from urllib.parse import parse_qs, urlparse

import pytest

from app.modpack import API, FABRIC_META, MODS, NotFound, PackError, Packs, pick, server_version

MC = "26.3"


def version(slug, kind="release", deps=(), number="1.0", vid=None):
    return {
        "id": vid or f"v-{slug}", "project_id": slug, "name": slug, "version_number": number, "version_type": kind,
        "game_versions": [MC], "loaders": ["fabric"],
        "dependencies": [{"project_id": d, "version_id": None, "dependency_type": "required"} for d in deps],
        "files": [{"primary": True, "filename": f"{slug}.jar", "url": f"https://cdn.modrinth.com/{slug}.jar", "size": 5,
                   "hashes": {"sha1": "a" * 40, "sha512": "b" * 128}}],
    }


def fake(listing):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.startswith(FABRIC_META):
            return [{"loader": {"version": "0.1-beta", "stable": False}}, {"loader": {"version": "0.2", "stable": True}}]
        if url.startswith(f"{API}/projects?"):
            return [{"id": s, "slug": s, "title": s.title()} for s in json.loads(parse_qs(urlparse(url).query)["ids"][0])]
        slug = url.split("/project/")[1].split("/")[0]
        if slug not in listing:
            raise NotFound(url)
        return listing[slug]

    fetch.calls = calls
    return fetch


def full():
    listing = {m.slug: [version(m.slug)] for m in MODS}
    listing["tweakeroo"] = [version("tweakeroo", deps=["malilib"])]
    listing["malilib"] = [version("malilib")]
    return listing


def server_dir(tmp_path):
    with zipfile.ZipFile(tmp_path / "paper.jar", "w") as z:
        z.writestr("version.json", json.dumps({"id": MC}))
    return tmp_path


def test_reads_version_from_jar_then_folder(tmp_path):
    assert server_version(server_dir(tmp_path)) == MC
    other = tmp_path / "other"
    (other / "versions" / "26.4").mkdir(parents=True)
    assert server_version(other) == "26.4"
    with pytest.raises(PackError):
        server_version(tmp_path / "nothing")


def test_prefers_older_release_over_newer_beta_and_never_takes_alpha():
    assert pick([version("a", "beta", number="2"), version("a", "release", number="1")])["version_number"] == "1"
    assert pick([version("a", "alpha", number="3"), version("a", "beta", number="2")])["version_number"] == "2"
    assert pick([version("a", "alpha")]) is None


def test_builds_valid_pack_with_dependencies(tmp_path):
    packs = Packs(server_dir(tmp_path), tmp_path / "modpack", fetch=fake(full()))
    made = packs.build("a@example.com")
    with zipfile.ZipFile(packs.current_file()) as z:
        assert z.namelist() == ["modrinth.index.json", "icon.png"]
        assert z.read("icon.png").startswith(b"\x89PNG")
        index = json.loads(z.read("modrinth.index.json"))
    assert index["formatVersion"] == 1 and index["game"] == "minecraft" and index["name"] == "TNV Server Pack"
    assert index["dependencies"] == {"minecraft": MC, "fabric-loader": "0.2"}
    paths = {f["path"]: f for f in index["files"]}
    assert "mods/malilib.jar" in paths and "mods/tweakeroo.jar" in paths
    assert paths["mods/iris.jar"]["env"] == {"client": "optional", "server": "unsupported"}
    assert paths["mods/sodium.jar"]["env"]["client"] == "required"
    assert set(paths["mods/sodium.jar"]["hashes"]) == {"sha1", "sha512"}
    assert made["built_by"] == "a@example.com" and made["skipped"] == []
    assert packs.current()["file"] == packs.current_file().name


def test_missing_optional_mod_is_reported_not_fatal(tmp_path):
    listing = full()
    del listing["iris"]
    made = Packs(server_dir(tmp_path), tmp_path / "m", fetch=fake(listing)).build("a")
    assert [s["slug"] for s in made["skipped"]] == ["iris"]


def test_mod_without_its_dependency_is_left_out(tmp_path):
    listing = full()
    del listing["malilib"]
    made = Packs(server_dir(tmp_path), tmp_path / "m", fetch=fake(listing)).build("a")
    assert "tweakeroo" in [s["slug"] for s in made["skipped"]]
    assert "tweakeroo.jar" not in [m["file"] for m in made["mods"]]


@pytest.mark.parametrize("slug", ["fabric-api", "simple-voice-chat", "distanthorizons"])
def test_failed_build_keeps_current_pack(tmp_path, slug):
    listing = full()
    packs = Packs(server_dir(tmp_path), tmp_path / "m", fetch=fake(listing))
    before = packs.build("a")["file"]
    del listing[slug]
    with pytest.raises(PackError):
        packs.build("a")
    assert packs.current_file().name == before


def test_unsafe_filename_refused(tmp_path):
    listing = full()
    listing["sodium"][0]["files"][0]["filename"] = "../../evil.jar"
    with pytest.raises(PackError):
        Packs(server_dir(tmp_path), tmp_path / "m", fetch=fake(listing)).build("a")


def test_keeps_last_builds_only(tmp_path):
    folder = tmp_path / "m"
    folder.mkdir()
    for i in range(8):
        (folder / f"TNV-Server-Pack-26.3-20260101-00000{i}.mrpack").write_text("x")
        (folder / f"TNV-Server-Pack-26.3-20260101-00000{i}.json").write_text("{}")
    packs = Packs(server_dir(tmp_path), folder, fetch=fake(full()))
    current = packs.build("a")["file"]
    left = sorted(p.name for p in folder.glob("*.mrpack"))
    assert len(left) == 5 and current in left and "TNV-Server-Pack-26.3-20260101-000002.mrpack" not in left
    assert not (folder / "TNV-Server-Pack-26.3-20260101-000002.json").exists()


def test_corrupt_pointer_means_no_pack(tmp_path):
    folder = tmp_path / "m"
    folder.mkdir()
    (folder / "current.json").write_text(json.dumps({"file": "../../etc/passwd.mrpack"}))
    assert Packs(tmp_path, folder).current_file() is None
