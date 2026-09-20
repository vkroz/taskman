---
name: docs-sync
description: Process the doc-sync queue. Reads tasks/docs-sync-queue.txt, reviews upstream docs (vision/spec/requirements/architecture/linked spec) for drift against each closed unit per rules/doc-maintenance.md, proposes diffs, applies after user approval, clears processed entries. Triggered manually or after taskman.sh prints the "Doc-sync pending" hint on a close action.
argument-hint: (no args — processes all pending entries) | <line-number> | <slug-or-path>
---

# Docs sync (`/tm:docs-sync`)

## Purpose

Every time a work unit closes (task → done, feature close, epic close), `taskman.sh` appends an entry to `tasks/docs-sync-queue.txt` and prints a "Doc-sync pending" hint. This skill processes that queue: it reviews upstream `docs/*` files for drift against each closed unit, proposes diffs, and clears the entry once the user approves.

Follow `plugins/tm/rules/doc-maintenance.md` for what to check and when no update is needed.

## Arguments

| Position | Variable | Value |
|----------|----------|-------|
| `$1` | filter | Optional. Without an argument, process all pending entries. With a number, process only that line of the queue (1-indexed). With a slug or path, process only entries matching that id. |

## Preconditions

- `tasks/docs-sync-queue.txt` exists. If missing or empty, report *"No pending doc-sync entries."* and stop.
- For each entry being processed, the named unit file must exist (closed task in `tasks/done/`, feature in `tasks/features/`, epic in `tasks/epics/`).

## Workflow

1. **Read the queue** — Load `tasks/docs-sync-queue.txt`. Each line:
   ```
   <ISO-8601 timestamp> <kind> <id-or-path>
   ```
   `kind` is `task`, `feature`, or `epic`. For task, `id-or-path` is the full path under `tasks/done/`. For feature/epic, it is the slug.

   Apply `$1` filter if provided.

2. **For each entry, in order:**

   a. **Load context.** Read the closed unit's file. If it carries `feature: <slug>` or `epic: <slug>`, also read the parent. If the closed unit links to a spec under `docs/<area>/.../-spec.md`, read that spec.

   b. **Check upstream docs in dependency order** (per `rules/doc-maintenance.md`):
      1. `docs/vision.md`
      2. `docs/spec.md`
      3. `docs/requirements.md`
      4. `docs/architecture.md`
      5. Linked spec under `docs/<area>/.../-spec.md` (if applicable)

      For each doc, ask:
      - Does the doc state a behavior or intent that the closed work changed or invalidated?
      - Does the doc lack a behavior or intent that the closed work now exhibits?

      If yes to either, propose a diff. If no, mark the doc as "no update needed".

   c. **Surface the result** — Present one of:
      - **Proposed diffs.** Show file path, before/after for each changed section, one-sentence rationale per diff. List in dependency order.
      - **Explicit no-op.** *"No doc updates needed for `<entry>` — closure does not affect vision, spec, requirements, architecture, or linked spec."*

      Silence is worse than a deliberate no-op note — always say one or the other.

   d. **User review** — Wait for the user to approve, reject, or revise each diff. Apply approved diffs via the Edit tool. Do not auto-commit (git commits stay user-owned).

   e. **Clear the entry.** After the user confirms processing for this entry is complete (regardless of whether any diffs were applied), remove its line from `tasks/docs-sync-queue.txt`.

3. **Final report** — Print:
   - Entries processed
   - Files changed (with paths)
   - Entries with no-op verdicts
   - Any entries skipped (with reason)

## Queue clearing

The queue is a simple line-oriented file. To remove a processed line, read all lines, filter out the processed one, write back via the Write tool.

If all entries process cleanly, the file ends empty; do not delete the file itself (taskman expects it to exist and re-appends).

If a user interrupts mid-run, leave un-processed entries in the queue — they will be picked up on the next invocation.

## Discipline

- **Propose, don't commit.** The user owns the decision on whether a diff lands. Apply via Edit tool only after explicit approval. Never run `git add` or `git commit` from this skill.
- **Stay in scope.** This skill reviews `docs/*` for drift introduced by the closed unit. Do not refactor unrelated docs, do not chase typos in passing.
- **Explicit no-op is mandatory.** Per `rules/doc-maintenance.md`, silence is worse than a deliberate no-op note — always state "no updates needed" if that's the verdict.
- **One entry at a time when uncertain.** If the queue has many entries spanning broad changes, process them in dependency order (vision → spec → requirements → architecture). Avoid bundling cross-cutting diffs from multiple entries into a single review — surface per-entry so the user can triage.

## No-active-session fallback

When `taskman.sh close` runs from a direct CLI (no Claude session active), entries accumulate in the queue. The queue file is durable on disk. The next time the user runs `taskman.sh list` in any Claude session, taskman prints a WARN hint about pending entries. Run `/tm:docs-sync` then.

Users wanting automatic surfacing on session start can add an opt-in Claude Code SessionStart hook — see `plugins/tm/README.md` for a copy-paste snippet.
