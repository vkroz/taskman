---
name: implement
description: Execute implementation work at any tier — task, feature, or epic. Invoke with /tm:implement <level> <id>. Task takes a file path; feature and epic take a slug.
argument-hint: task <path> | feature <slug> | epic <slug>
---

# Implement (`/tm:implement <level> <id>`)

## Arguments

| Position | Variable | Value |
|----------|----------|-------|
| `$0` | level | `task`, `feature`, or `epic` |
| `$1` | id | Task file path (for `task`) or slug (for `feature` / `epic`) |

## Argument validation

If `$0` is missing, stop and ask:
> *"What level — task, feature, or epic?"*

If `$0` is not one of `task`, `feature`, `epic`, stop and list the valid set.

If `$1` is missing, stop and ask the user for the task path or slug.

## Lifecycle actions — always via taskman.sh

All file moves and status transitions go through [`scripts/taskman.sh`](../../scripts/taskman.sh). **Do not** `mv` task files or edit YAML `status` by hand.

```bash
./scripts/taskman.sh move <task-path> active        # backlog → active
./scripts/taskman.sh move <task-path> done          # active  → done
./scripts/taskman.sh feature close <slug>           # close feature when all tasks done
./scripts/taskman.sh epic close <slug>              # close epic when all features done
```

## Design gap escalation protocol

When `/tm:implement task` detects a meaningful design gap during detailed design (missing scope detail, ambiguous acceptance criterion, undefined interface contract), it halts, writes a gap-log entry, and routes the user to the upstream skill that should address it. This protocol preserves the Design/Implementation separation: implementers do not silently improvise design.

### Gap-log file format

Gap-log files live at `tasks/gaps/<YYYYMMDD-HHMMSS>_<task-slug>.md`. One file per detected gap. Written via the Write tool directly — **not** through `taskman.sh` (gaps are observability records, not lifecycle units).

Frontmatter:

```yaml
---
detected: 2026-05-26T15:23:00Z          # ISO 8601 with timezone
task: <task-slug>
task_path: tasks/active/YYYYMMDD_<slug>.md
suspected_level: task | feature | epic | architecture
status: open | resolved
---
```

Body sections (all required):

```markdown
# Gap: <one-line description>

## Description

<what design information is missing or ambiguous>

## Detection context

<which step of /tm:implement task surfaced this; what the implementer was trying to do at the moment of detection>

## Suspected upstream

<which level needs revision; which skill the user should invoke>

## Resolution

<filled when the gap is marked resolved; left empty initially>
```

### Routing message

On detection, the skill prints (substitute the actual values):

```
Design gap detected. Halted before coding.

Gap-log: tasks/gaps/<YYYYMMDD-HHMMSS>_<task-slug>.md
Suspected upstream: <level>
Recommended next step: /tm:design <level>     # or /tm:plan <level>, or "edit the task body" for task-level gaps

Re-invoke /tm:implement task <task-path> after the upstream skill closes.
```

Then stop. Do not proceed to architecture checkpoint or coding. If multiple gaps were detected, write one file per gap and list them all in the message.

### Resume protocol

At the start of every `/tm:implement task` invocation, **before** activation or design work:

1. Scan `tasks/gaps/` for files where YAML `task == <this task slug>` and `status == open`.
2. If any open gaps exist, surface them to the user with their paths and suspected levels.
3. Ask: *"Continue (upstream is now addressed — mark gap(s) resolved) or re-route (upstream is still incomplete)?"*
4. **Mark resolved** — Edit each named gap file via the Edit tool: set `status: resolved`, fill in `## Resolution` with a one-line description of what the upstream skill changed. Then proceed with the normal task flow.
5. **Re-route** — Reprint the routing message for the still-open gap(s) and stop.

The skill checks resume state even on the first invocation — a prior session may have logged gaps that were never resolved.

## Dispatch

Read `$0`. Run exactly one of the branches below.

---

## $0 = task

### Usage

```
/tm:implement task @tasks/backlog/20260419_wire_up_widget.md
```

### Preconditions
- A valid task file under `tasks/backlog/` or `tasks/active/`.
- If the task carries `feature: <slug>`, also read the matching feature file under `tasks/features/`.
- If the parent feature carries `epic: <slug>`, also read the matching epic file under `tasks/epics/`.
- For work that **changes high-level architecture**, stop and discuss with the user before coding (**architecture impact gate**).

### Required reading (re-read at task start, do not rely on prior context)
- The task file itself (`$1`).
- The parent feature file (if the task has `feature:`).
- The parent epic file (if the feature has `epic:`).
- `docs/architecture.md`.
- Relevant sections of `docs/spec.md` and `docs/requirements.md` referenced by the task.

### Execution steps

0. **Resume check** — Before activation, scan `tasks/gaps/` per the **Design gap escalation protocol** section above. If any open gaps name this task, halt and prompt the user (mark resolved, or re-route). Only proceed past this step when no open gaps remain for this task.

1. **Activate the task**
   - `./scripts/taskman.sh move $1 active` (does the folder move and YAML update atomically).

2. **Read and understand**
   - Read everything in the required reading list.
   - **Detailed design first** — API shapes, data touched, interfaces, key logic — before any code.

3. **Design gap detection**
   - Enumerate ambiguities or missing information surfaced during detailed design. For each: what is unclear, and at what tier does the answer belong (`task` body, `feature` scope, `epic` design, or product `architecture`)?
   - If any gaps exist, follow the **Design gap escalation protocol** above: write one gap-log entry per gap, print the routing message, and stop. Do not proceed to step 4.
   - If no gaps, continue.

4. **Architecture compliance checkpoint**
   - Verify the detailed design against `docs/architecture.md` constraints.
   - If it violates any constraint, flag to the user before coding — do not proceed until resolved.

5. **Execute**
   - Ask for clarification if the task is ambiguous.
   - For multi-step or risky work, get user approval before executing.
   - Implement, then write unit tests as appropriate.
   - Stay within task scope; flag scope creep.

6. **Complete (after user confirms)**
   - Append a `## Summary` section to the task file describing steps completed, changes made, notable decisions, links.
   - `./scripts/taskman.sh move <active-path> done`.
   - If the task had `feature: <slug>` and this was the last open task, offer to run `./scripts/taskman.sh feature close <slug>`.

7. **Documentation** — If behavior or intent in docs is wrong or incomplete, update `docs/*` (dependency order: vision → spec → requirements → architecture → tasks).

### After single-task flows (bugfix / ad-hoc)
When the user expects integration coverage, run **`/tm:validate feature`** for the affected scope before hand-off. Full regression uses **`/tm:validate product`**.

### Task-branch discipline
- Use `taskman.sh` for every state transition. Never `mv` or hand-edit YAML status.
- Never expand scope beyond the task body without user instruction.
- Append the `## Summary` section before moving to `done` — required by the task-management standard.

---

## $0 = feature

### Usage

```
/tm:implement feature worktree_isolated_dev_stacks
```

### Preconditions
- A feature file exists for `$1`. Confirm: `./scripts/taskman.sh feature show $1`.
- If not, stop: *Cannot run — feature not found. Create it first with `/tm:define feature`.*

### Workflow

1. **Read the feature file** at `tasks/features/YYYYMMDD_<slug>.md`. Understand problem / objective / acceptance criteria.

2. **Enumerate member tasks**
   ```bash
   ./scripts/taskman.sh feature show $1
   ./scripts/taskman.sh list tasks --feature $1 --status backlog
   ./scripts/taskman.sh list tasks --feature $1 --status active
   ```
   Plan execution in DAG order. If the feature body numbers requirements (R1, R2, …) or lists tasks in order, honor that ordering.

3. **For each open task, in order**, run the full per-task contract by invoking the `$0 = task` branch of this skill on that task:
   - Activate (`move … active`).
   - Required reading (task file + feature file + epic file if any + architecture).
   - Detailed design → architecture compliance checkpoint → implement with tests.
   - User confirmation.
   - Append `## Summary` to the task file.
   - Complete (`move … done`).

   Run tasks **sequentially** (v1 — no parallel agent execution).

4. If a task is blocked, stop and report; do not skip silently.

5. **When every member task is in `done/`**, run **`/tm:validate feature`** scoped to the feature, then:
   - Append a `## Summary` section to the feature file.
   - Close the feature:
     ```bash
     ./scripts/taskman.sh feature close $1
     ```
   This verifies the precondition (all members done) and sets the feature's status to `done`.

6. **Interrupts** — The user may stop at any task boundary; report what's complete and what remains.

### Feature-branch discipline
- Respect scope in the feature body; flag conflicts with the user.
- Do not close the feature until the user has approved the final task and integration results.
- Bugs that surface during implementation: create them via `/tm:define task` with `--kind bug --feature $1` so they remain traceable.
- Use `taskman.sh` for every state transition. Never `mv` or hand-edit YAML status.

---

## $0 = epic

### Usage

```
/tm:implement epic aws_onboarding
```

### Preconditions
- An epic file exists for `$1` at `tasks/epics/YYYYMMDD_<slug>.md`. Confirm: `./scripts/taskman.sh list epics`.
- If not, stop: *Cannot run — epic not found. Create it first with `/tm:define epic`.*
- The epic body's `## Linked features` lists the features that compose the epic, in execution order.

### Workflow

1. **Read the epic file** at `tasks/epics/YYYYMMDD_<slug>.md`. Understand problem / objective / scope / acceptance criteria / linked features.

2. **Enumerate member features**
   ```bash
   ./scripts/taskman.sh list features --epic $1 --status backlog
   ./scripts/taskman.sh list features --epic $1 --status active
   ```
   Cross-check the enumerated features against the epic body's `## Linked features` ordering. If the body has a different order than the slug list, honor the body ordering.

3. **For each open feature, in order**, run the full per-feature contract by invoking the `$0 = feature` branch on that feature:
   - Read the feature file and its linked tasks.
   - Iterate through each task using the `$0 = task` branch.
   - User confirmation per task.
   - Append `## Summary` to each task file.
   - When every task of the feature is `done`, run **`/tm:validate feature`** scoped to that feature.
   - Append `## Summary` to the feature file.
   - Close the feature: `./scripts/taskman.sh feature close <feature-slug>`.

   Run features **sequentially**. Within a feature, tasks also run sequentially (v1 — no parallel agent execution).

4. If a feature is blocked, stop and report; do not skip silently.

5. **Feature boundary gate** — After each feature is closed and its integration test passes, the user reviews the results before the next feature starts. Do not auto-advance.

6. **When every linked feature is in `done`**, run **`/tm:validate epic`** scoped to the whole epic (cross-feature interactions), then:
   - Append a `## Summary` section to the epic file.
   - Close the epic:
     ```bash
     ./scripts/taskman.sh epic close $1
     ```
   This verifies the precondition (all linked features done) and sets the epic's status to `done`.

7. **Interrupts** — The user may stop at any feature boundary; report what's complete and what remains.

8. After the epic closes, point the user to **`/tm:validate product`** for full product verification if the epic represents a release-ready slice.

### Epic-branch discipline
- Respect scope in the epic body; flag conflicts with the user.
- Do not start the next feature without user approval.
- Do not expand scope beyond the approved epic without user instruction.
- Same architecture-impact rules as the task and feature branches — any architectural change requires a stop-and-discuss before coding.
- Use `taskman.sh` for every state transition. Never `mv` or hand-edit YAML status.
