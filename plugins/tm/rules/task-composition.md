# Work-item composition

An item describes an outcome, its scope, observable acceptance criteria and how to verify them locally. Use the sections needed to make those facts clear; avoid duplicating a linked specification.

Types are `epic`, `feat` (story/feature), and `task`. A task is a leaf; other levels may be nested as needed. IDs and parent/dependency relationships are assigned through the CLI. For bugs, use a task describing reproduction, expected behavior and actual behavior. There is no separate `bug` type or `--kind` creation flag.

For planning, make children independently verifiable and identify ordering dependencies. A parent's acceptance criteria cover the integrated outcome, not just whether children are marked done.

Keep significant decisions, their reasons and scope refinements in the journal. Before closure, summarize changes, rationale, relevant artifacts, remaining limits and follow-up links. Record verification separately with exact checks, outcomes and source state. Closed records remain immutable.

See [the shared workflow](../references/workflow.md) for persistence and lifecycle commands. Never compose managed frontmatter by hand.
