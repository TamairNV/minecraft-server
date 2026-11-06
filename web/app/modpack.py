"""Builds the client modpack (.mrpack) from Modrinth for whatever version the server runs."""
import hashlib
import json
import logging
import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

log = logging.getLogger("mc")
API = "https://api.modrinth.com/v2"
FABRIC_META = "https://meta.fabricmc.net/v2/versions/loader"
HEADERS = {"User-Agent": "tamairnv-mc-site/1.0"}
PACK_NAME = "TNV Server Pack"
KEEP_BUILDS = 5
# Modrinth ignores this file at the root of the pack, but some launchers use it.
ICON = Path(__file__).parent / "static" / "brand" / "logo-512.png"
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")
# Launchers refuse an .mrpack that downloads from anywhere else.
ALLOWED_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}


@dataclass(frozen=True)
class Mod:
    slug: str
    client: str = "required"
    essential: bool = False  # no build means no pack: the server's features need it


# Iris goes last so Sodium is chosen on its own merits before Iris's pin is looked at.
MODS = (
    Mod("fabric-api", essential=True),
    Mod("simple-voice-chat", essential=True),
    Mod("distanthorizons", essential=True),
    Mod("sodium"),
    Mod("lithium"),
    Mod("ferrite-core"),
    Mod("entityculling"),
    Mod("immediatelyfast"),
    Mod("modmenu"),
    Mod("tweakeroo"),
    Mod("iris", client="optional"),
)


class PackError(Exception):
    pass


class NotFound(Exception):
    pass


def http_fetch(url: str):
    try:
        r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    except httpx.HTTPError as e:
        raise PackError(f"Couldn't reach {urlparse(url).netloc}: {e}") from None
    if r.status_code == 404:
        raise NotFound(url)
    if r.status_code != 200:
        raise PackError(f"{urlparse(url).netloc} answered {r.status_code}.")
    return r.json()


def server_version(server: Path) -> str:
    """The version paper.jar will run, which still works while the server is stopped."""
    try:
        with zipfile.ZipFile(server / "paper.jar") as z:
            version = json.loads(z.read("version.json"))["id"]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        folders = sorted((p for p in (server / "versions").glob("*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
        if not folders:
            raise PackError("Couldn't work out which Minecraft version the server runs.") from None
        version = folders[-1].name
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise PackError("The server reported an odd Minecraft version.")
    return version


def pick(versions: list[dict]) -> dict | None:
    """Modrinth lists newest first. Alphas are never picked."""
    for kind in ("release", "beta"):
        for v in versions:
            if v.get("version_type") == kind:
                return v
    return None


class Resolver:
    def __init__(self, mc: str, fetch):
        self.mc, self.fetch = mc, fetch
        self.query = urlencode({"game_versions": json.dumps([mc]), "loaders": json.dumps(["fabric"])})
        self.chosen: dict[str, dict] = {}  # project id -> {"version", "client", "via"}
        self.skipped: list[dict] = []

    def latest(self, project: str) -> dict | None:
        try:
            return pick(self.fetch(f"{API}/project/{project}/version?{self.query}"))
        except NotFound:
            return None

    def _pinned(self, version_id: str) -> dict | None:
        try:
            v = self.fetch(f"{API}/version/{version_id}")
        except NotFound:
            return None
        return v if self.mc in v.get("game_versions", []) and "fabric" in v.get("loaders", []) else None

    def _dependency(self, dep: dict) -> dict | None:
        found = self.latest(dep["project_id"]) if dep.get("project_id") else None
        if not found and dep.get("version_id"):
            found = self._pinned(dep["version_id"])
        return found

    def _gather(self, version: dict, client: str, via: str | None, plan: dict) -> str | None:
        """Adds a version and its required dependencies to plan. Returns what's missing, if anything."""
        pid = version["project_id"]
        if pid in self.chosen or pid in plan:
            return None
        plan[pid] = {"version": version, "client": client, "via": via}
        for dep in version.get("dependencies", []):
            if dep.get("dependency_type") != "required" or not (dep.get("project_id") or dep.get("version_id")):
                continue
            if dep.get("project_id") in self.chosen or dep.get("project_id") in plan:
                continue
            found = self._dependency(dep)
            if not found:
                return dep.get("project_id") or dep["version_id"]
            missing = self._gather(found, client, via or pid, plan)
            if missing:
                return missing
        return None

    def add(self, mod: Mod) -> None:
        version = self.latest(mod.slug)
        reason = f"no release or beta build for {self.mc}" if not version else None
        plan: dict = {}
        if version:
            # A mod whose dependency is missing crashes the game on launch, so it can't go in alone.
            missing = self._gather(version, mod.client, None, plan)
            if missing:
                reason = f"needs {missing}, which has no build for {self.mc}"
        if reason:
            if mod.essential:
                raise PackError(f"{mod.slug} can't go in the pack: {reason}.")
            self.skipped.append({"slug": mod.slug, "reason": reason})
            return
        self.chosen.update(plan)

    def warnings(self) -> list[str]:
        out = []
        ids = {e["version"]["id"]: pid for pid, e in self.chosen.items()}
        for pid, entry in self.chosen.items():
            for dep in entry["version"].get("dependencies", []):
                other = dep.get("project_id") or ids.get(dep.get("version_id"))
                if other not in self.chosen or other == pid:
                    continue
                name = entry["version"].get("name") or pid
                if dep.get("dependency_type") == "incompatible":
                    out.append(f"{name} says it's incompatible with {self.chosen[other]['version'].get('name') or other}.")
                elif dep.get("dependency_type") == "required" and dep.get("version_id") and dep["version_id"] != self.chosen[other]["version"]["id"]:
                    out.append(f"{name} asks for a different version of {self.chosen[other]['version'].get('name') or other} than the one chosen.")
        return out


def stable_loader(mc: str, fetch) -> str:
    try:
        loaders = fetch(f"{FABRIC_META}/{mc}")
    except NotFound:
        loaders = []
    for entry in loaders:
        if entry.get("loader", {}).get("stable"):
            return entry["loader"]["version"]
    raise PackError(f"Fabric has no stable loader for {mc}.")


def index_file(version: dict, client: str) -> tuple[dict, str]:
    files = version.get("files", [])
    file = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not file:
        raise PackError(f"{version.get('name')} has no files on Modrinth.")
    name = file.get("filename", "")
    hashes = file.get("hashes", {})
    if not name.endswith(".jar") or "/" in name or "\\" in name or name.startswith("."):
        raise PackError(f"Modrinth gave an unsafe filename: {name!r}.")
    if not hashes.get("sha1") or not hashes.get("sha512") or urlparse(file.get("url", "")).netloc not in ALLOWED_HOSTS:
        raise PackError(f"{name} is missing hashes or isn't hosted on Modrinth.")
    return {
        "path": f"mods/{name}",
        "hashes": {"sha1": hashes["sha1"], "sha512": hashes["sha512"]},
        "env": {"client": client, "server": "unsupported"},
        "downloads": [file["url"]],
        "fileSize": file["size"],
    }, name


def titles(ids: list[str], fetch) -> dict[str, dict]:
    if not ids:
        return {}
    try:
        return {p["id"]: p for p in fetch(f"{API}/projects?{urlencode({'ids': json.dumps(ids)})}")}
    except NotFound:
        return {}


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Packs:
    def __init__(self, server: Path, folder: Path, fetch=http_fetch):
        self.server, self.folder, self.fetch = server, folder, fetch
        self._lock = threading.Lock()
        self._job: dict | None = None

    def build(self, who: str) -> dict:
        """Writes a new pack and makes it the one people download. A failure leaves the current one alone."""
        mc = server_version(self.server)
        resolver = Resolver(mc, self.fetch)
        for mod in MODS:
            resolver.add(mod)
        loader = stable_loader(mc, self.fetch)
        info = titles(list(resolver.chosen), self.fetch)

        files, mods = [], []
        for pid, entry in resolver.chosen.items():
            version = entry["version"]
            item, filename = index_file(version, entry["client"])
            files.append(item)
            project = info.get(pid, {})
            mods.append({
                "name": project.get("title") or version.get("name") or filename,
                "slug": project.get("slug") or pid,
                "version": version.get("version_number"),
                "type": version.get("version_type"),
                "file": filename,
                "client": entry["client"],
                "dependency_of": info.get(entry["via"], {}).get("title") or entry["via"],
            })
        mods.sort(key=lambda m: m["name"].lower())

        now = int(time.time())
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
        index = {
            "formatVersion": 1,
            "game": "minecraft",
            "versionId": f"{mc}-{stamp}",
            "name": PACK_NAME,
            "summary": f"Client mods for the TNV SMP on Minecraft {mc}.",
            "files": files,
            "dependencies": {"minecraft": mc, "fabric-loader": loader},
        }
        self.folder.mkdir(parents=True, exist_ok=True)
        filename = f"TNV-Server-Pack-{mc}-{stamp}.mrpack"
        pack = self.folder / filename
        tmp = pack.with_name(filename + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("modrinth.index.json", json.dumps(index, indent=2))
            z.write(ICON, "icon.png")
        os.replace(tmp, pack)

        manifest = {
            "name": PACK_NAME, "file": filename, "minecraft": mc, "fabric_loader": loader, "mods": mods,
            "skipped": resolver.skipped, "warnings": resolver.warnings(),
            "built_at": now, "built_by": who,
            "bytes": pack.stat().st_size, "sha512": hashlib.sha512(pack.read_bytes()).hexdigest(),
        }
        write_atomic(self.folder / f"{pack.stem}.json", json.dumps(manifest, indent=2).encode())
        write_atomic(self.folder / "current.json", json.dumps({"file": filename}).encode())
        self._prune()
        return manifest

    def _prune(self) -> None:
        for stray in self.folder.glob("*.tmp"):
            stray.unlink(missing_ok=True)
        keep = self._current_name()
        packs = sorted(self.folder.glob("TNV-Server-Pack-*.mrpack"), key=lambda p: p.name.rsplit("-", 2)[-2:], reverse=True)
        for old in packs[KEEP_BUILDS:]:
            if old.name != keep:
                old.unlink(missing_ok=True)
                old.with_suffix(".json").unlink(missing_ok=True)

    def _current_name(self) -> str | None:
        try:
            name = json.loads((self.folder / "current.json").read_text())["file"]
        except (OSError, KeyError, TypeError, ValueError):
            return None
        return name if isinstance(name, str) and Path(name).name == name and name.endswith(".mrpack") else None

    def current_file(self) -> Path | None:
        name = self._current_name()
        if not name or not (self.folder / name).is_file():
            return None
        return self.folder / name

    def current(self) -> dict | None:
        pack = self.current_file()
        if not pack:
            return None
        try:
            return json.loads(pack.with_suffix(".json").read_text())
        except (OSError, ValueError):
            return None

    def builds(self) -> list[dict]:
        out = []
        for path in self.folder.glob("TNV-Server-Pack-*.json"):
            try:
                m = json.loads(path.read_text())
                out.append({k: m.get(k) for k in ("file", "minecraft", "built_at", "built_by")} | {"mods": len(m.get("mods", []))})
            except (OSError, ValueError):
                pass
        return sorted(out, key=lambda b: b["built_at"] or 0, reverse=True)

    def job(self) -> dict | None:
        return self._job

    def start(self, who: str, finished=None) -> bool:
        """Runs a build in the background. False if one's already running."""
        with self._lock:
            if self._job and self._job["status"] == "running":
                return False
            self._job = {"status": "running", "started": int(time.time()), "who": who}
        threading.Thread(target=self._run, args=(who, finished), daemon=True, name="modpack-build").start()
        return True

    def _run(self, who: str, finished) -> None:
        job = {"who": who, "started": self._job["started"]}
        try:
            job |= {"status": "done", "manifest": self.build(who)}
        except PackError as e:
            job |= {"status": "failed", "error": str(e)}
        except Exception:
            log.exception("modpack build failed")
            job |= {"status": "failed", "error": "The build hit an unexpected error. See the site's log."}
        job["finished"] = int(time.time())
        self._job = job
        if finished:
            finished(job)
