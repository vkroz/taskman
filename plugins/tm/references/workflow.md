# Shared Taskman workflow

Taskman is the source of truth for work shared by Codex, Claude Code and other agents. Use `taskman` (alias `tm`) for all task mutations. Read `taskman --help` and command help when syntax is uncertain. Do not write Taskman's database directly, and do not treat Markdown task files from earlier Taskman versions as current state.

## Context and scope

Before work, run `taskman auth status`. When the project instructions or the user name a Taskman server, continue only when it prints `Logged in to` that server. Otherwise stop and report the output. Never create items in a local database for shared work.

When no server is named, the local database is correct. Taskman stores work items in SQLite (the default, one file for all repositories) or Postgres, as the user's settings select. If a command reports that there is no database or that the schema is old, run `taskman db upgrade`.

Items have keys such as `TM-42`: the project key, then a number. A key identifies the item and its project. Read `taskman context KEY` before work: it includes requirements, ancestors, descendants, dependencies, comments and activity. Resolve a missing key with `taskman list --json` (add `--project KEY` to narrow it); ask only if the target remains ambiguous.

Use `epic`, `feat` (story/feature), `task`, and `bugfix` (a task that fixes a bug); a task or bugfix is a leaf. Product-level requirements and architecture live in the project's existing documents. Follow that project's document conventions and link documents from tasks rather than duplicating requirements.

The UI creates tasks, edits the title and description of open items in place, moves and closes them from the status field, adds comments, sets assignees and labels, and displays agent-written context. All lifecycle operations are available through the CLI. Taskman does not launch agents itself.

## Create and plan

```sh
taskman new --type epic --title "Outcome" --project PROJECT --body-file requirements.md --json
taskman new --type feat --title "Story" --parent PARENT_KEY --body-file story.md --json
taskman new --title "Work" --parent PARENT_KEY --body-file task.md --json
taskman depend TASK_KEY --on DEPENDENCY_KEY
taskman finalize TASK_KEY
```

A top-level item needs `--project` with the project key (2-10 uppercase letters or digits); Taskman creates the project on first use. A child gets the project of its parent, and dependencies stay inside one project. Replace placeholders with returned keys. `depend` replaces the dependency list; repeat `--on` to retain multiple dependencies. `--clear` removes it. Use parent relationships for decomposition and dependencies for execution order. Independent tasks may run in parallel if the host supports it and shared files remain coordinated.

Items begin as drafts. Finalize a draft only when the user agrees to its scope in this conversation. A request to work on "the first task" does not approve a draft. Leave unresolved drafts open; `discard` removes only an unreferenced draft leaf. Finalize ancestors before executing children.

## Execute and comment

```sh
taskman ready --json
taskman move TASK_KEY active --actor ACTOR
taskman assign TASK_KEY ACTOR
taskman update TASK_KEY --body-file refined-requirements.md
taskman comment TASK_KEY --kind decision --actor ACTOR --body-file decision.md
taskman comment TASK_KEY --kind progress --actor ACTOR --body "Parser done; tests next."
```

Every command that changes an item accepts `--actor` to name the agent. Without it, the author is the person of the token on a server, or the git `user.name` with a local database. Each field change is recorded in the item's activity (`taskman activity TASK_KEY`). Use `assign` and `label` to show who works on an item and how it is grouped.

Open requirements may be refined to reach the agreed outcome. Comment on significant choices, reasons, scope refinements and implementation progress. Use a `handoff` comment for the next agent's entry point and remaining work. These are concise engineering rationales, not a transcript of internal deliberation. Do not relax acceptance criteria merely to make a failing result pass. Ask about material changes to the user's intended outcome; routine implementation choices remain within authorized work.

## Verify and complete

Run the specified local quality gates and compare the actual result against acceptance criteria. Parent completion also needs checks of integrated behavior; passing child tests alone does not establish it.

```sh
taskman comment TASK_KEY --kind summary --actor ACTOR --body-file summary.md
taskman comment TASK_KEY --kind verification --result pass --actor ACTOR --body-file evidence.md
taskman close TASK_KEY
```

`comment` takes its text from exactly one of `--body TEXT` (a short note) and `--body-file FILE` (`-` reads stdin). Summarize what changed, why, and any limits without copying every comment. Comments cannot change or be deleted, so earlier decisions stay available. Evidence should identify commands, exit codes, observed results, relevant source revision or working-tree state, and acceptance coverage. Distinguish tests you ran, source inspection, and another agent's report.

Use `--result fail` or `blocked` when appropriate and leave the item open. A passing verification is an agent assertion, not a test runner. `taskman validate` checks the dependency graph, not product correctness.

Close requires a current summary, a current passing verification, completed dependencies (including inherited ones), and completed descendants. Description, type, dependency and child changes, and new decision or progress comments, invalidate earlier completion evidence. Existing authorization to implement and verify includes closing work that meets the criteria; respect any explicit human sign-off requirement.

Closed tasks and their comments are immutable. New changes require a new task referencing the original key. Do not reopen or rewrite completed work. Deployment, commits and pushes require the user's applicable authorization; local completion does not imply them.
