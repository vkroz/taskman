---
name: define
description: Define a work unit at any tier — product, epic, feature, or task. Invoke with /tm:define <level>. Produces vision/spec/requirements (product), an epic + linked features (epic), a feature + linked tasks (feature), or a single task/bug ticket (task).
argument-hint: product | epic | feature | task [title|kind]
---

# Define (`/tm:define <level>`)

## Arguments

| Position | Variable | Value |
|----------|----------|-------|
| `$0` | level | `product`, `epic`, `feature`, or `task` |
| `$1+` | hints | Optional title or, for task, kind (`task` \| `bug`) |

## Argument validation

If `$0` is missing, stop and ask:
> *"What level — product, epic, feature, or task?"*

If `$0` is not one of `product`, `epic`, `feature`, `task`, stop and list the valid set.

## Dispatch

Read `$0`. Run exactly one of the four workflows below.

## Composition via the Planner sub-agent

For `epic`, `feature`, and `task` levels, composition of the unit body (and decomposition into child units, where applicable) is delegated to the Planner sub-agent (`plugins/tm/agents/planner.md`). This skill stays focused on user dialog, slug confirmation, and `taskman.sh` persistence.

The `product` branch does **not** invoke the Planner — vision/spec/requirements drafting is pre-Design and stays in the parent session. Product-level Design (architecture) lives in `/tm:design product`; the epic list comes from `/tm:define epic` calls afterward.

When a workflow below says "delegate to the Planner", invoke it via the Agent tool with `subagent_type: planner` and a brief containing:

- `level` — `epic` | `feature` | `task`
- `mode` — `new`
- `title` — the human-readable title
- `initial_scope` — what the user said in the dialog so far (problem, objective, scope, acceptance criteria, any decomposition discussed)
- `upstream` — paths to relevant docs (`docs/vision.md`, `docs/spec.md`, `docs/requirements.md`, `docs/architecture.md`; parent epic or feature file)

The Planner returns:
- `## Body` — full markdown body for the unit, matching the required sections from `rules/task-composition.md`.
- `## Decomposition` — ordered child list (empty for `task`).
- Optional `## Questions for parent` — surface to the user, gather answers, re-invoke the Planner with the updated brief.

Pipe the returned Body to `./scripts/taskman.sh new <level> --slug … --title …`. For each Decomposition entry, invoke `./scripts/taskman.sh new` for that child after composing its own body (which may delegate to the Planner one more level down).

---

## $0 = product

### Preconditions
- **None.** This branch may create `docs/` and initial documents if they do not exist.
- For **incremental features**, existing `docs/vision.md`, `docs/spec.md`, and `docs/requirements.md` are the baseline; produce **additions/amendments** so the product (including the new feature) is fully described — no separate "feature-only" docs.

### Outputs
| Artifact | Path |
|----------|------|
| Vision | `docs/vision.md` |
| Specification (product-level, cross-cutting) | `docs/spec.md` |
| Requirements (product-level, cross-cutting) | `docs/requirements.md` |

The `docs/spec.md` and `docs/requirements.md` produced here are **product-level** documents — they describe the product as a whole. Feature-scoped specs follow a different convention and live under `docs/<area>/.../-spec.md`.

### Workflow

This branch does not invoke the Planner sub-agent — drafting of `docs/vision.md`, `docs/spec.md`, and `docs/requirements.md` happens in this parent session. The Planner takes over at `/tm:design product` (architecture) and `/tm:define epic` (epic decomposition).

1. **Vision** — Interview the user (problem, users, capabilities). Draft a one-page `docs/vision.md`. Iterate until the user is satisfied with the direction.

2. **Specification** — Using the approved vision plus any references (products, docs, domain notes), draft `docs/spec.md`: functionality, user flows, interactions. Iterate.

3. **Requirements** — Draft `docs/requirements.md` to formalize and disambiguate the spec. Iterate.

4. **Validation** — Run two rubrics and produce a short **validation report**:
   - **Business case:** problem understanding, whether the spec solves it, alternatives, monetization, risks.
   - **Functional completeness:** consistency, gaps, ambiguities, missing detail for design/implementation.

5. **Consistency** — If any document changes, check downstream docs in the chain: vision → spec → requirements. State explicitly what you updated and why.

6. **Gate** — Do not treat the stage as complete until the **user** explicitly approves moving to `/tm:design product`.

**Discipline:** Do not silently skip validation. Do not proceed to architecture without user approval to leave this stage.

---

## $0 = epic

### Objective

Collaborate with the user to **define an epic** — a named functional block larger than a feature — and the set of features needed to deliver it. Produce:

1. One **epic file** under `tasks/epics/YYYYMMDD_<slug>.md` holding the functional-block-level plan.
2. Zero or more **feature files** under `tasks/features/`, each stamped with `epic: <slug>`.

This branch is **requirements + decomposition only**. Do not implement. Do not propose code-level designs. Capture WHAT and WHY at functional-block level; leave HOW to `/tm:define feature` and `/tm:implement feature` for each feature, and `/tm:implement task` for each task within. See `rules/first-principles.md` (Planning Guidelines).

### Preconditions

None strictly required. If the epic is architecture-sensitive, consider running `/tm:design product` first to produce `docs/architecture.md`. If the user is starting from vision/spec docs under `docs/`, read them before the dialog.

### When to create an epic vs a feature

Create an **epic** when the work spans multiple features that share a coherent objective at the functional-block level. Examples: "AWS onboarding", "Authentication & authorization", "Cost ingestion pipeline".

Create a **feature** directly via `/tm:define feature` when the work fits inside a single coherent slice that produces tasks at one level of depth. Most product work is a feature, not an epic. Epics are for genuinely larger blocks where decomposition by feature adds clarity.

If unclear, ask the user.

### Workflow

#### 1. Interview — problem and objective

- **Current situation** — what is true today at the functional-block level?
- **Problem statement** — what pain, risk, or gap does the epic address?
- **Objective** — the end state at the functional-block level. One or two sentences.
- **Who benefits and how** — users, operators, internal teams.
- Apply first-principles checks: is the problem real? what happens if we do nothing? is there a simpler shape (one feature instead of an epic)?

#### 2. Interview — scope and success

- **In scope / out of scope** — call out adjacent functional blocks that will NOT be included.
- **Acceptance criteria** — observable conditions at functional-block level that prove the epic is complete. These are coarser than feature-level acceptance criteria.

Skip **Risks and mitigations** unless each risk has a named owner and a mitigation plan the team will track.

#### 3. Propose the slug and title

Slug format: `[a-z][a-z0-9_]*` — lowercase, underscores, stable over the epic's life. Reuse in branch names by convention.

Example: title *"AWS onboarding"* → slug `aws_onboarding`.

Confirm both with the user.

#### 4. Break the epic into features

List the features in order, each with:

- A short title (will become the feature slug)
- A one-line summary of what it delivers
- Whether you will create the feature file now (`--epic <slug>` flag) or defer until later

Keep features small enough that each is itself a coherent slice with its own tasks. Resist the temptation to design the implementation here.

#### 5. Delegate body composition to the Planner

Hand the dialog outputs (problem, objective, scope, acceptance criteria, feature breakdown from step 4) to the Planner per the **Composition via the Planner sub-agent** section above, with `level=epic`. The Planner returns:

- `## Body` — the epic body matching the required sections (`## Problem statement`, `## Objective`, `## Scope`, `## Acceptance criteria`, `## Linked features`).
- `## Decomposition` — ordered feature list that should mirror your step-4 list. Reconcile any divergence with the user before persisting.

The Planner enforces the body shape (including what NOT to include — `Requirements`, freeform `Risks and mitigations`, `Success metrics`); you do not need to restate section requirements here.

#### 6. Write drafts and preview

Write the epic first, then any feature files the user wants created now. `taskman.sh new` stamps each file with `draft: true` so they are visibly mid-creation until the user approves. Feature creation will fail if the epic does not exist yet.

```bash
cat <<'EOF' | ./scripts/taskman.sh new epic --slug <slug> --title "<title>"
# epic body here
EOF
```

```bash
cat <<'EOF' | ./scripts/taskman.sh new feature --slug <feature-slug> --title "<feature title>" --epic <epic-slug>
# feature body here
EOF
```

Collect the printed paths. Re-read each file from disk and present them to the user. Apply edits in place if requested.

#### 7. Finalize or discard

On approval, finalize every draft (epic first, then features):

```bash
./scripts/taskman.sh finalize <epic-path>
./scripts/taskman.sh finalize <feature-path>
```

On rejection of any individual file, discard it:

```bash
./scripts/taskman.sh discard <path>
```

If the user rejects the epic outright, discard the epic **and** every feature created against it.

#### 8. Report and stop

Report:

- The created epic file path
- The created feature file paths (if any)
- A one-sentence next step (typically: *"Ready for `/tm:implement epic <epic-slug>`"* — or *"Run `/tm:define feature` with `--epic <epic-slug>`"* if features are still to be defined)

Do not transition any feature out of `backlog`. That belongs to `/tm:implement feature` (or `/tm:implement epic`, which iterates features).

### Epic-branch discipline

- Use `taskman.sh`. Never write epic or feature files directly.
- Always end the dialog with `taskman.sh finalize` (per file) or `taskman.sh discard` (per file). Do not leave files with `draft: true` behind.
- One epic per invocation. If the user describes two epics, suggest splitting.
- If the user asks "how should we build it?", capture as a follow-up — implementation design is not this branch's job.
- If the user describes work that fits in a single feature, suggest `/tm:define feature` instead.

---

## $0 = feature

### Objective

Collaborate with the user to **define a feature** — a named product initiative — and the set of tasks needed to deliver it. Produce:

1. One **feature file** under `tasks/features/YYYYMMDD_<slug>.md` holding the plan.
2. Zero or more **task files** under `tasks/backlog/`, each stamped with `feature: <slug>`.

This branch is **requirements + planning only**. Do not implement. Do not propose code-level designs. Capture WHAT and WHY; leave HOW to `/tm:implement feature` / `/tm:implement task`. See `rules/first-principles.md` (Planning Guidelines).

### Preconditions

None strictly required. If the feature is large and architecture-sensitive, consider running `/tm:design product` first to produce `docs/architecture.md`. If the user is starting from vision/spec docs under `docs/`, read them before the dialog. If the feature belongs to an existing epic, confirm the epic slug during the dialog and pass `--epic <slug>` when creating the feature.

### Workflow

#### 1. Interview — problem and objective

- **Current situation** — what is true today?
- **Problem statement** — what pain, risk, or gap does the feature address?
- **Objective** — the end state. One or two sentences.
- **Who benefits and how** — users, operators, internal teams.
- Apply first-principles checks: is the problem real? what happens if we do nothing? is there a simpler shape?

#### 2. Interview — scope and success

- **In scope / out of scope** — call out tempting adjacent work that will NOT be included.
- **Acceptance criteria** — testable conditions that prove the feature is complete. Include post-launch success measures here as observable conditions, not in a separate section.
- **Tasks** — ordered list of work items that deliver the feature.

Detailed requirements (R1, R2, …) belong in the **spec**, not the feature file. If the feature has no spec yet, agree with the user where it will live (`docs/<area>/.../-spec.md`) and note it in the feature body under a `## Linked spec` heading.

Skip **Risks and mitigations** unless each risk has a named owner and a mitigation plan the team will track. Otherwise the section turns into noise.

#### 3. Epic attachment

- Ask whether the feature belongs to an existing epic. If yes, confirm the slug by listing candidates: `./scripts/taskman.sh list epics`.
- If no epic, the feature is stand-alone.

#### 4. Propose the slug and title

Slug format: `[a-z][a-z0-9_]*` — lowercase, underscores, stable over the feature's life. Reuse in branch names and worktree names by convention.

Example: title *"Worktree-isolated dev stacks"* → slug `worktree_isolated_dev_stacks`.

Confirm both with the user.

#### 5. Break the feature into tasks

List the tasks in order, each with:

- A short title
- Kind (`task` or, rarely, `bug` for a known defect rolled into the feature)
- A one-line summary of what it delivers

Keep tasks small enough that each is executable in a single focused session. Resist the temptation to design the implementation here — the task file states WHAT, not HOW.

#### 6. Delegate body composition to the Planner

For the **feature file**: hand the dialog outputs (problem, objective, scope, acceptance criteria, task breakdown from step 5) to the Planner per the **Composition via the Planner sub-agent** section above, with `level=feature`. The Planner returns a feature `## Body` and a `## Decomposition` (the task list).

For each task in the decomposition: invoke the Planner again with `level=task`, `mode=new`, `title=<task title>`, `initial_scope=<task one-line summary + parent feature context>`. The Planner returns a task body matching the required sections.

`taskman.sh` validates the section names (`## Problem statement`, `## Scope`, `## Acceptance criteria`, `## Quality gates` for tasks; `## Problem statement`, `## Objective`, `## Acceptance criteria` for features) on creation; the Planner enforces them in the body it returns.

#### 7. Write drafts and preview

Write the feature first, then the tasks. `taskman.sh new` stamps each file with `draft: true` so they are visibly mid-creation until the user approves. Task creation will fail if the feature does not exist yet.

```bash
cat <<'EOF' | ./scripts/taskman.sh new feature --slug <slug> --title "<title>" [--epic <epic-slug>]
# feature body here
EOF
```

```bash
cat <<'EOF' | ./scripts/taskman.sh new task --title "<task title>" --feature <slug>
# task body here
EOF
```

Collect the printed paths. Re-read each file from disk and present them to the user. Apply edits in place if requested.

#### 8. Finalize or discard

On approval, finalize every draft (feature first, then tasks):

```bash
./scripts/taskman.sh finalize <feature-path>
./scripts/taskman.sh finalize <task-path>
```

On rejection of any individual file, discard it:

```bash
./scripts/taskman.sh discard <path>
```

If the user rejects the feature outright, discard the feature **and** every task created against it — orphaned tasks pointing at a missing feature should not be left behind.

#### 9. Report and stop

Report:

- The created feature file path
- The created task file paths
- A one-sentence next step (typically: *"Ready for `/tm:implement feature <feature-slug>`"* or per-task implementation)

Do not transition any task out of `backlog/`. That belongs to `/tm:implement task` or `/tm:implement feature`.

### Feature-branch discipline

- Use `taskman.sh`. Never write task files directly.
- Always end the dialog with `taskman.sh finalize` (per file) or `taskman.sh discard` (per file). Do not leave files with `draft: true` behind.
- One feature per invocation. If the user describes two features, suggest splitting.
- If the user asks "how should we build it?", capture as a follow-up — implementation design is not this branch's job.

### Where the feature spec lives

If the feature needs a spec document (UI layout, field-by-field contract, API payload, etc.), place it under `docs/<area>/[<subarea>/]<name>-spec.md`. Group by product area, not by feature lifecycle. Example: `docs/settings/integration/aws-onboarding-spec.md`.

Feature files under `tasks/features/` hold the plan (problem, objective, requirements). Feature specs under `docs/<area>/...` hold the implementation contract. Link one to the other.

---

## $0 = task

### Usage

```
/tm:define task                         # Claude will ask for the task kind
/tm:define task task                    # Development task
/tm:define task bug                     # Bug ticket
/tm:define task task <short title>
/tm:define task bug  <short title>
```

If the task belongs to an existing feature, mention the feature slug during the dialog — it becomes `feature: <slug>` in the YAML header. If you don't know the slug, ask: *"Does this task belong to a feature? If yes, which slug under `tasks/features/`?"* A task without a feature is **ad-hoc** — perfectly valid.

### Objective

Collaborate with the user to **define a task** with clear, non-ambiguous requirements that can be executed later — product-manager style, not implementation design.

**This branch is requirements definition only:**
- Do not implement the task.
- Do not make code/config changes beyond writing the task file (via `taskman.sh`).
- Do not propose or evaluate solutions. If the user asks "how should we build it?", capture that as a follow-up for `/tm:implement task`.

### Task kind

If `$1` was not provided, ask:
> *"What kind — development task or bug ticket?"*

Accept `task` / `feature` / `dev` as task; `bug` / `defect` / `fix` as bug.

---

### Development task — collaboration process

#### 1. Establish the problem (and whether it's real)

- Current situation — what is happening today?
- Problem statement — the concrete pain, failure, or risk.
- Evidence — errors, logs, screenshots, links.
- Impact — who is affected and how often.
- Who is the user/system experiencing the problem? What decision or workflow is blocked?

Apply first-principles checks:
- Do we understand what problem we solve?
- Is it really a problem (not just preference)?
- What happens if we don't do it?

#### 2. Feature attachment

- Ask whether the task belongs to an existing feature. If yes, confirm the slug by listing candidates: `./scripts/taskman.sh list features`.
- If no feature, the task is ad-hoc.

#### 3. Specify scope precisely

- In-scope — what must change, where, and what must be true afterward.
- Out-of-scope / non-goals — adjacent work that will NOT be included.
- Dependencies — environments, hosts, repos, external services.
- Constraints — security, availability, performance, deadlines.
- Assumptions — what we assume true, and how we will validate.

Iterate until the task is executable without guesswork.

#### 4. Define success

- Acceptance criteria — specific, observable outcomes.
- Success metrics (optional) — what metric moves, in what direction.

Do not choose implementation approach here.

#### 5. Define validation and rollback

- Quality gates — the exact commands and/or manual steps that must pass.
- Rollback plan — how to revert safely (when relevant).

#### 6. Delegate body composition to the Planner

Hand the dialog outputs (problem, scope, acceptance criteria, quality gates, optional constraints/assumptions, optional risks/rollback) to the Planner per the **Composition via the Planner sub-agent** section above, with `level=task`, `mode=new`. The Planner returns a `## Body` matching the required sections (`## Problem statement`, `## Scope`, `## Acceptance criteria`, `## Quality gates`) and an empty `## Decomposition`.

`taskman.sh` validates the section names on creation; the Planner enforces them.

#### 7. Write the draft and preview

Write the task file directly. `taskman.sh new` stamps the YAML with `draft: true` so the file is clearly mid-creation until the user approves.

```bash
cat <<'EOF' | ./scripts/taskman.sh new task --title "<title>" [--feature <slug>]
# body with the required sections
EOF
```

Capture the printed path. Re-read the file from disk, show it to the user, confirm title / feature attachment / body. Offer edits — apply them by editing the file in place.

#### 8. Finalize or discard

On approval, clear the draft marker:

```bash
./scripts/taskman.sh finalize <path>
```

On rejection, delete the draft:

```bash
./scripts/taskman.sh discard <path>
```

Report the final path to the user. Stop. User will run `/tm:implement task <path>` when ready.

---

### Bug ticket — collaboration process

#### 1. Observed problem

What is broken, where, and what is the user/system impact?

#### 2. Expected vs actual

Clear contrast between correct behaviour and current.

#### 3. Reproduction steps

Minimal, numbered, reliable.

#### 4. Context

Environment, version, logs, screenshots, links.

#### 5. Feature attachment

Bugs can be attached to a feature (post-merge follow-up) or left ad-hoc. Ask.

#### 6. Delegate body composition to the Planner

Hand the dialog outputs (observed problem, expected vs actual, reproduction steps, context, feature attachment if any) to the Planner per the **Composition via the Planner sub-agent** section above, with `level=task`, `mode=new`. Note `kind=bug` in the `initial_scope` so the Planner shapes bug-appropriate content:

- `## Problem statement` — observed vs expected.
- `## Scope` — what is and is not in the fix.
- `## Acceptance criteria` — reproduction no longer reproduces; regression test added.
- `## Quality gates` — exact validation commands.

Run `taskman.sh new task --kind bug` to persist (the `--kind` flag belongs on the taskman call, not in the Planner brief).

#### 7. Write the draft, preview, finalize

```bash
cat <<'EOF' | ./scripts/taskman.sh new task --kind bug --title "<title>" [--feature <slug>]
# body
EOF
```

Re-read the file from disk and show the user. On approval run `./scripts/taskman.sh finalize <path>`; on rejection run `./scripts/taskman.sh discard <path>`. Report the final path, stop.

---

## Discipline (all levels)

- Use `taskman.sh` for any task/feature/epic persistence. Never write those files directly.
- Always end the dialog with either `taskman.sh finalize` or `taskman.sh discard` for every draft. Do not leave files with `draft: true` behind.
- This skill captures requirements and decomposition only; it does not propose implementation. If the user asks "how should we build it?", capture as a follow-up — execution belongs in `/tm:implement`.
- For maintenance work at the task level (`refactor`, `upgrade`, `chore`), use the task branch; scope tightly.
- Stop as soon as the work is written and the path(s) reported. No execution follows from this skill.
- If the task implements something defined in a spec under `docs/<area>/.../-spec.md`, reference the spec rather than restating its requirements. Spec sections drift if duplicated.
