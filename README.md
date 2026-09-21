# Taskman

Taskman keeps requirements, plans, dependencies, decisions and verification in a shared task tree. Codex and Claude Code use the same CLI and records, so one agent can implement a task and another can review it without a conversation handoff.

The browser UI creates tasks and displays their hierarchy, status and evidence. Agents plan and execute through the CLI. Completion means the agreed acceptance criteria work locally; deployment is outside this workflow.

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and the coding tools you want to use.

```sh
uv tool install 'git+https://github.com/vkroz/taskman.git#subdirectory=taskman'
taskman install --platform codex --platform claude-code
```

Choose either platform or repeat `--platform` for both. `taskman` and `tm` are equivalent commands. Start a fresh coding session after installing the plugin.

For a local checkout under development:

```sh
uv tool install --force --editable ./taskman
taskman install --platform codex --platform claude-code --source "$PWD"
```

The CLI installs one shared plugin through host adapters. `taskman install --list-platforms` lists supported hosts. If an existing marketplace points to another source, the installer reports the conflict rather than replacing it.

## Work on a project

Run Taskman inside the project. It finds the nearest ancestor `tasks/` directory, stopping at the repository root. Set `TASKMAN_TASKS_DIR` explicitly when multiple agents must share a particular tree across working directories.

```sh
taskman new --type epic --title "Add conversation history" --body-file requirements.md --json
taskman new --type feat --title "Persist conversations" --parent EPIC_ID --body-file story.md --json
taskman new --title "Save messages" --parent STORY_ID --body-file task.md --json
```

Replace the uppercase placeholders with the IDs returned by `new`. New items are drafts. Finalize items once their scope is agreed; finalize their ancestors too before execution.

```sh
taskman finalize TASK_ID
taskman depend REVIEW_ID --on TASK_ID
taskman ready --json
taskman context TASK_ID
taskman move TASK_ID active
```

A story uses type `feat`. Taskman permits other nesting shapes; `task` is always a leaf. Independent tasks can run in parallel. Each agent reads `context` and records its contribution through the CLI. Taskman does not launch coding agents itself.

## Record work and verify

```sh
taskman record TASK_ID --kind decision --actor Codex --body-file decision.md
taskman update TASK_ID --body-file refined-requirements.md
taskman record TASK_ID --kind summary --actor Codex --body-file summary.md
taskman record TASK_ID --kind verification --result pass --actor "Claude Code" --body-file verification.md
taskman close TASK_ID
```

Use `--body-file -` to read stdin. Journal significant choices, their reasons and changes to scope. The completion summary should explain what changed, why, and any limits. Verification records should name the checks, commands, results and source revision or relevant working-tree changes. Distinguish executed tests from source inspection and another agent's report.

Open requirements may be refined to achieve the agreed outcome. Changes to the body, dependencies, type, implementation journal or children invalidate earlier completion evidence. Close requires a current summary, a current passing verification, and completed dependencies and descendants. A recorded pass is the agent's assertion: Taskman does not run tests or prove that assertion.

Closed tasks are immutable. Further changes belong in a new task that references the original ID. Summaries supplement the journal; original entries remain available. `discard` removes only an unreferenced draft leaf.

## View and check

```sh
taskman ui
taskman list --recursive --json
taskman context TASK_ID
taskman validate
```

The UI refreshes as agents change tasks. `taskman validate` checks task-data integrity; acceptance testing is separate. Use `taskman help` for storage rules and `taskman COMMAND --help` for syntax.

The plugin supplies define, design, plan, implement and validate skills, plus optional review, commenting, commit and documentation skills. Skills use the same task records on either platform.

## Develop Taskman

```sh
cd taskman
uv sync --extra dev
uv run pytest
```

Python code is under `taskman/src/taskman/`; plugin sources are under `plugins/tm/`. Add a platform adapter to the installer to support another coding tool without changing task storage. The shell script under `plugins/tm/scripts/` is retained for legacy data; current workflows use the Python CLI.
