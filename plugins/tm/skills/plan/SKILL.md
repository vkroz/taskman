---
name: plan
description: Focused revision of an existing unit's decomposition — epic into features, or feature into tasks. Invoke with /tm:plan <level>. Delegates to the Planner sub-agent in refine + plan-only mode.
argument-hint: epic | feature
---

# Plan (`/tm:plan <level>`)

## Arguments

| Position | Variable | Value |
|----------|----------|-------|
| `$0` | level | `epic` or `feature` |

## Argument validation

If `$0` is missing, stop and ask:
> *"What level — epic or feature?"*

If `$0` is not one of `epic`, `feature`, stop and list the valid set.

## Dispatch

Read `$0`. Run exactly one of the branches below.

---

## $0 = epic

### Preconditions

The epic file must exist under `tasks/epics/`. If it does not, stop and direct the user to `/tm:define epic`.

### Workflow

1. **Locate** — Confirm the epic slug. Read the epic file and its current `## Linked features`. List the existing feature files under `tasks/features/` that carry `epic: <slug>`.

2. **Identify the decomposition gap** — Ask the user what needs to change: a missing feature, an obsolete feature, reordering, splitting a feature, merging two. Surface what is currently in `done` vs `active` vs `backlog` so the user knows what is in flight.

3. **Delegate to the Planner.** Invoke via the Agent tool with `subagent_type: planner` and the following brief:
   - `level=epic`
   - `mode=refine`
   - `title=<existing epic file path>`
   - `initial_scope=plan-only — <user description of decomposition change>; existing features: <list>`
   - `upstream=<docs/vision.md, docs/spec.md, docs/architecture.md if relevant>`

   The Planner returns a `## Decomposition` delta — features to add, remove, or re-order — plus a one-sentence rationale. It does not touch the epic body (that's `/tm:design epic`).

4. **User review** — Show the proposed feature changes. Iterate until approved.

5. **Persist** —
   - For each new feature: invoke `/tm:define feature --epic <slug>` (or `taskman.sh new feature --epic <slug>` if the user provides bodies inline).
   - For each obsolete feature still in `backlog`: confirm with the user, then `taskman.sh discard <path>`.
   - For ordering changes: edit the epic's `## Linked features` list in place via the Edit tool.
   - Features already in `active` or `done` are not modified; surface them in the report.

6. **Report** — One-line summary: features added, features removed, ordering changes.

### Discipline

- Plan refinement does not touch feature bodies — for that, use `/tm:design feature`.
- Do not move features between status folders here.
- If the user asks to re-state the epic's objective or scope, redirect to `/tm:design epic`.

Stop.

---

## $0 = feature

### Preconditions

The feature file must exist under `tasks/features/`. If it does not, stop and direct the user to `/tm:define feature`.

### Workflow

1. **Locate** — Confirm the feature slug. Read the feature file and its current `## Tasks` list. List the existing task files under `tasks/{backlog,active,done}/` that carry `feature: <slug>`.

2. **Identify the decomposition gap** — Ask the user what needs to change: a missing task, an obsolete task, reordering, splitting, merging. Surface tasks already in `active` or `done` so the user knows what is in flight.

3. **Delegate to the Planner.** Invoke via the Agent tool with `subagent_type: planner` and the following brief:
   - `level=feature`
   - `mode=refine`
   - `title=<existing feature file path>`
   - `initial_scope=plan-only — <user description of decomposition change>; existing tasks: <list>`
   - `upstream=<parent epic file if any, linked spec, docs/architecture.md if relevant>`

   The Planner returns a `## Decomposition` delta — tasks to add, remove, or re-order — plus a one-sentence rationale. It does not touch the feature body (that's `/tm:design feature`).

4. **User review** — Show the proposed task changes. Iterate until approved.

5. **Persist** —
   - For each new task: invoke `/tm:define task` with `--feature <slug>` (or call `taskman.sh new task --feature <slug>` directly if the user provides bodies inline).
   - For each obsolete task still in `backlog`: confirm with the user, then `taskman.sh discard <path>`.
   - For ordering changes: edit the feature's `## Tasks` list in place via the Edit tool.
   - Tasks already in `active` or `done` are not modified; surface them in the report.

6. **Report** — One-line summary: tasks added, tasks removed, ordering changes.

### Discipline

- Plan refinement does not touch task bodies — for that, use `/tm:design task` (not yet implemented; edit the task file directly under guidance for now).
- Do not move tasks between status folders here.
- If the user asks to re-state the feature's objective or acceptance criteria, redirect to `/tm:design feature`.

Stop.
