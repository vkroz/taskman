---
name: design
description: Focused revision of an existing unit's design at any tier — product, epic, or feature. Invoke with /tm:design <level>. Product drafts docs/architecture.md in this session; epic and feature delegate to the Planner sub-agent for in-place refinement.
argument-hint: product | epic | feature
---

# Design (`/tm:design <level>`)

## Arguments

| Position | Variable | Value |
|----------|----------|-------|
| `$0` | level | `product`, `epic`, or `feature` |

## Argument validation

If `$0` is missing, stop and ask:
> *"What level — product, epic, or feature?"*

If `$0` is not one of `product`, `epic`, `feature`, stop and list the valid set.

## Dispatch

Read `$0`. Run exactly one of the branches below.

---

## $0 = product

### Preconditions
**Required before starting:**
- `docs/vision.md`
- `docs/spec.md`
- `docs/requirements.md`

If any are missing, stop and tell the user: *Cannot run `/tm:design product` — definition documents not found. Run `/tm:define product` first.*

### Output
| Artifact | Path |
|----------|------|
| High-level architecture | `docs/architecture.md` |

**In scope:** technology choices, system architecture, domain dictionary (key terms), workflows and **key** APIs, security mechanisms.

**Out of scope:** detailed API signatures, database schemas, function-level design — those belong in task-level detailed design during implementation.

### Workflow

1. Draft `docs/architecture.md` from the definition documents.

2. **Review cycle** with the user: incorporate feedback.

3. **Self-validation** — produce a report covering: completeness, consistency with specs, over/under-engineering, design-principle issues.

4. **Document consistency** — If architectural decisions change scope (e.g. defer a feature), update `docs/spec.md` and/or `docs/requirements.md` and state why. Chain: vision → spec → requirements → architecture.

5. **Gate** — User explicitly approves before `/tm:define epic` or `/tm:define feature`.

### Product-branch discipline
- Do not invent requirements; align with definition docs or flag conflicts to the user.
- Architecture impact that contradicts prior commitments requires explicit user agreement.

---

## $0 = epic

### Preconditions

The epic file must exist under `tasks/epics/`. If it does not, stop and direct the user to `/tm:define epic`.

### Workflow

1. **Locate** — Confirm the epic slug with the user. Read `tasks/epics/YYYYMMDD_<slug>.md` and note the current `## Linked features`.

2. **Identify the design gap** — Ask the user what needs revision. Typical reasons: scope drifted, a new constraint surfaced, a downstream feature exposed an ambiguity in the epic's acceptance criteria.

3. **Delegate to the Planner.** Invoke via the Agent tool with `subagent_type: planner` and the following brief:
   - `level=epic`
   - `mode=refine`
   - `title=<existing epic file path>`
   - `initial_scope=<user description of what needs to change>`
   - `upstream=<paths to relevant docs: vision, spec, requirements, architecture>`

   The Planner returns the changed sections of the `## Body` plus a one-sentence rationale. It does not normally touch the feature list — that's the `/tm:plan epic` flow.

4. **User review** — Show the diff (current sections vs Planner's proposed sections). Iterate until approved.

5. **Persist** — Apply the edits to the epic file in place via the Edit tool. The file's `status` and `slug` do not change; only body sections are touched. Do not create a new unit.

6. **Report** — One-line summary of what changed.

### Discipline

- Refinement is in-place. Do not move the epic between status folders.
- If the user asks for a fundamental re-decomposition (adding/removing features), redirect to `/tm:plan epic`.
- If the user describes a contradicting requirement, surface the conflict; do not silently resolve.

Stop.

---

## $0 = feature

### Preconditions

The feature file must exist under `tasks/features/`. If it does not, stop and direct the user to `/tm:define feature`.

### Workflow

1. **Locate** — Confirm the feature slug with the user. Read `tasks/features/YYYYMMDD_<slug>.md` and the linked spec under `docs/<area>/.../-spec.md` if any.

2. **Identify the design gap** — Ask the user what needs revision. Typical reasons: scope drift, an acceptance criterion is ambiguous, a task surfaced a missing constraint, the linked spec is out of date.

3. **Delegate to the Planner.** Invoke via the Agent tool with `subagent_type: planner` and the following brief:
   - `level=feature`
   - `mode=refine`
   - `title=<existing feature file path>`
   - `initial_scope=<user description of what needs to change>`
   - `upstream=<paths to: parent epic file if any, docs/vision.md, docs/spec.md, docs/architecture.md, linked spec>`

   The Planner returns the changed sections of the `## Body` (and optionally the linked spec content) plus a one-sentence rationale. It does not normally touch the task list — that's the `/tm:plan feature` flow.

4. **User review** — Show the diff. Iterate until approved.

5. **Persist** — Apply the edits to the feature file (and linked spec, if proposed) in place via the Edit tool. The file's `status` and `slug` do not change.

6. **Report** — One-line summary of what changed.

### Discipline

- Refinement is in-place. Do not move the feature between status folders.
- If the user asks for a fundamental re-decomposition (adding/removing tasks), redirect to `/tm:plan feature`.
- If the user describes a contradicting requirement, surface the conflict.

Stop.
