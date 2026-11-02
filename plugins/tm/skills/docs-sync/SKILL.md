---
name: docs-sync
description: Review project documentation for drift against completed Taskman work and update affected documents.
---

Arguments: `[ID]`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, journals and closure rules.

Run `taskman pull ID --agent <author>` and read `.taskman/tasks/ID.md` for the completed item. Do not edit historical files under `tasks/` as if they were the live store.

Read the specified item's context, completion summary and changed artifacts. If an existing `tasks/docs-sync-queue.txt` is present, it can identify candidates; the Python CLI does not automatically populate that legacy queue.

Check the project's requirements, architecture, setup and usage documents only where completed work affects them. Correct established drift within the user's authorization, preserving document structure. Surface unresolved product decisions instead of inventing requirements. Never edit the closed task or its evidence. For follow-up work, create a linked task when requested or within an authorized maintenance scope.

Report documents changed and remaining drift. Clear only queue entries actually processed, if a queue was used.
