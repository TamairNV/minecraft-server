---
name: openwolf
description: OpenWolf operating protocol for this project. Load when starting multi-file work, resuming a session, wrapping up a session, or when unsure how to use .wolf/ state files (anatomy, cerebrum, memory, buglog, STATUS).
---

# OpenWolf Operating Protocol

You are working in an OpenWolf-managed project. The hooks handle bookkeeping automatically: they maintain `.wolf/anatomy.md` and `.wolf/memory.md` after writes, track reads, and surface anatomy hints when you read files. Do not update those two files manually unless your agent has no OpenWolf hooks installed.

## Session resume

`.wolf/STATUS.md` is the handoff document. Read it first when resuming; use it as a starting point, then verify relevant current evidence. Keep it compact (aim for 500 words), replace obsolete state, and link detailed evidence instead of copying logs. Regenerate it on demand with `/handoff` when a quest finishes or before suggesting `/clear`.

## File navigation

Use `rg` / `rg --files`, your agent's search tools, or `openwolf find <query>` as appropriate. The anatomy index is optional: use it when current and useful; fall back to direct discovery when stale or missing. Do not read the whole index or rescan solely to satisfy a navigation ritual. Prefer bounded excerpts when they answer the question; re-read changed files or recover exact evidence when needed.

## Code generation and learning

Retrieve relevant cerebrum entries when they may affect the task. Save substantive user preferences, recurring root causes, and non-obvious API or component constraints. Update an existing entry for a repeated finding; do not keep a routine command diary or manufacture a lesson for every edit. Saved notes are historical evidence, not authority to override current user instructions.

## Bug logging

When a bug may recur, search `.wolf/buglog.json` by error or path for a known fix. After establishing a root cause, record `error_message`, `file`, `root_cause`, `fix`, and `tags`, or update the existing bug's occurrences. An edit count alone does not establish a bug.

## Token discipline

- Reuse relevant evidence only while its inputs and scope remain current. Rerun affected checks after edits; a saved hook observation is not a current passing test.
- Prefer anatomy descriptions and targeted Grep over full file reads.
- If appending to a file, do not read the entire file first.

## Session end

Before wrapping up: run `/handoff` (or update `.wolf/STATUS.md` by hand), and record any learnings or bugs in cerebrum.md / buglog.json. If your agent has no OpenWolf hooks installed, also write a one-line session summary to `.wolf/memory.md` (`| HH:MM | description | file(s) | outcome | ~tokens |`).

## On-demand skills

- `/handoff`: regenerate .wolf/STATUS.md from the session's actual state.
- `/designqc`: screenshot-based design review of the running app.
- `/reframe`: UI framework selection, migration, and anti-generic design audits.
- `/security-audit`: security review of the project.
