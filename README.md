# Friends SMP

A Paper Minecraft server run by Crafty Controller, plus a login-only website with the live map, player stats, a client modpack and world swapping. The site is at https://mc.tamairnv.co.uk.

## Features

- **Website:** server status, a whitelist page with 3D player skins, and the BlueMap world map.
- **Admin area:** swap worlds, edit server settings and world borders, pre-generate chunks, rebuild the modpack and see an audit log.
- **Client modpack:** a one-click `.mrpack` download, built from Modrinth.
- **Voice chat:** Simple Voice Chat, on its own UDP port.
- **Backups:** every 6 hours, keeping 28, on a separate drive.

## How it works

| Container | Job |
|---|---|
| `minecraft-crafty` | Crafty Controller 4.11, running the Paper server and BlueMap |
| `minecraft-web` | FastAPI site (`web/`), with player stats, world swaps and the modpack builder |
| `minecraft-proxy` | nginx in front of the site. It asks the app (`/authz`) before serving the map, because BlueMap has no login of its own |

The site sits behind Cloudflare Access, and the app checks the login token and the person's role itself. `user` sees status, map and stats, and `admin` also gets `/admin`.

| What | Where |
|---|---|
| Game | `25565/tcp`, whitelist on |
| Voice | `24454/udp` |
| Map (BlueMap) | `/map/` on the site, behind login. Host `127.0.0.1:8105` for local use only |
| Crafty dashboard | `https://<server>:8444` |
| Site | `127.0.0.1:8106` -> `minecraft-proxy` -> `minecraft-web` |
| World | `crafty/servers/<id>/world` |
| Other worlds | `crafty/servers/<id>/worlds/slots/<slug>`, left out of scheduled backups |
| Backups | `/mnt/fpv2tb/minecraft-backups` |
| Admin audit log | `web-data/audit.jsonl` |
| server.properties backups (site saves) | `web-data/settings-backups/`, newest 50 |

## Run it

Needs Docker with Compose, and the `access-net` Docker network. `crafty/`, `web-data/` and `.env` aren't in this repo, so a new machine starts with no world.

```sh
cp .env.example .env
docker network create access-net
docker compose up -d --build
```

| Setting in `.env` | What it's for |
|---|---|
| `CRAFTY_ADMIN_USER`, `CRAFTY_ADMIN_PASSWORD` | Crafty admin login |
| `CRAFTY_WEB_USER`, `CRAFTY_WEB_PASSWORD` | The site's restricted Crafty account |
| `CRAFTY_SERVER_ID`, `CRAFTY_BACKUP_ID` | Which Crafty server and backup config to use |
| `MC_PUBLIC_ADDRESS` | Address shown on the site |
| `ACCESS_SITE`, `CF_TEAM_DOMAIN`, `CF_AUD`, `ACCESS_LOWEST_ROLE` | Login checks |

The server ID is also written into the volume paths in `docker-compose.yml`, so change those for a new Crafty server.

## Deployment

| | |
|---|---|
| Folder | `~/minecraft` |
| Port | `127.0.0.1:8106` (site), plus the game, voice and Crafty ports above |
| Address | https://mc.tamairnv.co.uk |

To update the site, run `docker compose up -d --build web`.

## Commands

- `scripts/mc logs`: live console
- `scripts/mc whitelist add <player>`: any console command
- `scripts/mc start|stop|restart`
- `scripts/pregen.sh`: pre-generate the whole border
- `scripts/mc backup`: start a backup
- `scripts/update-server.sh <version>`: back up, then update Paper and plugins. Not yet run. For a fresh world on that version, swap to a new one on the site.

## Site and admin area

- Pages: `/` home, `/players` whitelist with 3D skins, `/world` map, `/admin`. Skins are fetched from Mojang by the server and cached in `web-data/skins` for 6 hours, so browsers never contact a third party.
- The 3D players are CSS boxes (`static/figure.js`), with no canvas or WebGL.
- `web/` is FastAPI, with `nginx` in front. nginx asks the app (`/authz`) before serving the map, because BlueMap has no login of its own.
- It reaches Crafty as `mcsite`, a restricted account (role `site-operator`, Commands permission only).
- After editing `web/nginx/default.conf`, run `docker compose up -d --force-recreate proxy`. A rebuild alone leaves the old config running.
- World swaps are in `web/app/worlds.py`. A swap writes `worlds/swap-journal.json` first, and if the site restarts part way through it restores the previous world on startup.
- Tests: `docker build --target test web`.

## Server settings and pre-generation (2026-11-08)

- Admin, **Server settings** tab (`web/app/settings.py`): server.properties as a grouped form (Gameplay, Performance, Access, Display) plus an Advanced list of every other key. Saving rewrites only changed lines, atomically, after copying the old file to `web-data/settings-backups/`, and logs to `audit.jsonl`. Keys with password/secret/token in the name are never shown; ports, level-name, level-seed, RCON, management-server and online-mode are read only.
- Live while running: difficulty (`difficulty`), default game mode (`defaultgamemode`), whitelist on/off (`whitelist on|off`) and pvp, which is the `pvp` game rule in 26.x, not a property. Everything else needs a restart; the tab shows a banner with a Restart button until Crafty reports a start after the save (`web-data/settings-pending.json`). The game rewrites server.properties on start, which drops comments.
- **World borders** on the same tab: each dimension has its own border in 26.x (`world/dimensions/minecraft/<dim>/data/minecraft/world_border.dat`). Size is read live with `execute in minecraft:<dim> run worldborder get`; the centre comes from that file, which is only written on a world save, so values set from the site are kept in `web-data/borders.json` until then. Set with `worldborder center` and `worldborder set`, no restart.
- Admin, **Pre-generate** tab (`web/app/pregen.py`): Chunky per dimension with radius (defaults to half the border), centre, shape, and Start, Pause, Continue, Cancel, Re-run last (last settings in `web-data/pregen.json`). Progress is parsed from Chunky's lines in `logs/latest.log` every 5 s, so watching sends nothing to the server. Chunky only generates missing chunks. It replaced the old one-button "pre-generate the live world".
- The Pre-generate tab also shows BlueMap's status (`bluemap` in the console, polled only while it's rendering) and has a Render map button per dimension (`bluemap update <map>`). BlueMap renders new chunks by itself: it checks region files every 5 minutes, then renders in the background (about 40 minutes for a 5,000-block square overworld).
- 2026-11-08: map tiles looked missing after a pre-gen because BlueMap's webserver.conf sends `Cache-Control: max-age=86400` on everything, including its empty 204 "no tile yet" replies, so browsers kept blank tiles for a day. nginx (`web/nginx/default.conf`, `/map/`) now replaces that: 204 is `no-store`, tiles `max-age=300`. Browsers that cached blanks before this need the site's cached files cleared (or wait out the day).

## Client modpack

- `/mods` lists the pack and serves the current `.mrpack` at `/mods/download`, for any logged-in user. The pack holds links and hashes, no jars: the launcher downloads from Modrinth.
- Admin, Modpack tab, "Rebuild & distribute pack": takes the newest release (or beta if there's no release, never alpha) of each mod in `MODS` in `web/app/modpack.py`, plus required dependencies, for the version inside `paper.jar`. So after an update to a new version, press it once.
- Fabric API, Simple Voice Chat or Distant Horizons missing fails the build and the old pack stays. Any other mod with no build is skipped and listed, along with anything that needs it.
- Each build has the logo at the root of the zip as `icon.png`, from `web/app/static/brand/logo-512.png`. `/mods` also offers it as a download.
- Files are in `web-data/modpack`: the newest 5 builds, a manifest for each, and `current.json` pointing at the one being served. Builds go in `audit.jsonl`.
- Voice chat's client and server versions should match. Check `plugins/voicechat-bukkit-*.jar` against the pack after updating either.

## Minecraft 26.x gotchas

- Needs Java 25. `-XX:+ZGenerational` is gone: ZGC is always generational since Java 24.
- Gamerules are snake_case: `gamerule players_sleeping_percentage 1`.
- Stats are in `world/players/stats`, dimensions in `world/dimensions/minecraft/*`.
- Each dimension has its own world border.
- Chunky names worlds `minecraft:overworld`, `minecraft:the_nether`, `minecraft:the_end`.
- A Crafty schedule needs `command: backup_server` set. Without it the schedule saves fine and never backs up.
- Crafty 4.11.0 can kill its scheduler at startup with "database is locked" if two or more non-system users have an email set, and backups then silently stop. Blank those emails, including the `default@example.com` new users get. After any Crafty start, check that `schedule.log` shows `Loaded schedules`.

## Running behind a public proxy

The proxy forwards `25565/tcp` and `24454/udp` to this machine.

1. Set `voice_host=<public host>:24454` in `plugins/voicechat/voicechat-server.properties`, then restart.
2. Set `MC_PUBLIC_ADDRESS` in `.env`, then `docker compose up -d web` to show it on the site.
3. Paper will see every player as the proxy's IP. To get real IPs the proxy must send PROXY protocol and `proxies.proxy-protocol` must be on in `config/paper-global.yml`. Direct joins that skip the proxy then stop working, so choose one.

## Project layout

```
docker-compose.yml  Crafty, the site and nginx
scripts/            Console, updates, pre-generation and backups
web/app/            FastAPI site, world swaps and modpack builder
web/nginx/          Proxy config, with the map's login check
web/tests/          Test suite
```
