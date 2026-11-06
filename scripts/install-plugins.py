#!/usr/bin/env python3
"""Download the Paper jar and plugins for one Minecraft version, verifying every hash.
Usage: install-plugins.py <mc-version> <server-dir>
Plugins that don't declare support for the version fall back to their newest plugin
build, and this script says so, because those need checking in the server log."""
import hashlib, json, os, sys, urllib.request
UA = {"User-Agent": "tnv-minecraft-setup"}
PLUGIN_LOADERS = {"paper", "bukkit", "spigot", "purpur"}
# modrinth slug -> filename prefix to prefer (BlueMap and Chunky publish a jar per loader)
PLUGINS = {
    "chunky": "Chunky-Paper",
    "bluemap": "bluemap",
    "simple-voice-chat": "voicechat-bukkit",
    "coreprotect": "CoreProtect",
    "discordsrv": "DiscordSRV",
    "distant-horizons-support": "DistantHorizonsSupport",
}

def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180).read()

def getj(url):
    return json.loads(fetch(url))

def install_paper(mc, server):
    b = getj(f"https://fill.papermc.io/v3/projects/paper/versions/{mc}/builds")
    b = next(x for x in b if x["channel"] == "STABLE")
    d = b["downloads"]["server:default"]
    data = fetch(d["url"])
    if hashlib.sha256(data).hexdigest() != d["checksums"]["sha256"]:
        sys.exit("paper: sha256 mismatch")
    with open(os.path.join(server, "paper.jar"), "wb") as f:
        f.write(data)
    print(f"paper {mc} build {b['id']} OK")

def install_plugin(slug, prefix, mc, pdir):
    vs = getj(f"https://api.modrinth.com/v2/project/{slug}/version")
    vs = [v for v in vs if set(v["loaders"]) & PLUGIN_LOADERS]
    if prefix == "bluemap":
        vs = [v for v in vs if "paper" in v["loaders"]]
    exact = [v for v in vs if mc in v["game_versions"]]
    v = (exact or vs)[0]
    tag = "" if exact else f"  ** NOT declared for {mc} (latest declares {v['game_versions'][-1]})"
    f = next((x for x in v["files"] if x["filename"].startswith(prefix)), v["files"][0])
    data = fetch(f["url"])
    if hashlib.sha512(data).hexdigest() != f["hashes"]["sha512"]:
        sys.exit(f"{slug}: sha512 mismatch")
    for old in os.listdir(pdir):
        if old.endswith(".jar") and prefix and old.lower().startswith(prefix.split("-")[0].lower()):
            os.remove(os.path.join(pdir, old))
    with open(os.path.join(pdir, f["filename"]), "wb") as out:
        out.write(data)
    print(f"{slug}: {f['filename']} ({v['version_type']}){tag}")

if __name__ == "__main__":
    mc, server = sys.argv[1], sys.argv[2]
    pdir = os.path.join(server, "plugins")
    os.makedirs(pdir, exist_ok=True)
    install_paper(mc, server)
    for slug, prefix in PLUGINS.items():
        install_plugin(slug, prefix, mc, pdir)
