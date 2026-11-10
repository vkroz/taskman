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
taskman comment TASK_ID --kind decision --actor Codex --body-file decision.md
taskman update TASK_ID --body-file refined-requirements.md
taskman comment TASK_ID --kind summary --actor Codex --body-file summary.md
taskman comment TASK_ID --kind verification --result pass --actor "Claude Code" --body-file verification.md
taskman close TASK_ID
```

`record` is an alias of `comment`. Every command that changes an item accepts `--actor NAME`; without it, the author is your git `user.name`. Use `--body-file -` to read stdin. Journal significant choices, their reasons and changes to scope. The completion summary should explain what changed, why, and any limits. Verification records should name the checks, commands, results and source revision or relevant working-tree changes. Distinguish executed tests from source inspection and another agent's report.

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

## Storage settings

Taskman reads its storage settings from environment variables and from a `.env` file at the repository root. A variable already set in the environment wins over `.env`. Docker Compose (`env_file`) and Cloud Run (environment variables and mounted secrets) set the same variables. Taskman ignores a `.env` file that git tracks, because a committed file could send your database password to another host. Keep `.env` out of git.

| Variable | Backend | Meaning |
| --- | --- | --- |
| `TASKMAN_BACKEND` | all | `file` (default), `sqlite` or `postgres`. Run `taskman db upgrade` once before you use `sqlite` or `postgres`. |
| `TASKMAN_TASKS_DIR` | file | The shared `tasks/` tree. Default: the nearest `tasks/` directory up to the repository root. |
| `TASKMAN_DATABASE_URL` | sqlite | `sqlite:///absolute/path.db` or `sqlite://~/path.db`. Default: `~/.local/share/taskman/taskman.db`, one file for all repositories. |
| `TASKMAN_DATABASE_URL` | postgres | `postgresql://user@host:port/dbname`. |
| `TASKMAN_DB_SOCKET`, `TASKMAN_DB_NAME`, `TASKMAN_DB_USER` | postgres | Use these instead of a URL to connect through a unix socket, for example Cloud SQL. All three are required. |
| `TASKMAN_DB_PASSWORD` | postgres | The database password. Optional. |
| `TASKMAN_DB_PASSWORD_FILE` | postgres | A file that holds the password, for example a mounted secret. Surrounding whitespace is removed. Set this or `TASKMAN_DB_PASSWORD`, not both. |

Error messages never show a password or a connection URL.

With `sqlite` or `postgres`, items belong to projects and have keys such as `TM-42`. A new top-level item names its project, which Taskman creates on first use: `taskman new --title "Save messages" --project TM`. A child uses the project of its parent. `list`, `ready`, `search` and `browse` show all projects; `--project KEY` limits them to one. These backends add assignees, labels and an activity history of every field change:

```sh
taskman assign TM-42 claude-code
taskman unassign TM-42
taskman label TM-42 --add backend --remove ui
taskman list --assignee claude-code --label backend
taskman activity TM-42
```

`context` includes the activity of the item. Consolidation and `history` are not available with these backends, and a done item keeps its position.

`taskman db upgrade` creates the schema in a new database and upgrades an older one. It does nothing when the schema is current. The postgres backend needs the `postgres` extra: `uv tool install 'taskman[postgres] @ git+https://github.com/vkroz/taskman.git#subdirectory=taskman'`.

SQLite:

```sh
TASKMAN_BACKEND=sqlite
```

Local Postgres:

```sh
TASKMAN_BACKEND=postgres
TASKMAN_DATABASE_URL=postgresql://taskman@localhost:5432/taskman
TASKMAN_DB_PASSWORD=change-me
```

Cloud SQL through its unix socket, for example on Cloud Run:

```sh
TASKMAN_BACKEND=postgres
TASKMAN_DB_SOCKET=/cloudsql/project:region:instance
TASKMAN_DB_NAME=taskman
TASKMAN_DB_USER=taskman_app
TASKMAN_DB_PASSWORD_FILE=/secrets/taskman-db-password
```

## Develop Taskman

```sh
cd taskman
uv sync --extra dev
uv run pytest
```

Python code is under `taskman/src/taskman/`; plugin sources are under `plugins/tm/`. Add a platform adapter to the installer to support another coding tool without changing task storage. The shell script under `plugins/tm/scripts/` is retained for legacy data; current workflows use the Python CLI.

### Workspace UI and history

Run `taskman ui` for the local workspace. The left navigation selects current, backlog, done or consolidated history. Search combines with epic scope and metadata filters; matching descendants retain labeled parent context. Sort orders siblings. Expand or collapse branches, and select a title to open details; Close returns the space to the list.

Create work with **New task**. Open task details offer title/description editing with a recorded reason. **Delete draft** is limited to draft leaves with no incoming dependencies; finalized and completed work cannot be deleted through this command.

Select completed rows and choose **Consolidate completed work** to create a Markdown documentation summary. Selecting a parent includes its descendants. Originals remain immutable and retrievable in Consolidated history, while individual rows leave ordinary lists. The summary contains bounded excerpts and links to full requirements and evidence.

The same operations are available from the CLI:

```sh
taskman browse --where status=active,backlog --sort title
taskman browse --where status=done --epic EPIC_ID --keyword migration
taskman browse --contains journal=verification --exists depends_on
taskman edit TASK_ID --title "Updated title" --body-file requirements.md --reason "Clarify table coverage"
taskman discard DRAFT_ID
taskman consolidate DONE_ID --title "Delivered database changes"
taskman history
taskman browse --include-archived
```

`browse` emits hierarchy-preserving JSON; contextual ancestors have `context: true` and do not match the query themselves. Use `show` or `context` with any retained ID for complete history. See [workspace design](docs/ui-design.md) for filtering and retention semantics.
