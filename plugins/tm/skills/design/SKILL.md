---
name: design
description: Refine the design of an existing product, epic or feature while preserving its agreed outcome.
---

Arguments: `[product|epic|feature] [ID]`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, journals and closure rules.

Run `taskman pull ID` and read `.taskman/tasks/ID.md` before changing the repository. Record the design note with `taskman update` and `taskman record`. Do not write live task files under `tasks/`.

Read the current requirements, architecture, implementation and target context. Identify the concrete design decision, alternatives that matter, and evidence supporting the choice. Preserve existing constraints and unrelated decisions.

Update the project's design document or the open item's body through `taskman update`. Journal the reason for the change. Keep decomposition unchanged unless it must change to make the design coherent; report affected tasks. Closed items require new linked work. Do not implement as part of a design-only request.
