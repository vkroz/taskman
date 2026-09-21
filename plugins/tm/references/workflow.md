# Shared Taskman workflow

Taskman is the source of truth for work shared by Codex, Claude Code and other agents. Use `taskman` (alias `tm`) for all task mutations. Read `taskman --help` and command help when syntax is uncertain. Do not call the legacy shell script or edit task files directly.

## Context and scope

Run inside the target project, or set `TASKMAN_TASKS_DIR` to its shared tree. Read `taskman context ID` before work: it includes requirements, ancestors, descendants, dependencies and journals. Resolve a missing ID with `taskman list --recursive --json`; ask only if the target remains ambiguous. IDs are stable; generated paths may change.

Use `epic`, `feat` (story/feature), and `task`; a task is a leaf. Product-level requirements and architecture live in the project's existing documents. Follow that project's document conventions and link documents from tasks rather than duplicating requirements.

The UI creates tasks and displays agent-written context. All lifecycle operations are available through the CLI. Taskman does not launch agents itself.

## Create and plan

```sh
taskman new --type epic --title "Outcome" --body-file requirements.md --json
taskman new --type feat --title "Story" --parent PARENT_ID --body-file story.md --json
taskman new --title "Work" --parent PARENT_ID --body-file task.md --json
taskman depend TASK_ID --on DEPENDENCY_ID
taskman finalize TASK_ID
```

Replace placeholders with returned IDs. `depend` replaces the dependency list; repeat `--on` to retain multiple dependencies. `--clear` removes it. Use parent relationships for decomposition and dependencies for execution order. Independent tasks may run in parallel if the host supports it and shared files remain coordinated.

Items begin as drafts. Finalize when the scope is agreed, including through existing user authorization. Leave unresolved drafts open; `discard` removes only an unreferenced draft leaf. Finalize ancestors before executing children.

## Execute and journal

```sh
taskman ready --json
taskman move TASK_ID active
taskman update TASK_ID --body-file refined-requirements.md
taskman record TASK_ID --kind decision --actor ACTOR --body-file decision.md
```

Open requirements may be refined to reach the agreed outcome. Record significant choices, reasons, scope refinements and implementation progress. Use `handoff` for the next agent's entry point and remaining work. These are concise engineering rationales, not a transcript of internal deliberation. Do not relax acceptance criteria merely to make a failing result pass. Ask about material changes to the user's intended outcome; routine implementation choices remain within authorized work.

## Verify and complete

Run the specified local quality gates and compare the actual result against acceptance criteria. Parent completion also needs checks of integrated behavior; passing child tests alone does not establish it.

```sh
taskman record TASK_ID --kind summary --actor ACTOR --body-file summary.md
taskman record TASK_ID --kind verification --result pass --actor ACTOR --body-file evidence.md
taskman close TASK_ID
```

`--body-file -` reads stdin. Summarize what changed, why, and any limits without copying the full journal. Keep original decision records available. Evidence should identify commands, exit codes, observed results, relevant source revision or working-tree state, and acceptance coverage. Distinguish tests you ran, source inspection, and another agent's report.

Use `--result fail` or `blocked` when appropriate and leave the item open. A passing record is an agent assertion, not a test runner. `taskman validate` checks storage integrity, not product correctness.

Close requires a current summary, a current passing verification, completed dependencies (including inherited ones), and completed descendants. Body, type, dependency, implementation-journal and child changes invalidate earlier completion evidence. Existing authorization to implement and verify includes closing work that meets the criteria; respect any explicit human sign-off requirement.

Closed tasks and their journals are immutable. New changes require a new task referencing the original ID. Do not reopen or rewrite completed work. Deployment, commits and pushes require the user's applicable authorization; local completion does not imply them.
