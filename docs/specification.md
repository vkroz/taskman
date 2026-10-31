# Taskman specification

The running product is the Postgres-backed server described in [architecture](architecture.md) and the README. The sections below that describe a file tree, drafts, or an immutable on-disk journal are the previous edition. They are not what `taskman serve` enforces.

Taskman owns the shared work records; coding agents own execution. A workflow succeeds when the requested local behavior meets its acceptance criteria and the evidence is retained for the next agent or engineer.

## Work records

Items have a stable ID, title, type, requirements, status and optional parent and dependencies. Types are epic, feature/story (`feat`) and task. A task is a leaf; other nesting is flexible. Requirements may link existing project documents rather than duplicate them.

New items start as drafts. Agreed items can be finalized and activated when their dependencies and inherited dependencies are complete. Planning records decomposition and ordering; independent work may execute concurrently in the coding host.

Open tasks can be refined to reach the agreed outcome. Significant decisions, reasons, scope changes, progress and handoffs go in an attributed journal. Completion includes a concise summary and evidence of local acceptance checks. After closure, the task and its evidence are immutable; corrections are new tasks referencing the original.

The CLI rejects closure without a current summary and passing verification, or with unfinished dependencies or children. Changes to the body, type, dependencies, implementation journal or children invalidate prior completion evidence. Taskman checks record consistency and revision, but does not execute tests or judge the truth of an agent's assertion. Source revision and dirty working-tree details belong in verification evidence; code changes outside Taskman are not automatically detected.

## Agents and interface

Codex and Claude Code share the same task tree and can take different roles within one lifecycle. A fresh agent reads the target, ancestors, descendants, dependencies and journals through `taskman context`. Agent handoffs must not depend on private conversation history.

The CLI supports every operation available through the UI. The UI creates and edits open work, discards unreferenced draft leaves, and displays hierarchy, dependencies, status and journals, refreshing as agents work. Metadata filters and sibling sorting preserve hierarchy context. Completed-work consolidation creates documentation excerpts and hides individual rows from ordinary collections while retaining immutable originals and ID lookup. Execution is controlled through coding tools. Deployment is outside local completion.

One CLI installs the shared plugin through platform adapters. Codex and Claude Code are the initial supported hosts. Adding another host should change installation integration, not task storage or lifecycle semantics.

## Acceptance baseline

Verify UI and CLI creation, nested decomposition, dependency ordering, journal updates, local validation and immutable closure. Verify that another agent can recover the context and record an independent review. Parent acceptance must assess integrated behavior as well as completed children.

The bounded Diviner pilot uses three read-only tasks. It tests Taskman and does not authorize development of Diviner or connection to its live services. Detailed findings and limitations are in [the evaluation report](evaluation.md).
