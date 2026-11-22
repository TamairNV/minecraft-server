"""Which Minecraft player a website login belongs to.

Approved sign-ups from the Discord bot (~/mc-onboard/data/onboard.db, mounted read-only) give email -> whitelist name.
Manual links set on the admin page (web-data/links.json) win over those.
"""
import json
import os
import re
import sqlite3
import threading
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


class LinkError(Exception):
    pass


class Links:
    def __init__(self, onboard_db: Path, data: Path, server: Path):
        self.onboard_db, self.file, self.server = onboard_db, data / "links.json", server
        self._lock = threading.Lock()

    def _onboard(self) -> dict:
        try:
            con = sqlite3.connect(f"file:{self.onboard_db}?mode=ro", uri=True, timeout=3)
        except sqlite3.Error:
            return {}
        try:
            rows = con.execute("SELECT email, mc_name FROM requests WHERE status='approved' ORDER BY id").fetchall()
        except sqlite3.Error:
            return {}
        finally:
            con.close()
        return {email.strip().lower(): name for email, name in rows}

    def _manual(self) -> dict:
        try:
            return json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}

    def _accounts(self) -> list:
        out = []
        for f in ("usercache.json", "whitelist.json"):  # whitelist last: its names win
            try:
                out += [(e["uuid"], e["name"]) for e in json.loads((self.server / f).read_text()) if e.get("uuid") and e.get("name")]
            except (OSError, ValueError, TypeError, KeyError):
                pass
        return out

    def player_for(self, email: str) -> dict | None:
        """The player for a login, with the name exactly as the game has it (the link may differ in case)."""
        email = (email or "").strip().lower()
        name = self._manual().get(email) or self._onboard().get(email)
        if not name or not NAME_RE.fullmatch(name):
            return None
        accounts = self._accounts()
        uuid = next((u for u, n in reversed(accounts) if n.lower() == name.lower()), None)
        if not uuid:
            return None
        real = next((n for u, n in reversed(accounts) if u == uuid), name)
        return {"name": real, "uuid": uuid}

    def list(self) -> dict:
        return {"onboard": [{"email": e, "name": n} for e, n in self._onboard().items()],
                "manual": [{"email": e, "name": n} for e, n in self._manual().items()]}

    def set_manual(self, email: str, name: str) -> None:
        email = (email or "").strip().lower()
        if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,190}", email):
            raise LinkError("That doesn't look like an email address.")
        if not NAME_RE.fullmatch(name or ""):
            raise LinkError("Minecraft names are 3-16 letters, digits or _.")
        with self._lock:
            m = self._manual()
            m[email] = name
            self._write(m)

    def remove_manual(self, email: str) -> None:
        with self._lock:
            m = self._manual()
            m.pop((email or "").strip().lower(), None)
            self._write(m)

    def _write(self, m: dict) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(m, indent=1))
        os.replace(tmp, self.file)
