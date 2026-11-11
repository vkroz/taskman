# Taskman specification

Taskman owns the shared work records; coding agents own execution. A workflow succeeds when the requested local behavior meets its acceptance criteria and the evidence is retained for the next agent or engineer.

## Work records

Items have a stable ID, title, type, requirements, status and optional parent and dependencies. Types are epic, feature/story (`feat`) and task. A task is a leaf; other nesting is flexible. Requirements may link existing project documents rather than duplicate them.

Items persist in a SQL database: SQLite (the default, one file shared by all repositories) or Postgres, which connects by URL or through a Cloud SQL unix socket. Items belong to projects and have keys such as `TM-42`; a key identifies the item and its project. Each item also has an assignee, labels and an activity entry for each field change. Taskman does not read `tasks/` Markdown files from earlier versions. [The data model](data-model.md) defines the entities and how each lifecycle rule applies to them.

New items start as drafts. Agreed items can be finalized and activated when their dependencies and inherited dependencies are complete. Planning records decomposition and ordering; independent work may execute concurrently in the coding host.

Open tasks can be refined to reach the agreed outcome. Significant decisions, reasons, scope changes, progress and handoffs go in attributed comments. Completion includes a concise summary and evidence of local acceptance checks. After closure, the task and its evidence are immutable; corrections are new tasks referencing the original.

The CLI rejects closure without a current summary and passing verification, or with unfinished dependencies or children. Changes to the description, type, dependencies or children, and new decision or progress comments, invalidate prior completion evidence. Taskman checks record consistency and revision, but does not execute tests or judge the truth of an agent's assertion. Source revision and dirty working-tree details belong in verification evidence; code changes outside Taskman are not automatically detected.

## Agents and interface

Codex and Claude Code share the same work items and can take different roles within one lifecycle. A fresh agent reads the target, ancestors, descendants, dependencies, comments and activity through `taskman context`. Agent handoffs must not depend on private conversation history.

The CLI supports every operation available through the UI. The UI creates and edits open work, discards unreferenced draft leaves, adds comments, sets assignees and labels, and displays hierarchy, dependencies, status, comments and activity, refreshing as agents work. Metadata filters and sibling sorting preserve hierarchy context. Execution is controlled through coding tools. Deployment is outside local completion.

One CLI installs the shared plugin through platform adapters. Codex and Claude Code are the initial supported hosts. Adding another host should change installation integration, not task storage or lifecycle semantics.

## Acceptance baseline

Verify UI and CLI creation, nested decomposition, dependency ordering, comments, local validation and immutable closure. Verify that another agent can recover the context and record an independent review. Parent acceptance must assess integrated behavior as well as completed children.

The bounded Diviner pilot uses three read-only tasks. It tests Taskman and does not authorize development of Diviner or connection to its live services. Detailed findings and limitations are in [the evaluation report](evaluation.md).
