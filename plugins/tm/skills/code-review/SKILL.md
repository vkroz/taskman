---
name: code-review
description: Review a requested code scope for concrete defects and maintainability problems; report evidence without modifying implementation.
---

Arguments: `[scope]`.

Read [the shared workflow](../../references/workflow.md) for CLI syntax, context, comments and closure rules.

Read the requested code and its requirements. Trace likely defects to an observable failure or specific maintenance consequence; avoid preference-only findings. Inspect relevant tests and run bounded checks when useful.

Report actionable findings with file references, trigger, consequence and suggested correction. Distinguish verified failures from hypotheses. Do not modify implementation. Create backlog tasks only when the user's review scope includes filing findings; check for duplicates first and use the Python CLI. Keep each task tied to the evidence and acceptance criteria for its fix.
