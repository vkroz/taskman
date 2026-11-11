---
name: implement
description: Execute authorized Taskman work through implementation, local verification and recorded completion.
---

Arguments: `[task|feature|epic] KEY`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, comments and closure rules.

Read the target context and relevant repository rules. For a parent, inspect the existing decomposition and execute ready children in dependency order. Create missing work only within the authorized scope; avoid duplicate tasks.

Activate ready work, implement the agreed outcome, and add decision comments for significant decisions and refinements. If the implementation reveals a necessary detail, update the open task and record why. If it changes the intended outcome or contradicts a parent requirement, surface that specific decision rather than silently widening scope.

Run the target's local checks, record truthful evidence and a concise completion summary, then close completed items. Fix in-scope failures and revalidate; keep unresolved work open. Verify parent-level integration after children complete. At handoff, record the next action and remaining uncertainty so another agent can continue from Taskman.
