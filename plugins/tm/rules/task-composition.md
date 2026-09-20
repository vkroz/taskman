# Task Composition: Frontmatter, Body, Summary

Composition rules for epic, feature, and task files — what fields they carry and what sections their bodies hold. Persistence (where they live, naming, when they move, lifecycle preconditions, approval, validation behavior) is documented at `./scripts/taskman.sh help`.

## Concepts

| Term | Meaning |
|------|---------|
| **Epic** | A named functional block larger than a feature. Decomposes into one or more features. Identified by a stable slug. |
| **Feature** | A named unit of product work that produces a plan and one or more tasks. Identified by a stable slug. Optionally belongs to an epic via `epic: <slug>`. |
| **Task** | A unit of implementation work. Either belongs to a feature (via `feature: <slug>`) or is ad-hoc (no feature field). |
| **Bug** | A task with `kind: bug`. Same lifecycle. Can be attached to a feature or ad-hoc. |

Default `kind` is `task`. A task without a `feature` field is ad-hoc. A feature without an `epic` field is stand-alone.

The hierarchy is **epic → feature → task**. Each tier is optional one level up: a task may have no feature, a feature may have no epic. The hierarchy is open, not enforced.

## YAML frontmatter

Every epic, feature, and task file starts with a YAML block.

**Epic:**
```yaml
---
status: backlog | active | done
slug: <slug>
title: <human-readable title>
---
```

**Feature:**
```yaml
---
status: backlog | active | done
slug: <slug>
epic: <slug>           # omit for stand-alone features
title: <human-readable title>
---
```

**Task:**
```yaml
---
status: backlog | active | done
kind: task | bug
feature: <slug>        # omit for ad-hoc tasks
title: <human-readable title>
---
```

## Body structure

### Epic body — required sections

- **Problem statement** — what problem this epic solves and why
- **Objective** — desired end state at the functional-block level
- **Scope** — what is in and out of scope; how this epic differs from adjacent epics
- **Acceptance criteria** — observable conditions at functional-block level that prove the epic is complete
- **Linked features** — ordered list of feature slugs that compose this epic, in execution order

An epic is a functional-block-sized project plan. It says **why** the work matters at a level larger than a single feature, **what** done looks like for the whole block, and **which features** deliver it. It does not duplicate feature or spec content. Each linked feature owns its own scope, acceptance criteria, and tasks.

### Feature body — required sections

- **Problem statement** — what problem this feature solves and why
- **Objective** — desired end state
- **Acceptance criteria** — testable conditions that prove the feature is complete; include any post-launch success measures here as observable conditions

Recommended:

- **Scope** — what is in and out of scope
- **Tasks** — ordered list of task titles or slugs that compose this feature, in execution order
- **Linked spec** — pointer to the implementation contract under `docs/<area>/.../-spec.md`

A feature is a tight project plan. It says **why** the work matters, **what** done looks like, and **which tasks** deliver it. It does not duplicate spec content.

**Do not** put **Requirements** in the feature file. Detailed requirements live in the linked spec — putting them in two places guarantees drift.

**Do not** put **Risks and mitigations** in the feature file unless each risk has a named owner and a mitigation plan you intend to track. Risks listed without action are dead weight.

**Do not** put **Success metrics** as a separate section. If a measure is testable, fold it into Acceptance criteria. If it is not testable, it does not belong here.

### Task body — required sections

- **Problem statement** — what problem this task solves
- **Scope** — what is in scope and what is explicitly out of scope
- **Acceptance criteria** — testable conditions that prove completion
- **Quality gates** — validation steps, required reviews, or checks that must pass before the task moves to `done`

Recommended: **Constraints and assumptions**.

## Completion summary

When a task, feature, or epic moves to `done`, append a `## Summary` section at the end of the file. This is mandatory for audit and traceability — a future agent troubleshooting a later issue should be able to read the file and understand what actually landed, without replaying git history.

The summary covers:

- **Steps completed** — what was actually done, in order.
- **Changes made** — files created, changed, or deleted; key artifacts produced.
- **Notable decisions or deviations** — anything that departs from the original plan and why.
- **Links** — PRs, commits, follow-up tasks, related bugs.

Example of why this matters: an agent investigating a reported bug jumped to changing the codebase, but the user recalled the bug had already been fixed weeks earlier. The agent then found the fix described in a `done/` task's summary and corrected its plan. That outcome depends on every completed task carrying an honest, specific summary.

Keep it factual and concrete. "Implemented per plan" is not a summary.
