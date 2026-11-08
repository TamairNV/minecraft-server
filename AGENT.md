# Friends SMP / TNV SMP (Minecraft): AGENT.md

> Read this first. It's the human-curated fact sheet for this project on TNV-server. Keep it true: fix any fact you find wrong.
> Learned memory lives in OpenWolf's `.wolf/` (`.wolf/cerebrum.md` = decisions and preferences, `.wolf/STATUS.md` = session handoff, `.wolf/buglog.json` = bugs; AGENT.md holds the facts, .wolf holds what agents learned). Server-wide: `~/AGENT.md` (index of every project), `~/.claude/CLAUDE.md`, and infra notes (`read_note("apps/minecraft")` with the tnv-server MCP tools, or `~/infra/notes/apps/minecraft.md`).
> **Before you finish a session, update "Current state / where we left off" at the bottom** (date, what changed, what's next).

## Purpose

Paper Minecraft server run by Crafty Controller, plus a login-only website (mc.tamairnv.co.uk) with status, whitelist with 3D skins, BlueMap world map, client modpack and an admin area (console, backups, whitelist, world swap). Currently a TEST WORLD on Paper 26.3 until 26.4 ships. Full details in README.md.

## Where it lives

| | |
|---|---|
| URL | https://mc.tamairnv.co.uk (game: MC_PUBLIC_ADDRESS, port 25565) |
| Login | Cloudflare Access on the whole site, panel card `mc` (roles user, admin); /admin needs admin |
| Folder | `~/minecraft` |
| Repo | TamairNV/minecraft-server (public: scan before pushing) |
| Branch state | `main`, 1 uncommitted file on 2026-11-08 |

## Run, deploy, roll back

- `cd ~/minecraft && docker compose up -d --build`. nginx config changes need `docker compose up -d --force-recreate proxy`.
- Console: `~/minecraft/scripts/mc` (e.g. `scripts/mc restart`).
- Update Paper when 26.4 is out (Tamair decides when): `scripts/update-server.sh 26.4`, then swap to a new world at /admin and rebuild the modpack once.
- Back up first: `tar czf ~/backups/minecraft_$(date +%Y%m%d-%H%M%S).tgz -C /home/tnv minecraft` (plus a copy of `.env`). For a Docker app also tag the running image: `docker tag <image> <image>:rollback-<date>`, then roll back by re-tagging it and `docker compose up -d --no-build`, or by restoring the tarball.

## Ports and binds

- 0.0.0.0:25565/tcp (game), 0.0.0.0:24454/udp (voice chat), 0.0.0.0:8444 (Crafty UI): all deliberately open.
- 127.0.0.1:8105 (BlueMap), 127.0.0.1:8106 (site via minecraft-proxy).

## Containers and processes

- `minecraft-crafty` (Crafty 4.11 + Paper + BlueMap, mem 14g, cpus 14), `minecraft-web` (FastAPI site, on access-net), `minecraft-proxy` (nginx, asks the app /authz before serving the map).

## Data

- `crafty/` (config, servers, logs). World slots in crafty/servers/<id>/worlds/slots. Backups every 6 h, keeping 28, on /mnt/fpv2tb/minecraft-backups. `web-data/` (skins cache, modpack builds). Crafty DB crafty/config/db/crafty.sqlite.

## Secrets (names only, never values)

- `.env`: CRAFTY_ADMIN_USER, CRAFTY_ADMIN_PASSWORD, CRAFTY_SERVER_ID, MC_PUBLIC_ADDRESS, ACCESS_SITE, CF_TEAM_DOMAIN, CF_AUD, ACCESS_LOWEST_ROLE, CRAFTY_WEB_USER, CRAFTY_WEB_PASSWORD, CRAFTY_BACKUP_ID.

## Depends on

- access-panel (roles).
- Tunnel `house`.
- /mnt/fpv2tb for backups.

## Health check

- `curl -s localhost:8106/healthz`; after any Crafty start check schedule.log says 'Loaded schedules'.
- Smoke-test site: `mc` (a stopped game server is only a WARN).
- After any change run `smoke-test` (and `smoke-test --tests` if you touched access-panel or house-finder). Don't finish with a new FAIL.

## Gotchas

- Crafty 4.11 scheduler crash: users with emails make refresh_gravatar lock the DB; blank emails on new Crafty users (default@example.com) or backups silently stop.
- Crafty schedules need command=backup_server or they do nothing.
- CoreProtect is off (no 26.3 release). DHSupport is a 0.15.0-SNAPSHOT build; replace with 0.15.0 when released.
- Files in web/app must be chmod 644 (app runs as uid 1000 and hashes every static file at startup).
- Sharing over Tailscale must restrict autogroup:shared to 25565/24454, or a share reaches every 0.0.0.0 service.

## Current state / where we left off

Agents: add a dated line here at the end of every session, newest last, and trim old lines once they stop mattering.

- 2026-11-08: AGENT.md created from the code, compose files and infra notes (server map work).
