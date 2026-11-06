# TNV SMP

Paper Minecraft server run by Crafty Controller, plus a login-only site at https://mc.tamairnv.co.uk. Add people on admin.tamairnv.co.uk, card "Friends SMP": `user` sees status, map and stats, `admin` also gets `/admin`.

| What | Where |
|---|---|
| Game | `25565/tcp`, whitelist on |
| Voice | `24454/udp` |
| Map (BlueMap) | `/map/` on the site, behind login. Host `127.0.0.1:8105` for local use only |
| Crafty dashboard | `https://<server>:8444` (8443 is NPM's). Login in `.env` |
| Site | `127.0.0.1:8106` -> nginx (`minecraft-proxy`) -> `minecraft-web`, through the `house` tunnel |
| World | `crafty/servers/<id>/world` on the NVMe |
| Other worlds | `crafty/servers/<id>/worlds/slots/<slug>`, left out of scheduled backups |
| Backups | `/mnt/fpv2tb/minecraft-backups`: every 6 hours, keeps 28 |
| Admin audit log | `web-data/audit.jsonl` |

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

## When the friend's public proxy is ready

It forwards `25565/tcp` and `24454/udp` to this machine over Tailscale.

1. Set `voice_host=<public host>:24454` in `plugins/voicechat/voicechat-server.properties`, then restart.
2. Set `MC_PUBLIC_ADDRESS` in `.env`, then `docker compose up -d web` to show it on the site.
3. Paper will see every player as the proxy's IP. To get real IPs the proxy must send PROXY protocol and `proxies.proxy-protocol` must be on in `config/paper-global.yml`. Direct LAN and Tailscale joins then stop working, so choose one.
