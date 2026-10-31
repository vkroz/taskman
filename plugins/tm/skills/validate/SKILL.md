---
name: validate
description: Check work against acceptance criteria and record actual evidence in Taskman. Use for task, feature, epic or product validation.
---

Arguments: `[task|feature|epic|product] [ID]`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, journals and closure rules.

Run `taskman pull ID` and read `.taskman/tasks/ID.md` before checking the repository. When completion is authorized, run `taskman complete ID --message "..."` and include `Taskman completion: ID` in the final message so the Stop hook records it. Do not write live task files under `tasks/`.

Read the target's context and linked specifications. Run its local quality gates and check the observable outcome, including integration across children for a parent. Review existing evidence before deciding which checks need rerunning; name any checks you did not execute.

Record a verification with pass, fail or blocked and evidence sufficient for another agent to assess it. For authorized completion, record a concise summary and close only after every criterion and dependency is satisfied. A failed check leaves the task open; do not weaken criteria or make unrelated fixes.

Use an independent reviewer when requested or warranted and available; supply the Taskman ID and source location so it can recover context. Do not assume a particular host's subagent API. Product validation uses the existing product requirements and produces a local acceptance report; it does not deploy or declare a release approved.
