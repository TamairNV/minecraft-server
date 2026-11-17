# Stats, challenges and rewards: design

Date: 2026-11-11. Status: draft for Tamair's review.

## Goal

Keep friends playing past the first week. The mc.tamairnv.co.uk site gets weekly leaderboards with a hall of fame,
daily and weekly challenges with rewards players claim on the site, community goals everyone fills together, more
awards and fun player profiles, and an admin "reset stats" button to clear test data.

## What Tamair decided

- Features: weekly leaderboards, weekly challenges, community goals, more awards and profiles, daily challenges.
- Rewards are in-game items. Weekly challenges have two tiers: everyone who finishes, plus a bonus for the first to finish.
  Daily challenges give small rewards (e.g. 32 oak logs, 10 iron ingots).
- Rewards are **claimed**: the player presses Claim on the site while they are in the game.
- Tamair can edit challenges and rewards on the admin page. Challenges are auto-picked from pools; Tamair can override.
- 2 daily challenges (reset at 00:00 UK time) and 3 weekly challenges (reset Monday 00:00 UK time).
- Website login to Minecraft name comes from the Discord sign-up bot (mc-onboard), which already stores each approved
  player's email and whitelist name. Tamair can add manual links for anyone not signed up through the bot.
- Reset is site-side: the game's own stat files are never touched.

## How progress is measured

The game keeps per-player totals in `world/players/stats/<uuid>.json` (categories custom, mined, crafted, used, broken,
picked_up, dropped, killed, killed_by). They are written on autosave (about every 5 min) and on logout.

The site stores **marks**: a copy of every player's flattened totals (`"mined:minecraft:stone": 812`) at a moment in time,
in `web-data/progress.db` (SQLite). Marks are taken at:

- season start (the reset button, or the first run),
- every day start (00:00 Europe/London),
- every week start (Monday 00:00 Europe/London).

Before taking a mark the site sends `save-all` and waits for "Saved the game" (up to 10 s), so the files are fresh.
Progress for a period = current totals minus the mark at the period's start. A player with no row in a mark started at 0.
Current totals are read from the files and cached for 30 s. If the server was down at a boundary, the mark is taken from
the files as they are (nobody could play, so they are exact).

A background thread in the web app (one uvicorn worker) wakes every 60 s to: take due marks, roll days and weeks over,
and detect challenge completions (recording the time each player first reached the target, which decides
first-to-finish). Ties within one check go to the player with the most progress, then alphabetical.

## Metrics

A metric is a stat category plus one or more key patterns, summed: e.g. `{"cat": "mined", "keys": ["minecraft:*_log"]}`
or `{"cat": "custom", "keys": ["minecraft:fish_caught"]}`. Distance metrics sum the `*_one_cm` keys and display km/m.
Play time displays hours. The same metric format is used by challenges, community goals, weekly leaderboards and awards.

## Player-facing pages

- **New page /challenges** (nav "Challenges"):
  - Today: 2 daily challenge cards (goal, reward, the viewer's progress bar, Claim button), countdown to midnight.
  - This week: 3 weekly cards (goal, everyone reward, first-to-finish reward and who got it, progress bars for every
    player, Claim button for the viewer), countdown to Monday.
  - Community goals: server-wide progress bars; when one is done the next in the list starts, with a chat/Discord shout.
  - Unclaimed rewards list for the viewer, with expiry.
- **Home**: a "This week" block (top 3 per weekly leaderboard: hours, blocks mined, distance, mob kills) and the
  current community goal bar.
- **Players**: awards grow from 6 to about 25 titles (season totals since the last reset). The player card gains fun
  facts: e.g. distance as "walked the length of Britain 0.3 times", favourite block mined, nemesis mob (most killed by),
  most used item, and a weekly hall-of-fame count.
- **Hall of fame** (section on /challenges): past weeks' leaderboard winners and challenge first-finishers.

## Claiming

1. The viewer's website login (Cloudflare Access email) is mapped to a Minecraft name: approved rows in
   `~/mc-onboard/data/onboard.db` (mounted read-only into minecraft-web at /onboard, opened `mode=ro`), then manual links
   in `web-data/links.json` (manual wins). Names are resolved to UUIDs through whitelist.json / usercache.json.
   Unlinked viewers see "Your login isn't linked to a Minecraft name yet, ask Tamair".
2. Claim needs the player online in the game: the site runs `list` and looks for the name. If not online:
   "Join the server, then press Claim".
3. The site sends `give <name> <item> <count>` for each item of the reward and waits for the game's "Gave ..." line.
   Only then is the reward marked claimed. A full inventory drops the items at the player's feet (vanilla behaviour).
4. Each reward can be claimed once (unique key: player + challenge instance + tier). Claims are serialised with a lock.
5. Expiry: daily rewards can be claimed for 7 days, weekly ones for 14 days after the week ends.
6. Claims go to `audit.jsonl` and the reward log.

Rate limit: 1 claim request per player per 3 s.

## Rewards and command safety

A reward is a list of up to 5 `{item, count}`. `item` is a Minecraft item id with optional components, e.g.
`minecraft:enchanted_book[stored_enchantments={"minecraft:mending":1}]`. Validation: matches
`^minecraft:[a-z0-9_]+(\[[A-Za-z0-9_:{}\[\]=",. '-]{1,200}\])?$`, no newlines, and the id must be in the item list read
from the server jar's registry at startup (if that list can't be read, the regex alone applies). Count 1 to 256.
Player names come from the link and must match `^[A-Za-z0-9_]{3,16}$`. Nothing a player types reaches the console.
Admin has a "Test reward" button that gives a reward to Tamair's own linked player.

## Challenge pools

- `web-data/challenges.json`: `daily` and `weekly` pools. Each entry: id, text ("Catch {n} fish"), metric, target,
  reward (daily) or reward + bonus (weekly), enabled. Pre-filled with about 20 daily and 30 weekly challenges.
- Picks: at each rollover, 2 dailies and 3 weeklies are picked at random from enabled entries, avoiding the previous
  period's picks. Stored as challenge instances (period, pool id, a frozen copy of text, metric, target and rewards, so
  editing the pool never changes a running challenge).
- Admin can swap any current pick for another pool entry or re-roll the period. Swapping keeps existing claims.

## Community goals

`web-data/goals.json`: ordered list of `{id, text, metric, target}` (e.g. "Mine 1,000,000 blocks together"). Progress =
sum over all players since season start. The first unfinished goal is shown; when it's reached the site announces it
(chat `tellraw @a` and `discord broadcast`) and moves to the next. No item reward (keeps it simple; can add later).

## Weekly leaderboards and hall of fame

Weekly boards: hours played, blocks mined, distance, mob kills. At the Monday rollover the site stores last week's top 3
per board and each weekly challenge's first finisher in `hall_of_fame`, and announces the winners in chat and on Discord.

## Announcements

`tellraw @a` with a JSON text component built from fixed templates (names validated as above), and
`discord broadcast <text>` (DiscordSRV). Announced: weekly winners on Monday, a first-to-finish, a community goal reached.

## Admin: new tab "Stats & challenges"

- Today / this week: current picks with swap and re-roll.
- Challenge pools: editable table per pool (text, metric, target, rewards, enabled), add/delete, validation errors shown.
- Community goals: editable ordered list.
- Links: list from the sign-up bot (read-only) plus manual links (add/remove).
- Reward log: claimed and unclaimed rewards, with a Give again button (sends the reward again; audited).
- Reset stats: typed confirmation ("reset"). Takes a new season mark, starts a new day and week (new picks), clears
  hall of fame, completions and claims. Tamair's test data is gone from every board. The old db is copied to
  `web-data/progress-backups/` first.

## Files

- `web/app/progress.py`: marks, metrics, periods, rollovers, completions, leaderboards, goals, awards, fun facts.
- `web/app/rewards.py`: reward validation, item registry, claim flow, give/confirm, announcements.
- `web/app/links.py`: email to player mapping (onboard db + manual).
- `web/app/challenges_default.py`: default pools and goals.
- `web/app/main.py`: /challenges page and `/api/challenges`, `/api/claim`, background thread start.
- `web/app/admin.py`: `/admin/api/progress/*` routes (same_origin + admin role, audited).
- Templates and static: challenges.html, home/players additions, admin tab.
- Compose: mount `/home/tnv/mc-onboard/data:/onboard:ro` into web.
- Tests: metric sums and patterns, deltas against marks, day/week boundaries in Europe/London (including the
  clock-change weekends), pick rotation, first-to-finish ordering, claim once only, claim when offline, give
  confirmation, expiry, reward validation (injection attempts), link resolution, reset.

## Not doing

- No plugin changes; progress lags by up to about 5 minutes (autosave).
- No item rewards for community goals, no per-player notifications in game beyond chat announcements.

## Rollout

Back up web-data and tag the web image; rebuild web only (no game restart). Smoke test. The first run takes the season
mark, so today's test data already counts as zero; the Reset button is there for later (e.g. after more testing).
