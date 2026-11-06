"""World slots: keep several worlds on the server and swap which one is live.

Live files stay where the game expects them. Everything else lives in
worlds/<slug>/payload, on the same disk, so a swap is a set of instant renames.
Each swap writes a journal first, so a failure or crash can always be undone.
"""
import gzip
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

PAYLOAD = (
    "world",
    "bluemap/web/maps",
    "bluemap/tasks.dat",
    "bluemap/pluginState.json",
    "plugins/DHSupport/data.sqlite",
    "plugins/CoreProtect",
)
NAME_RE = re.compile(r"^[A-Za-z0-9][\w .'-]{1,39}$")
SEED_RE = re.compile(r"^-?[\w ]{1,64}$")
BORDER = 10000
DISK_MARGIN = 5 * 1024**3


class WorldError(Exception):
    """Something the admin needs to read."""


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def data_version(world: Path):
    """(DataVersion, version name) from a world's level.dat, or (None, None)."""
    try:
        with gzip.open(world / "level.dat") as f:
            raw = f.read(1024 * 1024)
    except (OSError, EOFError):
        return None, None
    i = raw.find(b"\x03\x00\x0bDataVersion")
    number = int.from_bytes(raw[i + 14:i + 18], "big") if i >= 0 else None
    name = None
    j = raw.find(b"\x08\x00\x04Name")
    if j >= 0:
        size = int.from_bytes(raw[j + 7:j + 9], "big")
        name = raw[j + 9:j + 9 + size].decode(errors="replace")
    return number, name


def dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


class Worlds:
    def __init__(self, server: Path, backups: Path, crafty, ping, sleep=time.sleep,
                 stop_timeout=180, start_timeout=300, backup_timeout=1800):
        self.server, self.backups, self.crafty, self.ping, self.sleep = Path(server), Path(backups), crafty, ping, sleep
        self.stop_timeout, self.start_timeout, self.backup_timeout = stop_timeout, start_timeout, backup_timeout
        self.root = self.server / "worlds"
        self.state_file = self.root / "active.json"
        self.journal_file = self.root / "swap-journal.json"
        self.job_file = self.root / "job.json"
        self._lock = threading.Lock()
        self._sizes: dict = {}

    # ---- state -------------------------------------------------------------
    def setup(self) -> None:
        """First run: the world already on the server becomes the first slot."""
        if self.state_file.exists():
            return
        slug = "first-world"
        write_json(self._meta_file(slug), {"name": "First world", "seed": self._seed(), "created": int(time.time()),
                                           "source": "existing", "configured": True})
        write_json(self.state_file, {"active": slug, "history": [{"slug": slug, "since": 0}]})

    def active(self) -> str:
        return read_json(self.state_file, {}).get("active", "")

    def _slot(self, slug: str) -> Path:
        return self.root / "slots" / slug

    def _meta_file(self, slug: str) -> Path:
        return self._slot(slug) / "meta.json"

    def _meta(self, slug: str) -> dict:
        meta = read_json(self._meta_file(slug))
        if meta is None:
            raise WorldError("That world doesn't exist.")
        return meta

    def _seed(self) -> str:
        return self._properties().get("level-seed", "")

    def _properties(self) -> dict:
        out = {}
        try:
            for line in (self.server / "server.properties").read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    out[k] = v
        except OSError:
            pass
        return out

    def _set_seed(self, seed: str) -> None:
        path = self.server / "server.properties"
        lines = path.read_text().splitlines()
        new = [f"level-seed={seed}" if line.startswith("level-seed=") else line for line in lines]
        if f"level-seed={seed}" not in new:
            new.append(f"level-seed={seed}")
        tmp = path.with_name("server.properties.tmp")
        tmp.write_text("\n".join(new) + "\n")
        os.replace(tmp, path)

    def _size(self, slug: str, path: Path) -> int:
        hit = self._sizes.get(slug)
        if hit and time.monotonic() - hit[0] < 60:
            return hit[1]
        size = dir_size(path)
        self._sizes[slug] = (time.monotonic(), size)
        return size

    def list(self) -> list:
        active = self.active()
        live_number, _ = data_version(self.server / "world")
        out = []
        slots = self.root / "slots"
        for d in sorted(slots.iterdir()) if slots.exists() else []:
            meta = read_json(d / "meta.json")
            if meta is None:
                continue
            is_active = d.name == active
            world = self.server / "world" if is_active else d / "payload" / "world"
            number, version = data_version(world)
            out.append({
                "slug": d.name, "name": meta.get("name", d.name), "seed": meta.get("seed", ""),
                "created": meta.get("created"), "source": meta.get("source", ""), "active": is_active,
                "version": version, "bytes": self._size(d.name, world) if world.exists() else 0,
                "fresh": not world.exists(),
                "too_new": bool(number and live_number and number > live_number),
                "will_upgrade": bool(number and live_number and number < live_number),
            })
        return sorted(out, key=lambda w: (not w["active"], w["created"] or 0))

    def job(self):
        return read_json(self.job_file)

    def busy(self) -> bool:
        return self._lock.locked()

    # ---- new worlds --------------------------------------------------------
    def _new_slug(self, name: str) -> str:
        if not isinstance(name, str) or not NAME_RE.match(name.strip()):
            raise WorldError("Names can be 2 to 40 letters, numbers, spaces, dots, dashes or apostrophes.")
        slug = slugify(name)
        if not slug:
            raise WorldError("That name needs at least one letter or number.")
        if self._slot(slug).exists():
            raise WorldError("A world with that name already exists.")
        return slug

    def create_new(self, name: str, seed: str = "") -> str:
        slug = self._new_slug(name)
        seed = (seed or "").strip() or str(secrets.randbits(63) - 2**62)
        if not SEED_RE.match(seed):
            raise WorldError("Seeds can only have letters, numbers, spaces and a leading minus.")
        write_json(self._meta_file(slug), {"name": name.strip(), "seed": seed, "created": int(time.time()),
                                           "source": "new", "configured": False})
        return slug

    def backup_files(self) -> list:
        files = [p for p in self.backups.glob("*.zip") if p.is_file()]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def _world_at(self, when: float) -> str:
        current = ""
        for h in read_json(self.state_file, {}).get("history", []):
            if h["since"] <= when:
                current = h["slug"]
        return current

    def list_backups(self) -> list:
        names = {w["slug"]: w["name"] for w in self.list()}
        out = []
        for p in self.backup_files():
            st = p.stat()
            slug = self._world_at(st.st_mtime)
            out.append({"file": p.name, "bytes": st.st_size, "time": int(st.st_mtime), "world": names.get(slug, slug)})
        return out

    def _extract_world(self, backup: Path, target: Path) -> dict:
        with zipfile.ZipFile(backup) as z:
            members = [i for i in z.infolist() if i.filename.startswith("world/")]
            if not any(i.filename == "world/level.dat" for i in members):
                raise WorldError("That backup doesn't contain a world.")
            needed = sum(i.file_size for i in members)
            if shutil.disk_usage(self.root).free - needed < DISK_MARGIN:
                raise WorldError("Not enough free disk space to restore that backup.")
            base = target.resolve()
            for i in members:
                dest = (target / i.filename).resolve()
                is_link = (i.external_attr >> 16) & 0o170000 == 0o120000
                if is_link or not dest.is_relative_to(base):
                    raise WorldError("That backup has unsafe file paths, so it wasn't restored.")
            for i in members:
                dest = target / i.filename
                if i.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
            seed = ""
            if "server.properties" in z.namelist():
                for line in z.read("server.properties").decode(errors="replace").splitlines():
                    if line.startswith("level-seed="):
                        seed = line.split("=", 1)[1]
        (target / "world" / "session.lock").unlink(missing_ok=True)
        return {"seed": seed}

    def _restore(self, slug: str, name: str, backup: Path) -> None:
        self._step(f"Restoring {backup.name}")
        slot = self._slot(slug)
        try:
            found = self._extract_world(backup, slot / "payload")
            write_json(self._meta_file(slug), {"name": name.strip(), "seed": found["seed"], "created": int(time.time()),
                                               "source": f"backup {backup.name}", "configured": True})
        except Exception:
            shutil.rmtree(slot, ignore_errors=True)
            raise

    def start_restore(self, name: str, backup_name: str, by: str) -> None:
        slug = self._new_slug(name)
        match = [p for p in self.backup_files() if p.name == backup_name]
        if not match:
            raise WorldError("That backup doesn't exist.")
        self._run("restore", slug, by, lambda: self._restore(slug, name, match[0]))

    # ---- swapping ----------------------------------------------------------
    def start_swap(self, slug: str, by: str) -> None:
        meta = self._meta(slug)
        if slug == self.active():
            raise WorldError("That world is already live.")
        live, _ = data_version(self.server / "world")
        number, version = data_version(self._slot(slug) / "payload" / "world")
        if number and live and number > live:
            raise WorldError(f"That world was made on a newer version ({version}) than this server. Update the server first.")
        self._run("swap", meta.get("name", slug), by, lambda: self._swap(slug))

    def _run(self, kind: str, target: str, by: str, fn) -> None:
        if not self._lock.acquire(blocking=False):
            raise WorldError("Another world job is still running.")

        def work():
            try:
                fn()
                self._finish("done")
            except Exception as e:  # shown to the admin, who needs the reason
                self._finish("failed", str(e) or type(e).__name__)
            finally:
                self._lock.release()

        write_json(self.job_file, {"id": uuid.uuid4().hex[:8], "kind": kind, "target": target, "by": by,
                                   "status": "running", "step": "Starting", "error": None,
                                   "started": int(time.time()), "finished": None})
        threading.Thread(target=work, daemon=True, name=f"world-{kind}").start()

    def _step(self, text: str) -> None:
        job = self.job() or {}
        job["step"] = text
        write_json(self.job_file, job)

    def _finish(self, status: str, error=None) -> None:
        job = self.job() or {}
        job.update(status=status, error=error, finished=int(time.time()))
        write_json(self.job_file, job)

    def _wait(self, check, timeout: float, what: str) -> None:
        waited = 0.0
        while not check():
            if waited >= timeout:
                raise WorldError(f"Timed out waiting for {what}.")
            self.sleep(2)
            waited += 2

    def _safe_running(self) -> bool:
        try:
            return self.crafty.running()
        except Exception:
            return True

    def _stop(self) -> None:
        if self.crafty.running():
            self.crafty.action("stop_server")
        self._wait(lambda: not self._safe_running(), self.stop_timeout, "the server to stop")

    def _up(self) -> bool:
        try:
            return bool(self.ping())
        except Exception:
            return False

    def _start(self) -> None:
        self.crafty.action("start_server")
        # Paper can take a while on a new world, but a process that has died won't recover.
        waited, down = 0.0, 0.0
        while not self._up():
            if waited >= self.start_timeout or down >= 60:
                raise WorldError("The server didn't come back up with that world.")
            self.sleep(2)
            waited += 2
            down = 0.0 if self._safe_running() else down + 2

    def _backup(self) -> None:
        before = {p.name for p in self.backup_files()}
        self.crafty.backup()

        def finished():
            return any(p.name not in before and zipfile.is_zipfile(p) for p in self.backup_files())
        self._wait(finished, self.backup_timeout, "the backup to finish")

    def _warn_players(self) -> None:
        if not self._up():
            return
        status = self.ping()
        if not status.get("players", {}).get("online"):
            return
        for text, pause in (("60 seconds", 30), ("30 seconds", 20), ("10 seconds", 10)):
            self.crafty.command(f"say The server is restarting to change worlds in {text}.")
            self.sleep(pause)

    def _plan(self, out: str, into: str) -> list:
        ops = []
        for rel in PAYLOAD:
            if (self.server / rel).exists():
                ops.append([str(self.server / rel), str(self._slot(out) / "payload" / rel)])
        for rel in PAYLOAD:
            if (self._slot(into) / "payload" / rel).exists():
                ops.append([str(self._slot(into) / "payload" / rel), str(self.server / rel)])
        return ops

    @staticmethod
    def _move(src: str, dst: str) -> None:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)

    def _rollback(self, journal: dict) -> None:
        """Put the old world back. Safe to run again if it gets interrupted."""
        into = journal["to"]
        not_moved_yet = {src for src, dst in journal["ops"] if not Path(dst).exists()}
        # Whatever the failed start created belongs to the world it failed on.
        for rel in PAYLOAD:
            live = self.server / rel
            if str(live) in not_moved_yet:
                continue  # interrupted before this moved, so it's still the old world's
            kept = self._slot(into) / "payload" / rel
            if live.exists() and not kept.exists():
                self._move(str(live), str(kept))
            elif live.exists():
                self._move(str(live), str(self._slot(into) / f"failed-{int(time.time())}" / rel))
        for src, dst in journal["ops"]:
            if src.startswith(str(self.server / "worlds")):
                continue  # moved in, already returned above
            if Path(dst).exists() and not Path(src).exists():
                self._move(dst, src)
        self._set_seed(journal["seed"])
        self.journal_file.unlink(missing_ok=True)

    def _configure_new_world(self, slug: str) -> None:
        for command in ["gamerule players_sleeping_percentage 1"] + [
            f"execute in minecraft:{dim} run worldborder {part}"
            for dim in ("overworld", "the_nether", "the_end")
            for part in ("center 0 0", f"set {BORDER}")
        ]:
            self.crafty.command(command)
        meta = self._meta(slug)
        meta["configured"] = True
        write_json(self._meta_file(slug), meta)

    def _swap(self, target: str) -> None:
        old = self.active()
        meta = self._meta(target)
        self._step("Warning players")
        self._warn_players()
        self._step("Stopping the server")
        try:
            self._stop()
            self._step("Backing up the current world")
            self._backup()
        except Exception:
            self.crafty.action("start_server")
            raise
        journal = {"from": old, "to": target, "seed": self._seed(), "ops": self._plan(old, target)}
        write_json(self.journal_file, journal)
        try:
            self._step("Switching worlds")
            for src, dst in journal["ops"]:
                self._move(src, dst)
            if meta.get("seed"):
                self._set_seed(meta["seed"])
            self._step("Starting the server")
            self._start()
            if not meta.get("configured"):
                self._step("Setting the border and game rules")
                self._configure_new_world(target)
            state = read_json(self.state_file, {})
            state["active"] = target
            state.setdefault("history", []).append({"slug": target, "since": int(time.time())})
            write_json(self.state_file, state)
            self.journal_file.unlink(missing_ok=True)
            self._sizes.clear()
        except Exception as e:
            self._step("That failed, so the previous world is being restored")
            self._stop()
            self._rollback(journal)
            self._start()
            raise WorldError(f"Couldn't switch to that world, so the previous one is back. {e}") from None

    def recover(self) -> None:
        """After the app was restarted part way through a swap, go back to the old world."""
        journal = read_json(self.journal_file)
        job = self.job()
        if journal:
            with self._lock:
                self._stop()
                self._rollback(journal)
                self._start()
                self._finish("failed", "The site restarted during the swap, so the previous world was restored.")
        elif job and job.get("status") == "running":
            self._finish("failed", "The site restarted during this job.")
