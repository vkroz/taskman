---
name: plan
description: Decompose an existing Taskman epic or feature into executable work with dependencies.
---

Arguments: `[epic|feature] ID`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, journals and closure rules.

Run `taskman pull ID --agent <author>` and read `.taskman/tasks/ID.md` before planning against the repository. Persist new children with `taskman new` and `taskman depend`. Do not write live task files under `tasks/`.

Read the target and existing children before creating anything. Decompose the agreed scope into independently verifiable outcomes; preserve completed children and reuse existing open work. Add only the tasks needed for the requested scope.

Each child needs acceptance criteria and an appropriate local check. Use parent links for decomposition, dependency IDs for ordering, and identify work that can safely run concurrently. Keep integration and parent acceptance explicit. Do not change the product's requirements to fit the plan or implement code during planning.

Persist the plan and journal material decomposition decisions. Finalize agreed items and report blockers or unresolved scope decisions.
