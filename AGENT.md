# TNV SMP (Minecraft): AGENT.md

> Read this first. It's the human-curated fact sheet for this project on TNV-server. Keep it true: fix any fact you find wrong.
> Learned memory lives in OpenWolf's `.wolf/` (`.wolf/cerebrum.md` = decisions and preferences, `.wolf/STATUS.md` = session handoff, `.wolf/buglog.json` = bugs; AGENT.md holds the facts, .wolf holds what agents learned). Server-wide: `~/AGENT.md` (index of every project), `~/.claude/CLAUDE.md`, and infra notes (`read_note("apps/minecraft")` with the tnv-server MCP tools, or `~/infra/notes/apps/minecraft.md`).
> **Before you finish a session, update "Current state / where we left off" at the bottom** (date, what changed, what's next).

## Purpose

Paper Minecraft server run by Crafty Controller, plus a login-only website (mc.tamairnv.co.uk) with status, whitelist with 3D skins, BlueMap world map, client modpack and an admin area (console, backups, whitelist, world swap, server settings and borders, Nether/End lock, Chunky pre-generation). Currently a TEST WORLD on Paper 26.3 until 26.4 ships. Full details in README.md.

## Where it lives

| | |
|---|---|
| URL | https://mc.tamairnv.co.uk (game: MC_PUBLIC_ADDRESS, port 25565) |
| Login | Cloudflare Access on the whole site, panel card `mc` (roles user, admin); /admin needs admin |
| Folder | `~/minecraft` |
| Repo | TamairNV/minecraft-server (public: scan before pushing) |
| Branch state | Work on branch `stats-challenges` (2026-11-11), deployed; `main` is ahead of GitHub (not pushed). The server has no push credentials: push from the Mac by fetching over ssh |

## Run, deploy, roll back

- `cd ~/minecraft && docker compose up -d --build`. nginx config changes need `docker compose up -d --force-recreate proxy`.
- Console: `~/minecraft/scripts/mc` (e.g. `scripts/mc restart`).
- Update Paper when 26.4 is out (Tamair decides when): `scripts/update-server.sh 26.4`, then swap to a new world at /admin and rebuild the modpack once.
- Back up first: `tar czf ~/backups/minecraft_$(date +%Y%m%d-%H%M%S).tgz -C /home/tnv minecraft` (plus a copy of `.env`). For a Docker app also tag the running image: `docker tag <image> <image>:rollback-<date>`, then roll back by re-tagging it and `docker compose up -d --no-build`, or by restoring the tarball.

## Ports and binds

- 0.0.0.0:25565/tcp (game), 0.0.0.0:24454/udp (voice chat), 0.0.0.0:8444 (Crafty UI): all deliberately open.
- 127.0.0.1:8105 (BlueMap), 127.0.0.1:8106 (site via minecraft-proxy).

## Containers and processes

- `minecraft-crafty` (Crafty 4.11 + Paper + BlueMap, mem 14g, cpus 14, cgroup slice minecraft.slice), `minecraft-web` (FastAPI site, on access-net), `minecraft-proxy` (nginx, asks the app /authz before serving the map).

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
- 2026-11-08: added Admin tabs Server settings (server.properties form, live/restart tags, per-dimension world borders) and Pre-generate (Chunky per dimension with progress). Code in web/app/settings.py and pregen.py, tests in web/tests/test_settings.py. Web image before this tagged minecraft-web:rollback-20261108. Committed as ebf705b and 092e931.
- 2026-11-08: map cache fix in web/nginx/default.conf (BlueMap's day-long cache on empty 204 tiles hid newly rendered areas) and BlueMap status + Render map button on the Pre-generate tab. Rollback image minecraft-web:rollback-20261108b.
- 2026-11-10: blanked the Crafty system user email (DB backup ~/backups/crafty.sqlite.20261110-190346). Added scripts/backup-drive-guard.sh (Tamair runs with sudo) so a missing 2TB drive makes backups fail instead of filling the main disk.
- 2026-11-11: Nether/End lock: own Paper plugin DimLock (plugins-src/dimlock, jar in server plugins/) plus a Dimensions card on Admin > Server settings (web/app/dimensions.py, tests web/tests/test_dimensions.py). Both dimensions locked now; Tamair unlocks from the site. Rollback image minecraft-web:rollback-20261111, backup ~/backups/minecraft_20261111-131251.tgz. Committed on local main; push from the Mac.
- 2026-11-11: Admin > System: htop-style per-thread CPU meters and a real Minecraft CPU card (100% = one thread), every 1.5 s (web/app/cpu.py, tests web/tests/test_cpu.py). crafty now runs with cgroup_parent minecraft.slice and web mounts /sys/fs/cgroup read-only; Crafty's CPU stat was the 0% bug (it samples badly). Rollback images minecraft-web:rollback-20261111b, compose backup ~/backups/minecraft-compose.yml_20261111-*. Uncommitted: this and the TNV SMP rename.
- 2026-11-11: Challenges, claimable rewards, weekly boards, hall of fame, community goals, season awards and an admin Challenges tab (branch stats-challenges; spec/plan in docs/superpowers). web mounts ~/mc-onboard/data read-only (ONBOARD_DB). Reviewed by a fresh reviewer; fixes in 001b832. Rollback: minecraft-web:rollback-20261111c, ~/backups/minecraft-web-data_20261111-142943.tgz. Next: Tamair links his own email on the Challenges tab and tries Test reward in game; deferred minors are in the session summary.
- 2026-11-11: Claim buttons grey out while the player isn't in game (server list ping, 10 s cache) and rewards show item pictures from the BlueMap client jar (web/app/icons.py, /api/icon/<item>.png; chest has none). Test data added for RadioactiveAnt7 (manual link, swapped challenges, lowered marks): clear it with Reset on the Challenges tab. Rollback image minecraft-web:rollback-20261111d. Commit f1b4a6c.
- 2026-11-11: rewards now show as inventory slots (count in the corner, game-style tooltip); blocks are drawn as shaded isometric cubes from the client jar's models, chests from the entity texture (web/app/icons.py, needs Pillow, added to requirements). Rollback image minecraft-web:rollback-20261111e.
