---
name: implement
description: Execute authorized Taskman work through implementation, local verification and recorded completion.
---

Arguments: `[task|feature|epic] ID`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, journals and closure rules.

Before editing the repository, run `taskman pull ID --agent <author>` and read `.taskman/tasks/ID.md`. The server is the source of truth. Do not write live task files under `tasks/`. When the work is finished, run `taskman complete ID --message "..." --agent <author>` and include the line `Taskman completion: ID` in the final message. Commits and pull requests are reported by the Claude Code hook or, after `taskman hooks install`, by the git post-commit hook.

Read the target context and relevant repository rules. For a parent, inspect the existing decomposition and execute ready children in dependency order. Create missing work only within the authorized scope; avoid duplicate tasks.

Activate ready work, implement the agreed outcome, and journal significant decisions and refinements. If the implementation reveals a necessary detail, update the open task and record why. If it changes the intended outcome or contradicts a parent requirement, surface that specific decision rather than silently widening scope.

Run the target's local checks, record truthful evidence and a concise completion summary, then close completed items. Fix in-scope failures and revalidate; keep unresolved work open. Verify parent-level integration after children complete. At handoff, record the next action and remaining uncertainty so another agent can continue from Taskman.
