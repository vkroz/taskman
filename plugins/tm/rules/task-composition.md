# Work-item composition

An item describes an outcome, its scope, observable acceptance criteria and how to verify them locally. Use the sections needed to make those facts clear; avoid duplicating a linked specification.

Types are `epic`, `feat` (story/feature), `task`, and `bugfix`. A task or bugfix is a leaf; other levels may be nested as needed. Keys (such as `TM-42`) and parent/dependency relationships are assigned through the CLI; a top-level item names its project. For bugs, use type `bugfix` (`taskman new --type bugfix`) and describe reproduction, expected behavior and actual behavior. There is no `--kind` creation flag.

For planning, make children independently verifiable and identify ordering dependencies. A parent's acceptance criteria cover the integrated outcome, not just whether children are marked done.

## Definition of ready

`finalize` succeeds only when the item has a title, a description beyond its title heading, a repository on the item or an ancestor (`taskman repo KEY ADDRESS`), and a latest `review` comment with result `pass` at the current revision. Any later change that makes the revision advance makes the review stale; review again before you finalize.

Record the review with `taskman comment KEY --kind review --result pass|fail --body-file FILE`. Give the result of each check:

- Outcome: what is true when the item is done.
- Scope: what is in and what is out.
- Acceptance criteria: each one is observable.
- Verification: the exact local check for each criterion.
- Bugfix: steps to reproduce, expected behavior and actual behavior.
- Dependencies: each one is named, with its order.
- Size: a task or bugfix fits one change; a larger item needs children.
- Code context: you read the affected code, and the item names the affected area.
- Clarity: no open question and no undefined term.

Record a fail with the failed checks when the item is not ready. Rewrite the description first, then record the passing review, so that the review covers the final text. Only a person finalizes, or an agent after the person's explicit yes.

Keep significant decisions, their reasons and scope refinements in decision comments. Before closure, summarize changes, rationale, relevant artifacts, remaining limits and follow-up links. Record verification separately with exact checks, outcomes and source state. Closed records remain immutable.

See [the shared workflow](../references/workflow.md) for persistence and lifecycle commands. Never write Taskman's database directly.
