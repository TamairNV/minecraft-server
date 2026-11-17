# anatomy.md

> Auto-maintained by OpenWolf. Last scanned: 2026-11-11T18:15:49.469Z
> Files: 75 tracked | Anatomy hits: 0 | Misses: 0

> Project structure index. Auto-maintained by OpenWolf hooks and daemon.
> Run `openwolf scan` to generate, or wait for the first Claude Code session.
> Status: Pending initial scan

## ./

- `.gitignore` — Git ignore rules (~15 tok)
- `.kick-investigation-20261106.txt` (~2318 tok)
- `AGENT.md` — TNV SMP (Minecraft): AGENT.md (~2036 tok)
- `CLAUDE.md` — OpenWolf (~102 tok)
- `docker-compose.yml` — Docker Compose services (~936 tok)
- `README.md` — Project documentation (~3497 tok)

## docs/superpowers/plans/

- `2026-11-11-stats-challenges.md` — Stats, Challenges and Rewards Implementation Plan (~4071 tok)

## docs/superpowers/specs/

- `2026-11-11-stats-challenges-design.md` — Stats, challenges and rewards: design (~2408 tok)

## plugins-src/

- `.gitignore` — Git ignore rules (~4 tok)

## plugins-src/dimlock/

- `build.sh` — Builds target/DimLock-<version>.jar in a throwaway Maven container (nothing installed on the host). (~100 tok)
- `pom.xml` — Maven project configuration (~345 tok)

## plugins-src/dimlock/src/main/java/uk/tamairnv/dimlock/

- `DimLock.java` — Locks portal travel into the Nether and the End until an admin unlocks it. (~1344 tok)

## plugins-src/dimlock/src/main/resources/

- `config.yml` — Written by /dimlock and by the admin site (mc.tamairnv.co.uk/admin). true = nobody can travel in. (~39 tok)
- `plugin.yml` (~122 tok)

## scripts/

- `backup-drive-guard.sh` — Run once with sudo. Makes the empty /mnt/fpv2tb folder on the main disk (the one hidden (~410 tok)
- `crafty.py` — Tiny client for the local Crafty API. Reads ~/minecraft/.env. Prints no secrets. (~452 tok)
- `discord-apply` (~1201 tok)
- `install-plugins.py` — Download the Paper jar and plugins for one Minecraft version, verifying every hash. (~775 tok)
- `mc` (~275 tok)
- `ping.py` — Server-list ping a host: ping.py <host> [port] (~133 tok)
- `pregen.sh` — Queue pre-generation of the whole 10,000 x 10,000 block border. Chunky keeps one (~143 tok)
- `update-server.sh` — Move the server and plugins to a new Minecraft version. Backs up first. (~464 tok)

## web/

- `Dockerfile` — Docker container definition (~96 tok)
- `requirements.txt` — Python dependencies (~27 tok)

## web/app/

- `admin.py` — Admin area. Every route needs the `admin` role from the access panel. (~5765 tok)
- `challenges_default.py` — Challenge pools and community goals: the defaults, and loading/saving the admin-edited copies. (~3938 tok)
- `cpu.py` — CPU use like htop: per thread for the whole machine, plus the Minecraft container. (~904 tok)
- `crafty.py` — Crafty API client, using the site's restricted account: it can send commands (~780 tok)
- `dimensions.py` — Nether / End lock, enforced by the DimLock plugin (~/minecraft/plugins-src/dimlock). (~950 tok)
- `engine.py` — The background side of challenges: marks at day/week starts, picks, completions, goals, hall of fame, reset. (~3920 tok)
- `icons.py` — Item pictures for rewards, drawn the way the game's inventory shows them. (~2045 tok)
- `links.py` — Which Minecraft player a website login belongs to. (~976 tok)
- `main.py` — API: GET (12 endpoints) (~2783 tok)
- `modpack.py` — Builds the client modpack (.mrpack) from Modrinth for whatever version the server runs. (~4018 tok)
- `play.py` — Player-facing challenge routes: /api/challenges (what's on, everyone's progress, my rewards) and /api/claim. (~1327 tok)
- `pregen.py` — Chunk pre-generation with the Chunky plugin, one task per dimension, and the (~2362 tok)
- `progress.py` — Progress since a moment in time: the game's stats files compared with saved copies ("marks"). (~2682 tok)
- `rewards.py` — Challenge rewards: checking them, and giving them when a player presses Claim on the site. (~2916 tok)
- `settings.py` — Server settings: server.properties, and the live world border of each dimension. (~4769 tok)
- `skins.py` — Fetch player skins from Mojang and keep a copy, so visitors never talk to a third party. (~1088 tok)
- `slp.py` — Minecraft server list ping: who is online, without RCON or plugin access. (~369 tok)
- `stats.py` — Turn the game's per-player stats files into leaderboards. (~2359 tok)
- `tnv_access.py` — Check who someone is and what role they have on this site. Copy this file into your app. See README.md. (~1707 tok)
- `worlds.py` — World slots: keep several worlds on the server and swap which one is live. (~5410 tok)

## web/app/static/

- `admin.css` — Styles: 81 rules, 2 media queries (~1916 tok)
- `admin.js` — cell: api, act, button + 15 more (~11812 tok)
- `challenges.js` — Challenges page: daily and weekly challenges, claims, community goal, weekly leaders, hall of fame. (~1969 tok)
- `common.js` — fmt: toast, setText, setChip, refreshChip, skinHead (~540 tok)
- `figure.js` — A player built from their real skin with CSS 3D boxes. No canvas or WebGL, so a (~1438 tok)
- `items.js` — Minecraft item helpers shared by the challenges and admin pages: names, and rewards drawn as inventory slots (~1008 tok)
- `site.css` — Styles: 123 rules, 24 vars, 13 animations (~5378 tok)
- `site.js` — Zustand store (~3060 tok)

## web/app/static/fonts/

- `OFL-pixelify-sans.txt` — Declares of (~1099 tok)

## web/app/templates/

- `admin.html` — Declares and (~3822 tok)
- `base.html` — {% block title %}TNV SMP{% endblock %} (~486 tok)
- `challenges.html` (~483 tok)
- `home.html` (~672 tok)
- `mods.html` (~938 tok)
- `players.html` (~243 tok)
- `world.html` (~82 tok)

## web/nginx/

- `default.conf` — Front door: the site at /, BlueMap at /map/. Cloudflare Access guards the whole (~660 tok)

## web/tests/

- `test_admin_progress.py` — Tests: admin_only, overview, bad_pool_rejected_and_file_unchanged, reset_needs_typed_confirm + 6 more (~2307 tok)
- `test_admin.py` — Tests: needs_login_and_admin_role, other_sites_cant_post, whitelist_rejects_command_injection, modpack_build_needs_admin_and_same_origin + 2 more (~1273 tok)
- `test_challenges_api.py` — Tests: awards_use_season_delta, fun_facts, challenges_payload_shape, claim_requires_same_origin + 6 more (~3099 tok)
- `test_challenges.py` — Tests: pick_avoids_previous_when_possible, pick_skips_disabled_and_falls_back_when_pool_small, instance_is_frozen_copy, first_finisher_and_ties + 4... (~1006 tok)
- `test_cpu.py` — Tests: read_stat, percents_split_user_and_system, cgroup_usage, server_goes_past_100_when_several_threads_are_busy + 2 more (~689 tok)
- `test_dimensions.py` — Tests: reads_plugin_config, fresh_jar_without_config_is_all_locked, not_installed, stopped_edits_file_and_keeps_comments + 4 more (~845 tok)
- `test_engine.py` — Tests: first_tick_takes_season_day_week_and_picks, completion_and_first_finisher_announced, day_rollover_picks_new_dailies, week_rollover_writes_ha... (~2487 tok)
- `test_modpack.py` — Tests: reads_version_from_jar_then_folder, prefers_older_release_over_newer_beta_and_never_takes_alpha, builds_valid_pack_with_dependencies, missin... (~1610 tok)
- `test_players.py` — Tests: texture_info_only_trusts_mojang, png_size_rejects_other_files, merge_lists_everyone_online_first, awards_skip_zero_scores (~411 tok)
- `test_progress.py` — Tests: flatten, metric_patterns_sum, delta_clamps_and_counts_new_players, day_and_week_start_across_clock_change + 4 more (~1090 tok)
- `test_rewards.py` — Tests: reward_rejects_bad_items, reward_accepts_components_and_unknown_registry, links_manual_wins_and_only_approved, links_missing_onboard_db + 16... (~3807 tok)
- `test_settings.py` — Tests: escape_round_trip, save_keeps_comments_order_and_unknown_keys, nothing_changed_writes_nothing, rejects_bad_values + 9 more (~3082 tok)
- `test_stats.py` — Tests: collect_aggregates_and_sorts, no_stats_dir_is_empty, varint (~365 tok)
- `test_worlds.py` — Tests: swap_to_new_world_and_back, failed_start_restores_old_world, restore_refuses_unsafe_backup, bad_names_refused (~1139 tok)
