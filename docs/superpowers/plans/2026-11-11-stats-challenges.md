# Stats, Challenges and Rewards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily and weekly challenges with claimable item rewards, weekly leaderboards with a hall of fame, community goals, more awards and fun player facts, and an admin reset, on mc.tamairnv.co.uk.

**Architecture:** The web app snapshots ("marks") the game's per-player stats files at season/day/week boundaries into SQLite and measures progress as current minus mark. A 60 s background thread rolls periods over, picks challenges and records completions. Rewards are claimed on the site and given with console `give`, confirmed from the game log.

**Tech Stack:** Python 3.13, FastAPI, SQLite (stdlib), zoneinfo + `tzdata` wheel, Jinja2, vanilla JS/CSS. Tests: pytest, run with `docker build --target test .` in `web/`.

**Spec:** `docs/superpowers/specs/2026-11-11-stats-challenges-design.md`

## Global Constraints

- Time zone `Europe/London`; day starts 00:00, week starts Monday 00:00.
- 2 daily picks, 3 weekly picks; avoid the previous period's picks when the pool allows.
- Expiry: daily rewards until 7 days after the day ends (`day_start + 8d`), weekly until 14 days after the week ends (`week_start + 21d`).
- Reward: up to 5 `{item, count}`; count 1..256; item regex `^minecraft:[a-z0-9_]+(\[[A-Za-z0-9_:{}\[\]=",. '-]{1,200}\])?$`; id must be in the registry when the registry is known.
- Player names `^[A-Za-z0-9_]{3,16}$`. Nothing typed by a player reaches the console.
- Claim needs the player online (`list`), confirmed by a log line `Gave <n> [<...>] to <name>` / `Gave <n> [...] to <name>`; claim rate limit 1 per player per 3 s.
- Data: `web-data/progress.db`, `web-data/challenges.json`, `web-data/goals.json`, `web-data/links.json`, backups `web-data/progress-backups/`.
- Every admin write: `same_origin` + admin role + `audit()`. Files in `web/app` chmod 644.
- Onboard db mounted read-only at `/onboard/onboard.db`, opened `file:...?mode=ro` with `uri=True`; only rows with `status='approved'`.
- No game restart; rebuild web only.

## Review Focus

1. Stats file missing a category or a player who joins mid-week (no mark row) → progress counts from 0, never negative (clamp at 0 if files went backwards, e.g. world swap). Test in Task 1.
2. Clock-change weekends (2026-10-25, 2027-03-28): day/week starts stay at local midnight. Test in Task 1.
3. Two claims for the same reward fired together (double click / two tabs) → exactly one `give`. Test in Task 3.
4. A world swap (`level-name` changes / stats files reset) → progress clamps to 0 rather than going negative; the admin Reset fixes it properly. Covered by test 1.
5. Server stopped during a rollover → rollover still happens with files as they are, `save-all` failure is ignored. Test in Task 4.

---

### Task 1: Metrics, marks and periods (`progress.py` core)

**Files:**
- Create: `web/app/progress.py`, `web/tests/test_progress.py`
- Modify: `web/requirements.txt` (add `tzdata==2025.*`)

**Interfaces (produces):**
- `flatten(stats_json: dict) -> dict[str, int]` keys `"<cat>:<key>"` with the `minecraft:` category prefix stripped from the category only (`"mined:minecraft:stone"`).
- `read_totals(server: Path) -> dict[str, dict[str, int]]` uuid → flat totals, from `server/world/players/stats/*.json`, skipping unreadable files.
- `Metric = {"cat": str, "keys": list[str]}`; `metric_value(flat: dict, metric: dict) -> int` sums keys matched with `fnmatch` within the category.
- `delta(now: dict, mark: dict | None) -> dict` per key `max(0, now - mark)`.
- `day_start(t: float) -> float`, `week_start(t: float) -> float` (London local midnight / Monday), `next_day(t)`, `next_week(t)`.
- `class Store(path: Path)`: SQLite with tables `marks(name TEXT, uuid TEXT, data TEXT, PRIMARY KEY(name, uuid))`, `meta(key TEXT PRIMARY KEY, value TEXT)`, plus tables used by later tasks (`instances`, `completions`, `claims`, `hall`). Methods: `save_mark(name: str, totals: dict)`, `mark(name: str) -> dict[str, dict]`, `get(key, default=None)`, `put(key, value)` (JSON-encoded), `conn()` context manager. Mark names: `season`, `day:<YYYY-MM-DD>`, `week:<YYYY-MM-DD>` (the Monday).
- `progress(totals, store, mark_name, metric) -> dict[uuid, int]`.

- [ ] **Step 1: Write failing tests** in `test_progress.py`:
  `test_flatten`, `test_metric_patterns_sum` (`{"cat":"mined","keys":["minecraft:*_log"]}` over oak_log 3 + birch_log 2 + stone 9 → 5),
  `test_delta_clamps_and_counts_new_players` (no mark → full value; lower now than mark → 0),
  `test_day_and_week_start_across_clock_change` (2026-10-25 12:00 London → day_start is 00:00 BST of that day; `week_start` of Sunday 2026-10-25 = Monday 2026-10-19 00:00 BST; 2027-03-28 likewise),
  `test_store_marks_round_trip`, `test_read_totals_skips_bad_files`.
- [ ] **Step 2:** `cd web && docker build --target test .` → the new tests FAIL (import error).
- [ ] **Step 3:** Implement the interfaces above.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit `progress: stats marks, metrics and London periods`.

### Task 2: Challenge pools, picks and completions

**Files:**
- Create: `web/app/challenges_default.py`, `web/tests/test_challenges.py`
- Modify: `web/app/progress.py`

**Interfaces:**
- Consumes Task 1.
- `DEFAULT_POOLS: {"daily": [...], "weekly": [...]}` (about 20 daily, 30 weekly), `DEFAULT_GOALS: [...]`. Entry: `{"id": str, "text": "Catch {n} fish", "metric": Metric, "target": int, "reward": [Item], "bonus": [Item] (weekly only), "enabled": bool}`. Daily rewards small (e.g. 32 `minecraft:oak_log`, 10 `minecraft:iron_ingot`); weekly everyone-rewards medium, bonus bigger (e.g. diamonds, Mending book).
- `load_pools(data: Path) -> dict`, `save_pools(data, pools)` (validates with `rewards.validate_reward` from Task 3 — Task 2 uses a local placeholder-free check: text has `{n}`, target 1..10**9, metric cat in the 9 known categories; Task 3 adds item validation inside `save_pools`).
- `pick(pool: list, count: int, avoid: set[str], rng: random.Random) -> list[dict]`.
- `Store.instances` table: `(id INTEGER PK, kind TEXT 'daily'|'weekly', period TEXT, data TEXT)`; `Store.add_instance(kind, period, entry) -> int`, `Store.current(kind, period) -> list[dict]` (each with `iid`), `Store.replace_instance(iid, entry)`.
- `completions(iid INTEGER, uuid TEXT, at REAL, PRIMARY KEY(iid, uuid))`; `record_completions(store, iid, progress: dict, target: int, now: float) -> list[uuid]` returns newly completed, ordered by progress desc then name; `first_finisher(store, iid) -> uuid | None` (min `at`, ties by insertion order).

- [ ] **Step 1: Failing tests:** `test_pick_avoids_previous_when_possible`, `test_pick_falls_back_when_pool_small`, `test_instance_is_frozen_copy` (editing pool after add doesn't change `current`), `test_first_finisher_and_ties` (two complete in one check → higher progress first), `test_defaults_are_valid` (every default passes `save_pools` validation incl. Task 3 item checks once present).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Pass.
- [ ] **Step 5:** Commit `progress: challenge pools, picks and completions`.

### Task 3: Links and rewards (validation, claim, give)

**Files:**
- Create: `web/app/links.py`, `web/app/rewards.py`, `web/tests/test_rewards.py`
- Modify: `web/app/challenges_default.py` (call validation in `save_pools`)

**Interfaces:**
- `links.Links(onboard_db: Path, data: Path, server: Path)`: `player_for(email) -> {"name", "uuid"} | None` (manual `links.json` wins over onboard, case-insensitive email; uuid via whitelist/usercache), `list() -> {"onboard": [...], "manual": [...]}` (names and emails, for admin only), `set_manual(email, name)`, `remove_manual(email)`.
- `rewards.item_ids(server: Path) -> set[str] | None` from `item.minecraft.*` and `block.minecraft.*` keys of `assets/minecraft/lang/en_us.json` inside `server/versions/*/*.jar` (cached; None if unreadable).
- `rewards.validate_reward(items: list, known: set | None) -> list` raises `RewardError` with a readable message.
- `rewards.Claims(store, crafty, query, links)`:
  - `claimable(uuid, now) -> list[dict]` (each `{key, kind, title, items, expires}`); key = `f"{iid}:{tier}"`, tier `reward`|`bonus`.
  - `claim(email, key, now) -> dict` steps: link → name check → rate limit (3 s) → per-key lock + `claims` row `(key TEXT, uuid TEXT, state TEXT 'giving'|'done', at REAL, PRIMARY KEY(key, uuid))` inserted as `giving` (unique constraint stops doubles) → `query("list", re.compile(r"players online: (.*)"))` must contain the name, else delete row and raise "Join the server, then press Claim" → for each item `query(f"give {name} {item} {count}", re.compile(rf"Gave {count} \[.*\] to {name}|No player was found|Unknown item"))`; success only on `Gave`; on failure delete row and raise; then mark `done`.
  - `give_again(key, uuid)` for admin (bypasses claimed state, audited by caller).
  - `log(limit=200) -> list` for admin.
- `RewardError`, `ClaimError(Exception)` with user-facing messages.

- [ ] **Step 1: Failing tests:** `test_reward_rejects_injection` (`"minecraft:dirt\nop x"`, `"minecraft:dirt; op x"`, `"dirt"`, count 0, count 257, 6 items, unknown id when registry known) and accepts `minecraft:enchanted_book[stored_enchantments={"minecraft:mending":1}]`; `test_links_manual_wins_and_only_approved` (build a temp onboard db with the real schema: one approved, one pending); `test_claim_gives_and_marks_done`; `test_claim_offline_refused_and_retryable`; `test_claim_twice_gives_once` (two threads → one give); `test_claim_expired_refused`; `test_claim_rate_limited`; `test_give_failure_not_marked`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Pass.
- [ ] **Step 5:** Commit `rewards: links, reward validation and claim flow`.

### Task 4: Engine (rollovers, goals, hall of fame, announcements, reset)

**Files:**
- Create: `web/app/engine.py`, `web/tests/test_engine.py`

**Interfaces:**
- Consumes Tasks 1–3.
- `class Engine(server, data, crafty, query, store, clock=time.time, rng=random.Random())`:
  - `tick()` (called every 60 s by a daemon thread started in `main.py` startup; exceptions logged, never raised): if no `season` mark → take it; if `day:<today>` missing → rollover day; if `week:<monday>` missing → rollover week (week first, then day); then `record_completions` for current instances and announce new first-finishers of weekly challenges; then goals check.
  - `take_mark(name)`: `query("save-all", re.compile("Saved the game"), timeout=10)` (ignore None), then `store.save_mark(name, read_totals(server))`.
  - `rollover_week(prev_monday)`: store hall of fame for the previous week (top 3 per board: hours `custom:minecraft:play_time` /72000, mined `mined:*`, distance `custom:minecraft:*_one_cm` /100000 km, mob kills `custom:minecraft:mob_kills`, plus each weekly instance's first finisher) in `hall(week TEXT, board TEXT, rank INT, uuid TEXT, value REAL)`; announce winners; pick 3 weekly.
  - `rollover_day()`: pick 2 daily.
  - `goals_state() -> {"current": {text, value, target} | None, "done": [...]}`; reaching a goal sets `meta goals_done` and announces.
  - `announce(text: str)`: `tellraw @a {"text": text, "color": "gold"}` (JSON-encoded) and `discord broadcast <text>`; text built only from templates + validated names/numbers.
  - `reset(by: str)`: copy db to `progress-backups/progress-<ts>.db`, clear marks/instances/completions/claims/hall, take `season`, today's and this week's marks, pick new instances.
  - `board(name, period) -> list[{uuid, name, value}]` and `week_boards()` for the page.
- [ ] **Step 1: Failing tests** with a fake clock and fake files: `test_first_tick_takes_season_day_week_and_picks`, `test_week_rollover_writes_hall_and_announces`, `test_rollover_survives_server_down` (query returns None), `test_goal_reached_announces_once_and_advances`, `test_reset_backs_up_and_zeroes`, `test_announce_text_is_json_safe` (name with quotes impossible since validated; text containing `"` is JSON-escaped).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** Pass.
- [ ] **Step 5:** Commit `progress: engine with rollovers, goals and reset`.

### Task 5: Player-facing API and pages

**Files:**
- Modify: `web/app/main.py`, `web/app/stats.py` (more AWARDS from season deltas; `fun_facts(flat) -> list[str]`), `web/app/templates/base.html` (nav "Challenges"), `home.html`, `players.html`, `web/app/static/site.js`, `site.css`
- Create: `web/app/templates/challenges.html`, `web/tests/test_challenges_api.py`

**Interfaces:**
- `GET /api/challenges` (any user): `{daily: [card], weekly: [card], goals, hall: [...last 8 weeks], me: {linked, name, claimable: [...]}, ends: {day, week}}`; card = `{iid, text, target, reward, bonus?, bonus_winner?, progress: [{name, uuid, value, done}], mine: {value, done, claimed}}`. Cached 30 s except `me`.
- `POST /api/claim` (any user, `same_origin`): body `{key}` → `Claims.claim(user.email, key, now)`; 400 with message on `ClaimError`.
- `/api/overview` gains `week: engine.week_boards()`, `goal: engine.goals_state()["current"]`, awards from season deltas (about 25 titles), per-player `facts`.
- Fun facts: distance vs Great Britain length 1,407 km ("walked the length of Britain 0.3 times"), favourite block (top `mined:*`), nemesis (top `killed_by:*`), most used item (top `used:*`).
- [ ] **Step 1: Failing tests:** `test_claim_requires_same_origin`, `test_unlinked_user_sees_not_linked`, `test_challenges_payload_shape`, `test_awards_use_season_delta` (test data before season mark not counted), `test_fun_facts`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement API, page and front-end (cards, progress bars, Claim buttons with busy state and toast, countdowns, hall of fame, goal bar on home, week top-3 on home, facts in the player card). **Step 4:** Pass.
- [ ] **Step 5:** Commit `site: challenges page, claims, weekly boards, goals, more awards`.

### Task 6: Admin "Stats & challenges" tab

**Files:**
- Modify: `web/app/admin.py`, `web/app/templates/admin.html`, `web/app/static/admin.js`, `admin.css`
- Test: `web/tests/test_admin_progress.py`

**Interfaces (all under `/admin/api/progress`, writes `same_origin` + audited):**
- `GET /` → current picks, pools, goals, links, reward log.
- `POST /pools` `{pools}` → `save_pools` (400 with message on bad input).
- `POST /goals` `{goals}`.
- `POST /swap` `{iid, pool_id}`; `POST /reroll` `{kind}`.
- `POST /links` `{email, name}`; `POST /links/remove` `{email}`.
- `POST /give-again` `{key, uuid}`; `POST /test-reward` `{items}` (gives to the admin's own linked player).
- `POST /reset` `{confirm: "reset"}`.
- [ ] **Step 1: Failing tests:** `test_admin_only`, `test_bad_pool_rejected_and_file_unchanged`, `test_reset_needs_typed_confirm`, `test_swap_keeps_claims`, `test_writes_are_audited`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement routes and the tab (tables with inline editing, item rows, typed-confirm dialog for reset). **Step 4:** Pass.
- [ ] **Step 5:** Commit `admin: stats and challenges tab`.

### Task 7: Deploy and document

**Files:**
- Modify: `docker-compose.yml` (web volume `/home/tnv/mc-onboard/data:/onboard:ro`, env `ONBOARD_DB=/onboard/onboard.db`), `README.md`, `AGENT.md`; infra note `apps/minecraft`.

- [ ] **Step 1:** Back up: `tar czf ~/backups/minecraft-web-data_<ts>.tgz -C ~/minecraft web-data`; `docker tag minecraft-web:latest minecraft-web:rollback-<ts>`.
- [ ] **Step 2:** `docker compose up -d --build web`; check `curl -s localhost:8106/healthz` → `{"ok":true}`; `docker logs minecraft-web` has no traceback; `progress.db` has a `season` mark and 2 daily + 3 weekly instances.
- [ ] **Step 3:** Live check without anyone online: link Tamair's email to his player, claim a test reward → refused with "Join the server, then press Claim" and no row left in `claims`. The in-game give is confirmed by Tamair later (Test reward button while he is online). Then Reset to drop test entries.
- [ ] **Step 4:** `smoke-test` → 0 FAIL.
- [ ] **Step 5:** Docs (README section, AGENT.md current state, infra note), commit `deploy: stats and challenges`.
