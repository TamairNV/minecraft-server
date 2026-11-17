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
- `minecraft-playit` (playit.gg agent, host network, outbound only): public IPv4 way in for players, molecule-phases.tun.ply.gg port 46157 -> 127.0.0.1:25565. Every player then shows the same source address, so IP bans mean nothing.

## Data

- `crafty/` (config, servers, logs). World slots in crafty/servers/<id>/worlds/slots. Backups every 6 h, keeping 28, on /mnt/fpv2tb/minecraft-backups. `web-data/` (skins cache, modpack builds). Crafty DB crafty/config/db/crafty.sqlite.

## Secrets (names only, never values)

- `.env`: CRAFTY_ADMIN_USER, CRAFTY_ADMIN_PASSWORD, CRAFTY_SERVER_ID, MC_PUBLIC_ADDRESS, ACCESS_SITE, CF_TEAM_DOMAIN, CF_AUD, ACCESS_LOWEST_ROLE, CRAFTY_WEB_USER, CRAFTY_WEB_PASSWORD, CRAFTY_BACKUP_ID, PLAYIT_SECRET_KEY (playit.gg agent key; Tamair pasted it into a Claude chat on 2026-11-17, so rotate it in the playit account if that matters).

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
- nginx (web/nginx/default.conf) only lets GET/HEAD through, except /admin/api/ and /api/claim. A new player-facing POST route needs its own location block or it gets nginx's 403 (app tests don't go through nginx). Apply with `docker compose up -d --force-recreate proxy`.
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
- 2026-11-11: enchantment glint on enchanted rewards (CSS mask over the icon, still with reduced motion); README Challenges section now covers greyed Claim buttons and reward pictures. Commit ca08db4. Test data for RadioactiveAnt7 still in place until Tamair presses Reset.
- 2026-11-11: claims failed in production with "something went wrong": nginx denied every POST outside /admin/api/. Added `location = /api/claim` (POST only). Backup ~/backups/minecraft-nginx-default.conf_*.
- 2026-11-16: the server has a public IPv6 address and players can use smp.tamairnv.co.uk (DNS-only AAAA, IPv6 only; mc.tamairnv.co.uk stays the website). scripts/smp-ddns.sh (tnv cron, every 5 min) keeps the record on the current eno1 address; state and log in data/ddns (gitignored). Uncommitted: the script and .gitignore. Next: Tamair adds the router IPv6 firewall rule for TCP 25565 (and UDP 24454 for voice); if the prefix ever changes, the router rule needs the new address too. Details: infra notes `machine` and `apps/minecraft`.
- 2026-11-17: added the playit.gg agent as compose service `playit` (container minecraft-playit) because friends on IPv4-only networks can't use the IPv6 address; tested with a status ping through molecule-phases.tun.ply.gg:46157. scripts/ping.py fails through playit (its edge resets the protocol -1 handshake), so test with a real protocol number. Compose backup ~/backups/minecraft-compose.yml_20261117-001149. Uncommitted. Next: Tamair decides whether smp.tamairnv.co.uk gets an SRV record to the playit address.
- 2026-11-17: game address is now mc.tamairnv.co.uk: SRV record _minecraft._tcp.mc -> molecule-phases.tun.ply.gg:46157 (playit). The website record for mc is untouched. playit resets a handshake that carries any hostname other than its own; the vanilla client sends the SRV target so it should be fine, but Tamair has not yet confirmed a real join by that name. smp.tamairnv.co.uk (IPv6 AAAA + scripts/smp-ddns.sh cron) still exists as a direct route. MC_PUBLIC_ADDRESS in .env is empty; set it to mc.tamairnv.co.uk if the site should show the address.
- 2026-11-17: Players now join via the friend's reverse proxy (server.tamairnv.co.uk -> WireGuard wg0, this server 10.100.0.2; infra note `wireguard`). playit.gg container stopped at Tamair's request (was failing); SRV for mc.tamairnv.co.uk still points at playit, pending Tamair's DNS decision.
- 2026-11-17: bot load test through the friend's proxy (infra note `wireguard`): chunk loading ~4x slower than direct, limited per connection by lag + packet loss between the two ISPs. Server was briefly on a vanilla 26.1.2 jar with a throwaway world (online-mode off) for the test; everything restored from ~/backups/minecraft-loadtest_*, test world in ~/_attic/20261117. Next: BBR congestion control for the crafty container (needs sudo modprobe tcp_bbr).
