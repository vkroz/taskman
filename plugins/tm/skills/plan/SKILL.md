---
name: plan
description: Decompose an existing Taskman epic or feature into executable work with dependencies.
---

Arguments: `[epic|feature] KEY`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, comments and closure rules.

Read the target and existing children before creating anything. Decompose the agreed scope into independently verifiable outcomes; preserve completed children and reuse existing open work. Add only the tasks needed for the requested scope.

Each child needs acceptance criteria and an appropriate local check. Use parent links for decomposition, dependency keys for ordering, and identify work that can safely run concurrently. Keep integration and parent acceptance explicit. Do not change the product's requirements to fit the plan or implement code during planning.

Persist the plan and add decision comments for material decomposition decisions. Finalize agreed items and report blockers or unresolved scope decisions.
