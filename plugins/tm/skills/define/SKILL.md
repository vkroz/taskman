---
name: define
description: Create requirements and scoped work items in Taskman, from product direction to an epic, feature or task.
---

Arguments: `[product|epic|feature|task]`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, journals and closure rules.

Create items with `taskman new`. If an id already exists, run `taskman pull ID --agent <author>` and read `.taskman/tasks/ID.md` before editing the repository. Do not write live task files under `tasks/`. Copy any older file text into `taskman new` by hand.

Establish the intended outcome, scope, observable acceptance criteria and meaningful quality gates from the user's request and repository context. Ask only about unresolved decisions that affect the outcome. For a bug, include reproduction, expected and actual behavior; create it as type `task`.

For product work, update the project's existing vision, requirements or specification documents. For an epic or feature, create the parent and the minimum useful decomposition. For a single task, create only that task. Use `--parent` for hierarchy; do not invent separate slug-based links or mandatory tiers.

Read [composition guidance](../../rules/task-composition.md) when drafting work items. Persist with `new --body-file ... --json`, then set actual dependencies. Finalize items whose scope is agreed; leave unresolved questions visible in drafts. Return the created IDs and the outcome they describe. Definition does not authorize implementation.
